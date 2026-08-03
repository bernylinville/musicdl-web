<script setup lang="ts">
import type { SourceScope } from '@/types'

defineProps<{
  loading: boolean
  likedEnabled: boolean
  likedActive: boolean
}>()
const query = defineModel<string>('query', { required: true })
const source = defineModel<SourceScope>('source', { required: true })
const emit = defineEmits<{ submit: []; liked: [] }>()
</script>

<template>
  <form class="search-toolbar" role="search" @submit.prevent="emit('submit')">
    <div class="search-input-wrap">
      <span aria-hidden="true">⌕</span>
      <label class="sr-only" for="music-search">搜索词</label>
      <input id="music-search" v-model="query" type="search" autocomplete="off" placeholder="搜索歌名、歌手名，或两者组合" />
    </div>
    <fieldset class="scope-switch">
      <legend class="sr-only">来源范围</legend>
      <label v-for="option in ([['all', '全部'], ['netease', '网易云'], ['qq', 'QQ 音乐']] as const)" :key="option[0]">
        <input v-model="source" type="radio" name="source" :value="option[0]" />
        <span>{{ option[1] }}</span>
      </label>
    </fieldset>
    <button class="button button-primary search-button" type="submit" :disabled="loading">{{ loading ? '搜索中…' : '搜索' }}</button>
    <button
      class="button button-quiet liked-button"
      type="button"
      :disabled="loading || !likedEnabled"
      :aria-pressed="likedActive"
      :title="likedEnabled ? '查看当前网易云账号红心歌曲' : '需要已登录的网易云会话'"
      @click="emit('liked')"
    >
      {{ likedActive && loading ? '加载中…' : '我喜欢的' }}
    </button>
  </form>
</template>

<style scoped>
.search-toolbar { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); box-shadow: var(--shadow-sm); flex-wrap: wrap; }
.search-input-wrap { display: flex; align-items: center; gap: 9px; flex: 1; min-width: 250px; }
.search-input-wrap > span { color: var(--muted); font-size: 20px; transform: rotate(-20deg); }
.search-input-wrap input { width: 100%; height: 36px; padding: 0; border: 0; outline: 0; background: transparent; font-size: 14px; }
.scope-switch { display: flex; margin: 0; padding: 3px; border: 1px solid var(--line); border-radius: 7px; background: var(--canvas); }
.scope-switch label { cursor: pointer; }
.scope-switch input { position: absolute; opacity: 0; pointer-events: none; }
.scope-switch span { display: block; padding: 6px 10px; border-radius: 5px; color: var(--muted); font-size: 12px; }
.scope-switch input:checked + span { color: var(--text); background: var(--surface); box-shadow: 0 1px 2px rgb(0 0 0 / .08); font-weight: 700; }
.scope-switch input:focus-visible + span { outline: 2px solid var(--focus); outline-offset: 2px; }
.search-button { min-width: 76px; }
.liked-button { min-width: 88px; }
.liked-button[aria-pressed='true'] { color: var(--text); font-weight: 700; border-color: var(--line-strong, var(--line)); }
</style>
