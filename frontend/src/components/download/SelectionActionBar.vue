<script setup lang="ts">
import type { Delivery } from '@/types'

defineProps<{
  selectedCount: number
  readyCount: number
  submitting: boolean
  message: string | null
  messageError: boolean
}>()

const delivery = defineModel<Delivery>('delivery', { required: true })
const emit = defineEmits<{ clear: []; submit: [] }>()
</script>

<template>
  <section class="action-bar" aria-label="已选歌曲交付设置">
    <div class="selection-count">
      <strong>{{ selectedCount }}</strong>
      <span>首已选</span>
      <button class="link-button" type="button" @click="emit('clear')">清除选择</button>
    </div>
    <fieldset class="delivery-switch">
      <legend>交付方式</legend>
      <label>
        <input v-model="delivery" type="radio" value="server" />
        <span><strong>保存到服务器</strong><small>默认，发布到 Navidrome 音乐库</small></span>
      </label>
      <label>
        <input v-model="delivery" type="radio" value="browser" />
        <span><strong>浏览器取回</strong><small>逐文件临时交付，不加入音乐库</small></span>
      </label>
    </fieldset>
    <div class="submit-area">
      <span v-if="message" :class="['submit-message', { error: messageError }]" role="status">{{ message }}</span>
      <span v-else-if="readyCount !== selectedCount" class="submit-message error">{{ selectedCount - readyCount }} 首音质确认中…</span>
      <span v-else-if="selectedCount" class="submit-message">默认最高可用音质，可在列表中改</span>
      <button class="button button-primary" type="button" :disabled="submitting || !selectedCount || readyCount !== selectedCount" @click="emit('submit')">
        {{ submitting ? '正在创建任务…' : readyCount === selectedCount ? `下载 ${selectedCount} 首` : `等待音质 ${readyCount}/${selectedCount}` }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.action-bar { position: sticky; z-index: 5; bottom: 12px; display: flex; align-items: center; gap: 22px; margin-top: 12px; padding: 10px 12px; border: 1px solid var(--line-strong); border-radius: 9px; background: color-mix(in srgb, var(--surface) 94%, transparent); box-shadow: var(--shadow-lg); backdrop-filter: blur(12px); }
.selection-count { display: flex; align-items: baseline; gap: 5px; min-width: 120px; }
.selection-count strong { color: var(--accent); font-size: 20px; }
.selection-count span { color: var(--muted); font-size: 11px; }
.selection-count .link-button { margin-left: 5px; }
.delivery-switch { display: flex; gap: 6px; margin: 0; padding: 0; border: 0; }
.delivery-switch legend { position: absolute; overflow: hidden; width: 1px; height: 1px; clip: rect(0, 0, 0, 0); }
.delivery-switch label { display: flex; align-items: center; gap: 7px; min-width: 168px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 6px; cursor: pointer; }
.delivery-switch label:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
.delivery-switch input { accent-color: var(--accent); }
.delivery-switch span { display: grid; gap: 2px; }
.delivery-switch strong { font-size: 11px; }
.delivery-switch small { color: var(--muted); font-size: 9px; }
.submit-area { display: flex; align-items: center; justify-content: flex-end; gap: 9px; flex: 1; }
.submit-message { max-width: 210px; color: var(--success); font-size: 10px; text-align: right; }
.submit-message.error { color: var(--danger); }
</style>
