<script setup lang="ts">
import { computed } from 'vue'

import type { DownloadTask } from '@/types'
import { canCancel, sourceLabels, stageLabels } from '@/utils/format'

const props = defineProps<{ task: DownloadTask; history?: boolean }>()
const emit = defineEmits<{ cancel: [id: string]; retry: [id: string] }>()

const progressValue = computed(() => props.task.progress ?? (props.task.stage === 'completed' ? 100 : 0))
const stageTone = computed(() => props.task.stage === 'failed' ? 'error' : props.task.stage === 'completed' ? 'success' : props.task.stage === 'cancelled' ? 'muted' : 'active')
</script>

<template>
  <article class="task-item">
    <header class="task-header">
      <div class="task-title">
        <strong>{{ task.track.title }}</strong>
        <span>{{ task.track.artists.join(' / ') }}</span>
      </div>
      <span :class="['stage-badge', stageTone]">{{ stageLabels[task.stage] }}</span>
    </header>
    <div class="task-meta">
      <span>{{ sourceLabels[task.track.source] }}</span>
      <span>{{ task.qualityLabel }}</span>
      <span>{{ task.delivery === 'server' ? '保存到服务器' : '浏览器取回' }}</span>
    </div>
    <div v-if="!history && !['completed', 'failed', 'cancelled'].includes(task.stage)" class="progress-row">
      <progress :value="progressValue" max="100" :aria-label="`${task.track.title} 下载进度`" />
      <span>{{ task.progress === null ? '处理中' : `${task.progress}%` }}</span>
    </div>
    <p v-if="task.error" class="task-error" role="status">{{ task.error }}</p>
    <p v-if="task.warning" class="task-warning" role="status">{{ task.warning }}</p>
    <footer class="task-actions">
      <a v-if="task.browserFileUrl && task.stage === 'completed'" class="link-button" :href="task.browserFileUrl" download>取回文件</a>
      <button v-if="canCancel(task)" class="link-button danger" type="button" @click="emit('cancel', task.id)">取消</button>
      <button v-if="task.stage === 'failed'" class="link-button" type="button" @click="emit('retry', task.id)">重试</button>
    </footer>
  </article>
</template>

<style scoped>
.task-item { padding: 12px; border-bottom: 1px solid var(--line); }
.task-item:last-child { border-bottom: 0; }
.task-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.task-title { display: grid; min-width: 0; gap: 3px; }
.task-title strong, .task-title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-title strong { font-size: 12px; }
.task-title span, .task-meta { color: var(--muted); font-size: 10px; }
.stage-badge { flex: 0 0 auto; padding: 3px 6px; border-radius: 4px; font-size: 9px; font-weight: 700; }
.stage-badge.active { color: var(--accent); background: var(--accent-soft); }
.stage-badge.success { color: var(--success); background: var(--success-soft); }
.stage-badge.error { color: var(--danger); background: var(--danger-soft); }
.stage-badge.muted { color: var(--muted); background: var(--surface-hover); }
.task-meta { display: flex; flex-wrap: wrap; gap: 5px 10px; margin-top: 8px; }
.progress-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.progress-row progress { flex: 1; height: 5px; overflow: hidden; border: 0; border-radius: 4px; accent-color: var(--accent); }
.progress-row span { width: 34px; color: var(--muted); font-size: 9px; text-align: right; }
.task-error { margin: 8px 0 0; padding: 7px; border-radius: 4px; color: var(--danger); background: var(--danger-soft); font-size: 10px; line-height: 1.4; }
.task-warning { margin: 8px 0 0; padding: 7px; border-radius: 4px; color: var(--warning); background: var(--warning-soft); font-size: 10px; line-height: 1.4; }
.task-actions { display: flex; justify-content: flex-end; gap: 9px; margin-top: 8px; }
</style>
