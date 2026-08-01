import { computed, onUnmounted, readonly, shallowRef } from 'vue'

import type { MusicApi } from '@/services/api'
import type { QrChallenge, QrLoginState, Source } from '@/types'

interface UseQrLoginOptions {
  onSuccess: () => Promise<void>
}

export function useQrLogin(api: MusicApi, options: UseQrLoginOptions) {
  const source = shallowRef<Source | null>(null)
  const challenge = shallowRef<QrChallenge | null>(null)
  const state = shallowRef<QrLoginState | 'idle' | 'loading' | 'error'>('idle')
  const error = shallowRef<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let runId = 0

  const busy = computed(() => state.value === 'loading')

  function stopTimer(): void {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  function schedulePoll(activeRunId: number): void {
    stopTimer()
    timer = setTimeout(() => { void poll(activeRunId) }, 1000)
  }

  async function poll(activeRunId: number): Promise<void> {
    const activeSource = source.value
    const activeChallenge = challenge.value
    if (activeRunId !== runId || !activeSource || !activeChallenge) return

    try {
      const result = await api.pollQr(activeSource, activeChallenge.challengeId)
      if (activeRunId !== runId) return
      state.value = result.state
      if (result.state === 'success') {
        // Session is already persisted server-side; a refresh/UI failure must not
        // look like a failed login.
        try {
          await options.onSuccess()
        } catch {
          /* ignore post-success UI errors */
        }
        return
      }
      if (result.state === 'expired') return
      schedulePoll(activeRunId)
    } catch (cause) {
      if (activeRunId !== runId) return
      state.value = 'error'
      error.value = cause instanceof Error ? cause.message : '二维码状态查询失败'
    }
  }

  async function start(nextSource: Source): Promise<void> {
    stopTimer()
    const activeRunId = ++runId
    source.value = nextSource
    challenge.value = null
    state.value = 'loading'
    error.value = null

    try {
      const nextChallenge = await api.beginQr(nextSource)
      if (activeRunId !== runId) {
        await api.cancelQr(nextSource, nextChallenge.challengeId).catch(() => undefined)
        return
      }
      challenge.value = nextChallenge
      state.value = nextChallenge.state
      schedulePoll(activeRunId)
    } catch (cause) {
      if (activeRunId !== runId) return
      state.value = 'error'
      error.value = cause instanceof Error ? cause.message : '二维码 API 不可用'
    }
  }

  async function cancel(): Promise<void> {
    stopTimer()
    runId += 1
    const activeSource = source.value
    const activeChallenge = challenge.value
    source.value = null
    challenge.value = null
    state.value = 'idle'
    error.value = null
    if (activeSource && activeChallenge) {
      await api.cancelQr(activeSource, activeChallenge.challengeId).catch(() => undefined)
    }
  }

  onUnmounted(() => { void cancel() })

  return {
    challenge: readonly(challenge),
    state: readonly(state),
    error: readonly(error),
    busy,
    start,
    cancel,
  }
}
