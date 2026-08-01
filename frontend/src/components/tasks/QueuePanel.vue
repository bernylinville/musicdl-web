<script setup lang="ts">
import type { DownloadTask } from '@/types'

import TaskItem from './TaskItem.vue'

defineProps<{ tasks: readonly DownloadTask[] }>()
const emit = defineEmits<{ cancel: [id: string]; retry: [id: string] }>()
</script>

<template>
  <section class="panel" aria-labelledby="queue-heading">
    <header class="panel-header">
      <h2 id="queue-heading">下载队列</h2>
      <span>{{ tasks.length }} 项进行中</span>
    </header>
    <div v-if="tasks.length" class="task-list">
      <TaskItem v-for="task in tasks" :key="task.id" :task="task" @cancel="emit('cancel', $event)" @retry="emit('retry', $event)" />
    </div>
    <div v-else class="panel-empty">
      <span aria-hidden="true">✓</span>
      <strong>当前没有任务</strong>
      <p>选中歌曲并确认精确音质后，任务会在这里显示。</p>
    </div>
  </section>
</template>

<style scoped>
.task-list { max-height: 430px; overflow-y: auto; }
</style>
