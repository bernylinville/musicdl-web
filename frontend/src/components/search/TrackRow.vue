<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef, useTemplateRef, watch } from 'vue'

import type { QualityState, Track } from '@/types'
import { formatDuration } from '@/utils/format'

const props = defineProps<{
  track: Track
  selected: boolean
  quality: QualityState
  selectedQualityId: string | null
  previewActive: boolean
  previewState: 'idle' | 'loading' | 'playing' | 'unavailable'
  downloading: boolean
}>()

const emit = defineEmits<{
  toggle: [track: Track]
  requestQuality: [track: Track, force?: boolean]
  selectQuality: [track: Track, qualityId: string]
  preview: [track: Track]
  download: [track: Track]
  openArtist: [source: Track['source'], artistId: string, title: string]
  openAlbum: [source: Track['source'], albumId: string, title: string]
  setLiked: [track: Track, liked: boolean]
}>()

const likeBusy = shallowRef(false)

const likeLabel = computed(() => {
  if (props.track.source !== 'netease') return '仅网易云可红心'
  if (props.track.liked === true) return '已喜欢 · 点击取消'
  if (props.track.liked === false) return '未喜欢 · 点击红心'
  return '登录网易云后可红心'
})

async function toggleLike(event: Event): Promise<void> {
  event.preventDefault()
  event.stopPropagation()
  if (props.track.source !== 'netease' || props.track.liked == null || likeBusy.value) return
  likeBusy.value = true
  try {
    emit('setLiked', props.track, !props.track.liked)
  } finally {
    // Parent handles async; unlock after microtask so rapid double-click is damped.
    queueMicrotask(() => { likeBusy.value = false })
  }
}

function artistIdAt(index: number): string | null {
  const id = props.track.artistIds?.[index]
  return id && id.length > 0 ? id : null
}

function openArtist(index: number, name: string, event: Event): void {
  event.preventDefault()
  event.stopPropagation()
  const id = artistIdAt(index)
  if (!id || props.track.source !== 'netease') return
  emit('openArtist', props.track.source, id, name)
}

function openAlbum(event: Event): void {
  event.preventDefault()
  event.stopPropagation()
  const id = props.track.albumId
  if (!id || props.track.source !== 'netease') return
  emit('openAlbum', props.track.source, id, props.track.album)
}

const row = useTemplateRef<HTMLTableRowElement>('row')
const coverFailed = shallowRef(false)
let observer: IntersectionObserver | null = null
let observedTrackKey = ''

function trackIdentity(): string {
  return `${props.track.source}:${props.track.trackId}`
}

function requestIfNeeded(): void {
  if (props.quality.status === 'idle' || props.quality.status === 'session_required') {
    emit('requestQuality', props.track)
  }
}

watch(() => props.track.coverUrl, () => { coverFailed.value = false })

// New search clears qualities to idle but Vue may reuse TrackRow by trackId.
// Re-request whenever identity or idle status appears again.
watch(
  () => [trackIdentity(), props.quality.status] as const,
  () => { requestIfNeeded() },
  { immediate: true },
)

watch(
  () => props.selected,
  (selected) => {
    if (selected) requestIfNeeded()
  },
)

const previewLabel = computed(() => {
  if (!props.previewActive) return '短试听'
  if (props.previewState === 'loading') return '加载中'
  if (props.previewState === 'unavailable') return '不可试听'
  return '停止'
})

const qualityStatusLabel = computed(() => {
  if (props.quality.status === 'loading') return '正在确认…'
  if (props.quality.status === 'idle') return '等待确认'
  if (props.quality.status === 'ready') return null
  return props.quality.message
})

const preferredQualityLabel = computed(() => {
  if (props.quality.status !== 'ready') return null
  const selected = props.selectedQualityId
    ? props.quality.options.find((option) => option.id === props.selectedQualityId)
    : null
  if (selected) return selected.label
  return props.quality.options[props.quality.options.length - 1]?.label ?? null
})

const downloadLabel = computed(() => {
  if (props.downloading) return '加入中…'
  if (props.quality.status === 'loading') return '确认中…'
  if (props.quality.status === 'session_required') return '需登录'
  if (props.quality.status === 'unavailable' || props.quality.status === 'stale') return '不可下'
  return '下载'
})

