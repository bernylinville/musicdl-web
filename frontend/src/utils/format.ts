import type { DownloadTask, Source, TaskStage } from '@/types'

export const sourceLabels: Record<Source, string> = {
  netease: '网易云音乐',
  qq: 'QQ 音乐',
}

export const stageLabels: Record<TaskStage, string> = {
  queued: '排队',
  resolving: '解析音质',
  downloading: '下载',
  tagging: '写标签',
  publishing: '入库',
  completed: '完成',
  failed: '失败',
  cancelled: '已取消',
}

export function trackKey(source: Source, trackId: string): string {
  return `${source}:${trackId}`
}

export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.floor(durationMs / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  return `${minutes}:${String(totalSeconds % 60).padStart(2, '0')}`
}

export function canCancel(task: DownloadTask): boolean {
  return !['completed', 'failed', 'cancelled'].includes(task.stage)
}
