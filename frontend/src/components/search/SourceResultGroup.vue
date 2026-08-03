<script setup lang="ts">
import type { QualityState, SearchGroup, Track } from '@/types'
import { sourceLabels, trackKey } from '@/utils/format'

import TrackRow from './TrackRow.vue'

defineProps<{
  group: SearchGroup
  selectedTracks: Readonly<Record<string, Track>>
  qualities: Readonly<Record<string, QualityState>>
  selectedQualityIds: Readonly<Record<string, string>>
  previewKey: string | null
  previewState: 'idle' | 'loading' | 'playing' | 'unavailable'
}>()

const emit = defineEmits<{
  toggle: [track: Track]
  requestQuality: [track: Track, force?: boolean]
  selectQuality: [track: Track, qualityId: string]
  preview: [track: Track]
  loadMore: []
  openArtist: [source: Track['source'], artistId: string, title: string]
  openAlbum: [source: Track['source'], albumId: string, title: string]
  setLiked: [track: Track, liked: boolean]
}>()

const emptyQuality = (): QualityState => ({ status: 'idle', options: [], snapshotId: null, message: null })
</script>

<template>
  <section class="result-group" :aria-labelledby="`${group.source}-heading`">
    <header class="group-header">
      <div>
        <span :class="['source-mark', `source-${group.source}`]" aria-hidden="true" />
        <h2 :id="`${group.source}-heading`">{{ sourceLabels[group.source] }}</h2>
        <span class="result-count">{{ group.tracks.length }} 条结果</span>
      </div>
      <span class="page-label">第 {{ group.page }} 页</span>
    </header>
    <div v-if="group.status === 'unavailable'" class="group-notice" role="status">
      <strong>该来源当前不可用</strong>
      <span>{{ group.message ?? '请稍后重试，另一来源结果不受影响。' }}</span>
    </div>
    <div v-if="group.tracks.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th class="select-heading"><span class="sr-only">选择</span></th>
            <th class="like-heading" title="网易云红心"><span class="sr-only">喜欢</span>♥</th>
            <th>歌曲</th>
            <th>专辑</th>
            <th>时长</th>
            <th>音乐库</th>
            <th>当前可选音质</th>
            <th>确认</th>
          </tr>
        </thead>
        <tbody>
          <TrackRow
            v-for="track in group.tracks"
            :key="trackKey(track.source, track.trackId)"
            :track="track"
            :selected="Boolean(selectedTracks[trackKey(track.source, track.trackId)])"
            :quality="qualities[trackKey(track.source, track.trackId)] ?? emptyQuality()"
            :selected-quality-id="selectedQualityIds[trackKey(track.source, track.trackId)] ?? null"
            :preview-active="previewKey === trackKey(track.source, track.trackId)"
            :preview-state="previewState"
            @toggle="emit('toggle', $event)"
            @request-quality="(item, force) => emit('requestQuality', item, force)"
            @select-quality="(item, qualityId) => emit('selectQuality', item, qualityId)"
            @preview="emit('preview', $event)"
            @open-artist="(source, artistId, title) => emit('openArtist', source, artistId, title)"
            @open-album="(source, albumId, title) => emit('openAlbum', source, albumId, title)"
            @set-liked="(track, liked) => emit('setLiked', track, liked)"
          />
        </tbody>
      </table>
    </div>
    <div v-else-if="group.status === 'ready'" class="empty-state">此来源没有匹配的平台歌曲。</div>
    <footer v-if="group.hasMore" class="group-footer">
      <button class="button button-quiet" type="button" @click="emit('loadMore')">加载更多 {{ sourceLabels[group.source] }}结果</button>
    </footer>
  </section>
</template>

<style scoped>
.result-group { overflow: hidden; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); box-shadow: var(--shadow-sm); }
.group-header { display: flex; align-items: center; justify-content: space-between; min-height: 44px; padding: 0 12px; border-bottom: 1px solid var(--line); }
.group-header > div { display: flex; align-items: center; gap: 8px; }
.group-header h2 { margin: 0; font-size: 13px; }
.source-mark { width: 7px; height: 18px; border-radius: 3px; background: var(--muted); }
.source-netease { background: #d83b32; }
.source-qq { background: #e5a829; }
.result-count, .page-label { color: var(--muted); font-size: 11px; }
.table-scroll { overflow-x: auto; }
table { width: 100%; min-width: 880px; border-collapse: collapse; table-layout: fixed; }
thead { background: var(--canvas-subtle); }
th { height: 31px; padding: 0 8px; color: var(--muted); font-size: 10px; font-weight: 600; text-align: left; text-transform: uppercase; letter-spacing: .04em; }
.select-heading { width: 32px; }
.like-heading { width: 36px; text-align: center; color: #e11d48; text-transform: none; letter-spacing: 0; font-size: 12px; }
.group-notice { display: grid; gap: 4px; padding: 14px 16px; color: var(--danger); background: var(--danger-soft); font-size: 12px; }
.group-notice span { opacity: .85; }
.empty-state { padding: 28px 16px; color: var(--muted); font-size: 12px; text-align: center; }
.group-footer { display: flex; justify-content: center; padding: 9px; border-top: 1px solid var(--line); }
</style>