const downloadTitle = computed(() => {
  if (props.downloading) return '正在加入下载队列'
  if (props.quality.status === 'session_required') return '需要有效的平台会话'
  if (props.quality.status === 'unavailable') return props.quality.message ?? '当前不可下载'
  if (props.quality.status === 'stale') return '音质已过期，点击重试确认后再下载'
  if (preferredQualityLabel.value) return `下载（${preferredQualityLabel.value}）· 使用当前交付方式`
  return '下载：自动确认最高可用音质'
})

const downloadDisabled = computed(() => {
  if (props.downloading) return true
  if (props.quality.status === 'session_required') return true
  if (props.quality.status === 'unavailable') return true
  return false
})

const catalogMeta = computed(() => {
  if (typeof props.track.playCount === 'number') return `播放 ${props.track.playCount} 次`
  if (props.track.likedAt) {
    const ms = Date.parse(props.track.likedAt)
    if (!Number.isNaN(ms)) {
      const d = new Date(ms)
      const y = d.getFullYear()
      const m = String(d.getMonth() + 1).padStart(2, '0')
      const day = String(d.getDate()).padStart(2, '0')
      return `红心于 ${y}-${m}-${day}`
    }
  }
  return null
})

onMounted(() => {
  requestIfNeeded()
  if (!('IntersectionObserver' in window)) return
  observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) {
      requestIfNeeded()
    }
  }, { rootMargin: '100px' })
  if (row.value) {
    observedTrackKey = trackIdentity()
    observer.observe(row.value)
  }
})

watch(
  () => trackIdentity(),
  (next) => {
    if (!observer || !row.value || next === observedTrackKey) return
    observer.unobserve(row.value)
    observedTrackKey = next
    observer.observe(row.value)
    requestIfNeeded()
  },
)

onUnmounted(() => observer?.disconnect())
</script>

<template>
  <tr ref="row" :class="{ selected }">
    <td class="select-cell">
      <input :id="`select-${track.source}-${track.trackId}`" type="checkbox" :checked="selected" :aria-label="`选择 ${track.title}`" @change="emit('toggle', track)" />
    </td>
    <td class="like-cell">
      <button
        class="like-button"
        type="button"
        :class="{
          'like-on': track.liked === true,
          'like-off': track.liked === false,
          'like-unknown': track.liked == null || track.source !== 'netease',
        }"
        :disabled="track.source !== 'netease' || track.liked == null || likeBusy"
        :aria-pressed="track.liked === true"
        :aria-label="likeLabel"
        :title="likeLabel"
        @click="toggleLike"
      >
        <span aria-hidden="true">{{ track.liked === true ? '♥' : '♡' }}</span>
      </button>
    </td>
    <td class="track-cell">
      <div class="track-main">
        <img v-if="track.coverUrl && !coverFailed" class="cover" :src="track.coverUrl" alt="" loading="lazy" referrerpolicy="no-referrer" @error="coverFailed = true" />
        <span v-else class="cover cover-placeholder" aria-hidden="true">♪</span>
        <div class="track-copy">
          <strong>{{ track.title }}</strong>
          <span class="artists-line">
            <template v-for="(name, index) in track.artists" :key="`${track.trackId}-a-${index}`">
              <button
                v-if="artistIdAt(index)"
                class="meta-link"
                type="button"
                :title="`查看 ${name} 的歌曲`"
                @click="openArtist(index, name, $event)"
              >{{ name }}</button>
              <span v-else>{{ name }}</span>
              <span v-if="index < track.artists.length - 1" class="artist-sep"> / </span>
            </template>
            <span v-if="!track.artists.length">—</span>
          </span>
          <span v-if="catalogMeta" class="catalog-meta">{{ catalogMeta }}</span>
        </div>
      </div>
    </td>
    <td class="album-cell" :title="track.album">
      <button
        v-if="track.albumId && track.source === 'netease' && track.album"
        class="meta-link album-link"
        type="button"
        :title="`查看专辑 ${track.album}`"
        @click="openAlbum($event)"
      >{{ track.album }}</button>
      <span v-else>{{ track.album || '—' }}</span>
    </td>
    <td class="duration-cell">{{ formatDuration(track.durationMs) }}</td>
    <td class="library-cell">
      <span v-if="track.library" :class="['library-badge', `library-${track.library.state}`]">
        {{ track.library.state === 'missing' ? '文件缺失' : `已入库 · ${track.library.qualityLabel}` }}
      </span>
      <span v-else class="muted">未入库</span>
    </td>
    <td class="quality-cell">
      <select v-if="quality.status === 'ready'" :value="selectedQualityId ?? ''" :aria-label="`${track.title} 的精确音质`" @change="emit('selectQuality', track, ($event.target as HTMLSelectElement).value)">
        <option disabled value="">选择音质</option>
        <option v-for="option in quality.options" :key="option.id" :value="option.id">
          {{ option.label }}{{ option.codec ? ` · ${option.codec}` : '' }}{{ option.upgrade ? ' · 可升级' : '' }}
        </option>
      </select>
      <div v-else class="quality-state">
        <span :class="{ error: ['stale', 'session_required', 'unavailable'].includes(quality.status) }">{{ qualityStatusLabel }}</span>
        <button v-if="['stale', 'unavailable'].includes(quality.status)" class="link-button" type="button" @click="emit('requestQuality', track, true)">重试</button>
      </div>
    </td>
    <td class="preview-cell">
      <button class="button button-quiet preview-button" type="button" :disabled="previewActive && previewState === 'unavailable'" @click="emit('preview', track)">
        <span aria-hidden="true">{{ previewActive && previewState === 'playing' ? '■' : '▶' }}</span>
        {{ previewLabel }}
      </button>
    </td>
    <td class="download-cell">
      <button
        class="button button-primary download-button"
        type="button"
        :disabled="downloadDisabled"
        :title="downloadTitle"
        :aria-label="`${track.title} · ${downloadTitle}`"
        @click="emit('download', track)"
      >
        {{ downloadLabel }}
      </button>
    </td>
  </tr>
