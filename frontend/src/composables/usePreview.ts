import { computed, onUnmounted, shallowRef } from 'vue'

import type { MusicApi } from '@/services/api'
import type { Source } from '@/types'
import { trackKey } from '@/utils/format'

export function usePreview(api: MusicApi) {
  const activeKey = shallowRef<string | null>(null)
  const state = shallowRef<'idle' | 'loading' | 'playing' | 'unavailable'>('idle')
  let audio: HTMLAudioElement | null = null

  function stop(): void {
    audio?.pause()
    audio = null
    activeKey.value = null
    state.value = 'idle'
  }

  function toggle(source: Source, trackId: string): void {
    const key = trackKey(source, trackId)
    if (activeKey.value === key) {
      stop()
      return
    }
    stop()
    activeKey.value = key
    state.value = 'loading'
    audio = new Audio(api.previewUrl(source, trackId))
    audio.preload = 'none'
    audio.addEventListener('playing', () => { state.value = 'playing' }, { once: true })
    audio.addEventListener('ended', stop, { once: true })
    audio.addEventListener('error', () => {
      audio = null
      state.value = 'unavailable'
    }, { once: true })
    void audio.play().catch(() => {
      audio = null
      state.value = 'unavailable'
    })
  }

  const isActive = (source: Source, trackId: string): boolean => activeKey.value === trackKey(source, trackId)
  const label = computed(() => state.value === 'playing' ? '停止试听' : state.value === 'loading' ? '加载试听' : '短试听')

  onUnmounted(stop)
  return { activeKey, state, label, isActive, toggle, stop }
}
