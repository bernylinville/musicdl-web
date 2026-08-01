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
}>()

const emit = defineEmits<{
  toggle: [track: Track]
  requestQuality: [track: Track, force?: boolean]
  selectQuality: [track: Track, qualityId: string]
  preview: [track: Track]
}>()

const row = useTemplateRef<HTMLTableRowElement>('row')
const coverFailed = shallowRef(false)
let observer: IntersectionObserver | null = null

watch(() => props.track.coverUrl, () => { coverFailed.value = false })

const previewLabel = computed(() => {
  if (!props.previewActive) return '短试听'
  if (props.previewState === 'loading') return '加载中'
  if (props.previewState === 'unavailable') return '不可试听'
  return '停止'
})

const qualityStatusLabel = computed(() => {
  if (props.quality.status === 'loading') return '正在确认…'
  if (props.quality.status === 'idle') return '等待确认'
  return props.quality.message
})

onMounted(() => {
  if (!('IntersectionObserver' in window)) {
    emit('requestQuality', props.track)
    return
  }
  observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) {
      emit('requestQuality', props.track)
      observer?.disconnect()
    }
  }, { rootMargin: '100px' })
  if (row.value) observer.observe(row.value)
})
onUnmounted(() => observer?.disconnect())
</script>

<template>
  <tr ref="row" :class="{ selected }">
    <td class="select-cell">
      <input :id="`select-${track.source}-${track.trackId}`" type="checkbox" :checked="selected" :aria-label="`选择 ${track.title}`" @change="emit('toggle', track)" />
    </td>
    <td class="track-cell">
      <div class="track-main">
        <img v-if="track.coverUrl && !coverFailed" class="cover" :src="track.coverUrl" alt="" loading="lazy" referrerpolicy="no-referrer" @error="coverFailed = true" />
        <span v-else class="cover cover-placeholder" aria-hidden="true">♪</span>
        <div class="track-copy">
          <strong>{{ track.title }}</strong>
          <span>{{ track.artists.join(' / ') }}</span>
        </div>
      </div>
    </td>
    <td class="album-cell" :title="track.album">{{ track.album || '—' }}</td>
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
  </tr>
</template>

<style scoped>
tr { border-bottom: 1px solid var(--line); }
tr:hover, tr.selected { background: var(--row-hover); }
td { height: 54px; padding: 6px 8px; vertical-align: middle; font-size: 12px; }
.select-cell { width: 32px; text-align: center; }
.select-cell input { width: 15px; height: 15px; accent-color: var(--accent); }
.track-cell { min-width: 190px; }
.track-main { display: flex; align-items: center; gap: 9px; }
.cover { flex: 0 0 auto; width: 38px; height: 38px; border-radius: 5px; object-fit: cover; background: var(--surface-hover); }
.cover-placeholder { display: grid; place-items: center; color: var(--muted); }
.track-copy { display: grid; min-width: 0; gap: 3px; }
.track-copy strong, .track-copy span, .album-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.track-copy strong { font-size: 12px; }
.track-copy span, .album-cell, .duration-cell, .muted { color: var(--muted); }
.album-cell { max-width: 150px; }
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
</style>