</template>

<style scoped>
tr { border-bottom: 1px solid var(--line); }
tr:hover, tr.selected { background: var(--row-hover); }
td { height: 54px; padding: 6px 8px; vertical-align: middle; font-size: 12px; }
.select-cell { width: 32px; text-align: center; }
.select-cell input { width: 15px; height: 15px; accent-color: var(--accent); }
.like-cell { width: 36px; text-align: center; }
.like-button {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
}
.like-button:hover:not(:disabled) { background: var(--row-hover); }
.like-button:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.like-button:disabled { cursor: not-allowed; opacity: 0.55; }
.like-button.like-on { color: #e11d48; }
.like-button.like-off { color: var(--muted); }
.like-button.like-unknown { color: var(--muted); opacity: 0.45; }
.track-cell { min-width: 190px; }
.track-main { display: flex; align-items: center; gap: 9px; }
.cover { flex: 0 0 auto; width: 38px; height: 38px; border-radius: 5px; object-fit: cover; background: var(--surface-hover); }
.cover-placeholder { display: grid; place-items: center; color: var(--muted); }
.track-copy { display: grid; min-width: 0; gap: 3px; }
.catalog-meta { color: var(--muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.track-copy strong, .artists-line, .album-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.track-copy strong { font-size: 12px; }
.artists-line, .album-cell, .duration-cell, .muted { color: var(--muted); }
.album-cell { max-width: 150px; }
.meta-link {
  display: inline;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-decoration: none;
}
.meta-link:hover { color: var(--accent, #2563eb); text-decoration: underline; }
.meta-link:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; border-radius: 2px; }
.album-link { max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.duration-cell { width: 46px; font-variant-numeric: tabular-nums; }
.library-cell { min-width: 110px; }
.library-badge { padding: 3px 6px; border-radius: 4px; color: var(--success); background: var(--success-soft); white-space: nowrap; }
.library-missing { color: var(--danger); background: var(--danger-soft); }
.quality-cell { min-width: 170px; }
.quality-cell select { width: 100%; height: 30px; font-size: 11px; }
.quality-state { display: flex; align-items: center; gap: 7px; color: var(--muted); }
.quality-state .error { color: var(--danger); }
.preview-cell { width: 92px; }
.preview-button { width: 100%; padding-inline: 7px; }
.download-cell { width: 72px; }
.download-button { width: 100%; min-width: 0; padding-inline: 6px; font-size: 11px; }
</style>
