import { computed, onMounted, onUnmounted, shallowReadonly, shallowRef } from 'vue'

import { ApiError, type MusicApi } from '@/services/api'
import type {
  Delivery,
  DownloadSelection,
  DownloadTask,
  QualityState,
  SearchGroup,
  SessionStatus,
  Source,
  SourceScope,
  Track,
} from '@/types'
import { trackKey } from '@/utils/format'

const idleQuality = (): QualityState => ({ status: 'idle', options: [], snapshotId: null, message: null })

function qualityError(error: unknown): QualityState {
  if (error instanceof ApiError && (error.code === 'session_required' || error.status === 401)) {
    return { status: 'session_required', options: [], snapshotId: null, message: '需要有效的平台会话' }
  }
  if (error instanceof ApiError && error.code === 'quality_stale') {
    return { status: 'stale', options: [], snapshotId: null, message: '音质选项已过期，请重新获取' }
  }
  return { status: 'unavailable', options: [], snapshotId: null, message: error instanceof Error ? error.message : '音质 API 不可用' }
}

export function useWorkbench(api: MusicApi) {
  const query = shallowRef('')
  const sourceScope = shallowRef<SourceScope>('all')
  const sessions = shallowRef<SessionStatus[]>([])
  const groups = shallowRef<SearchGroup[]>([])
  const activeTasks = shallowRef<DownloadTask[]>([])
  const history = shallowRef<DownloadTask[]>([])
  const qualities = shallowRef<Record<string, QualityState>>({})
  const selectedTracks = shallowRef<Record<string, Track>>({})
  const selectedQualityIds = shallowRef<Record<string, string>>({})
  const delivery = shallowRef<Delivery>('server')
  const apiState = shallowRef<'checking' | 'available' | 'unavailable'>('checking')
  const searchState = shallowRef<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const searchMessage = shallowRef<string | null>(null)
  const submitState = shallowRef<'idle' | 'submitting' | 'success' | 'error'>('idle')
  const submitMessage = shallowRef<string | null>(null)
  const operationMessage = shallowRef<string | null>(null)
  let taskTimer: ReturnType<typeof setInterval> | null = null

  const selectedCount = computed(() => Object.keys(selectedTracks.value).length)
  const downloadSelections = computed<DownloadSelection[]>(() => Object.entries(selectedTracks.value).flatMap(([key, track]) => {
    const quality = qualities.value[key]
    const qualityId = selectedQualityIds.value[key]
    if (!qualityId || quality?.status !== 'ready') return []
    return [{ source: track.source, trackId: track.trackId, qualityId, qualitySnapshotId: quality.snapshotId }]
  }))
  const readyCount = computed(() => downloadSelections.value.length)

  async function refreshShell(): Promise<void> {
    const [sessionResult, activeResult, historyResult] = await Promise.allSettled([
      api.getSessions(),
      api.getTasks('active'),
      api.getTasks('history'),
    ])
    if (sessionResult.status === 'fulfilled') sessions.value = sessionResult.value
    if (activeResult.status === 'fulfilled') activeTasks.value = activeResult.value
    if (historyResult.status === 'fulfilled') history.value = historyResult.value
    apiState.value = [sessionResult, activeResult, historyResult].some((result) => result.status === 'fulfilled') ? 'available' : 'unavailable'
  }

  async function refreshTasks(): Promise<void> {
    try {
      const [active, completed] = await Promise.all([api.getTasks('active'), api.getTasks('history')])
      activeTasks.value = active
      history.value = completed
      apiState.value = 'available'
    } catch {
      if (apiState.value === 'checking') apiState.value = 'unavailable'
    }
  }

  async function refreshSessions(): Promise<void> {
    sessions.value = await api.getSessions()
    apiState.value = 'available'
  }

  async function search(page = 1): Promise<void> {
    const normalized = query.value.trim()
    if (!normalized) {
      searchState.value = 'error'
      searchMessage.value = '请输入歌名、歌手名或两者组合'
      return
    }
    searchState.value = 'loading'
    searchMessage.value = null
    try {
      const response = await api.search(normalized, sourceScope.value, page)
      groups.value = response.groups
      selectedTracks.value = {}
      selectedQualityIds.value = {}
      qualities.value = {}
      searchState.value = 'ready'
      apiState.value = 'available'
    } catch (error) {
      searchState.value = 'error'
      searchMessage.value = error instanceof Error ? error.message : '搜索 API 不可用'
      if (error instanceof ApiError && error.code === 'api_unavailable') apiState.value = 'unavailable'
    }
  }

  async function loadMore(source: Source): Promise<void> {
    const current = groups.value.find((group) => group.source === source)
    if (!current?.hasMore) return
    try {
      const response = await api.search(query.value.trim(), source, current.page + 1)
      const next = response.groups.find((group) => group.source === source)
      if (!next) return
      groups.value = groups.value.map((group) => group.source === source
        ? { ...next, tracks: [...group.tracks, ...next.tracks] }
        : group)
    } catch (error) {
      groups.value = groups.value.map((group) => group.source === source
        ? { ...group, status: 'unavailable', message: error instanceof Error ? error.message : '加载更多失败' }
        : group)
    }
  }

  async function requestQuality(track: Track, force = false): Promise<void> {
    const key = trackKey(track.source, track.trackId)
    const current = qualities.value[key]
    if (!force && current && current.status !== 'idle' && current.status !== 'stale') return
    const session = sessions.value.find((item) => item.source === track.source)
    if (!force && session && ['anonymous', 'expired'].includes(session.state)) {
      qualities.value = {
        ...qualities.value,
        [key]: { status: 'session_required', options: [], snapshotId: null, message: '需要有效的平台会话' },
      }
      return
    }
    qualities.value = { ...qualities.value, [key]: { status: 'loading', options: [], snapshotId: null, message: null } }
    try {
      const snapshot = await api.getQualities(track.source, track.trackId)
      if (Date.parse(snapshot.expiresAt) <= Date.now()) {
        qualities.value = { ...qualities.value, [key]: { status: 'stale', options: [], snapshotId: null, message: '音质选项已过期，请重新获取' } }
        return
      }
      qualities.value = {
        ...qualities.value,
        [key]: snapshot.options.length
          ? { status: 'ready', options: snapshot.options, snapshotId: snapshot.snapshotId, expiresAt: snapshot.expiresAt, message: null }
          : { status: 'unavailable', options: [], snapshotId: null, message: '当前会话没有可获取音质' },
      }
      if (snapshot.options.length === 1) {
        selectedQualityIds.value = { ...selectedQualityIds.value, [key]: snapshot.options[0]!.id }
      }
    } catch (error) {
      qualities.value = { ...qualities.value, [key]: qualityError(error) }
    }
  }

  function toggleTrack(track: Track): void {
    const key = trackKey(track.source, track.trackId)
    if (selectedTracks.value[key]) {
      const next = { ...selectedTracks.value }
      delete next[key]
      selectedTracks.value = next
      return
    }
    selectedTracks.value = { ...selectedTracks.value, [key]: track }
    void requestQuality(track)
  }

  function setQuality(track: Track, qualityId: string): void {
    selectedQualityIds.value = { ...selectedQualityIds.value, [trackKey(track.source, track.trackId)]: qualityId }
  }

  function clearSelection(): void {
    selectedTracks.value = {}
    selectedQualityIds.value = {}
  }

  async function submit(): Promise<void> {
    const expiredKeys = Object.keys(selectedTracks.value).filter((key) => {
      const quality = qualities.value[key]
      return quality?.status === 'ready' && Date.parse(quality.expiresAt) <= Date.now()
    })
    if (expiredKeys.length) {
      const next = { ...qualities.value }
      for (const key of expiredKeys) {
        next[key] = { status: 'stale', options: [], snapshotId: null, message: '音质选项已过期，请重新获取' }
      }
      qualities.value = next
      submitState.value = 'error'
      submitMessage.value = '部分音质选项已过期，请重新获取后再提交'
      return
    }
    if (readyCount.value !== selectedCount.value || !readyCount.value) {
      submitState.value = 'error'
      submitMessage.value = '请为每首已选歌曲确认仍有效的精确音质'
      return
    }
    submitState.value = 'submitting'
    submitMessage.value = null
    try {
      const created = await api.createBatch({ delivery: delivery.value, items: downloadSelections.value })
      activeTasks.value = [...created, ...activeTasks.value]
      clearSelection()
      submitState.value = 'success'
      submitMessage.value = `已创建 ${created.length} 个独立下载任务`
    } catch (error) {
      submitState.value = 'error'
      submitMessage.value = error instanceof Error ? error.message : '创建下载批次失败'
    }
  }

  async function cancelTask(id: string): Promise<void> {
    operationMessage.value = null
    try {
      const updated = await api.cancelTask(id)
      activeTasks.value = activeTasks.value.map((task) => task.id === id ? updated : task)
      await refreshTasks()
    } catch (error) {
      operationMessage.value = error instanceof Error ? error.message : '取消任务失败'
    }
  }

  async function retryTask(id: string): Promise<void> {
    operationMessage.value = null
    try {
      const updated = await api.retryTask(id)
      activeTasks.value = [updated, ...activeTasks.value.filter((task) => task.id !== id)]
      history.value = history.value.filter((task) => task.id !== id)
    } catch (error) {
      operationMessage.value = error instanceof Error ? error.message : '重试任务失败'
    }
  }

  async function clearHistory(): Promise<void> {
    operationMessage.value = null
    try {
      await api.clearHistory()
      history.value = []
    } catch (error) {
      operationMessage.value = error instanceof Error ? error.message : '清除任务历史失败'
    }
  }

  async function importSession(source: Source, value: string): Promise<void> {
    const updated = await api.importSession(source, { value })
    sessions.value = sessions.value.map((session) => session.source === source ? updated : session)
  }

  async function clearSession(source: Source): Promise<void> {
    operationMessage.value = null
    try {
      await api.clearSession(source)
      await refreshShell()
    } catch (error) {
      operationMessage.value = error instanceof Error ? error.message : '退出平台会话失败'
    }
  }

  onMounted(() => {
    void refreshShell()
    taskTimer = setInterval(() => { void refreshTasks() }, 3000)
  })
  onUnmounted(() => { if (taskTimer) clearInterval(taskTimer) })

  return {
    query,
    sourceScope,
    sessions: shallowReadonly(sessions),
    groups: shallowReadonly(groups),
    activeTasks: shallowReadonly(activeTasks),
    history: shallowReadonly(history),
    qualities: shallowReadonly(qualities),
    selectedTracks: shallowReadonly(selectedTracks),
    selectedQualityIds: shallowReadonly(selectedQualityIds),
    delivery,
    apiState: shallowReadonly(apiState),
    searchState: shallowReadonly(searchState),
    searchMessage: shallowReadonly(searchMessage),
    submitState: shallowReadonly(submitState),
    submitMessage: shallowReadonly(submitMessage),
    operationMessage: shallowReadonly(operationMessage),
    selectedCount,
    readyCount,
    search,
    loadMore,
    requestQuality,
    toggleTrack,
    setQuality,
    clearSelection,
    submit,
    cancelTask,
    retryTask,
    clearHistory,
    refreshSessions,
    importSession,
    clearSession,
  }
}

export { idleQuality }
