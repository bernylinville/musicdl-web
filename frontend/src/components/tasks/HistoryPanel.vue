<script setup lang="ts">
import type { DownloadTask } from '@/types'

import TaskItem from './TaskItem.vue'

defineProps<{ tasks: readonly DownloadTask[] }>()
const emit = defineEmits<{ retry: [id: string]; clear: [] }>()
</script>

<template>
  <section class="panel" aria-labelledby="history-heading">
    <header class="panel-header">
      <h2 id="history-heading">任务历史</h2>
      <button v-if="tasks.length" class="link-button" type="button" @click="emit('clear')">清除终态历史</button>
    </header>
    <div v-if="tasks.length" class="task-list">
      <TaskItem v-for="task in tasks" :key="task.id" :task="task" history @retry="emit('retry', $event)" />
    </div>
    <div v-else class="panel-empty compact">
      <p>暂无完成、失败或已取消任务。</p>
    </div>
  </section>
</template>

<style scoped>
.task-list { max-height: 330px; overflow-y: auto; }
</style>
