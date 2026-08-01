import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import type { DownloadTask } from '@/types'

import TaskItem from './TaskItem.vue'

const failedTask: DownloadTask = {
  id: 'failed-1',
  track: { source: 'qq', trackId: 'mid', title: '测试歌曲', artists: ['测试歌手'] },
  qualityLabel: 'Hi-Res',
  delivery: 'server',
  stage: 'failed',
  progress: 18,
  error: '所选音质已过期，未自动降级',
  warning: null,
  browserFileUrl: null,
  createdAt: '2026-07-31T08:00:00Z',
}

describe('TaskItem', () => {
  it('shows a precise task error and exposes retry only for failure', async () => {
    const screen = render(TaskItem, { props: { task: failedTask } })

    expect(screen.getByText('所选音质已过期，未自动降级')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(screen.emitted().retry?.[0]).toEqual(['failed-1'])
  })

  it('shows a nonfatal cover warning on a completed download', () => {
    const screen = render(TaskItem, {
      props: {
        task: {
          ...failedTask,
          stage: 'completed',
          progress: 100,
          error: null,
          warning: '音频成功，封面缺失',
        },
      },
    })

    expect(screen.getByText('音频成功，封面缺失')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument()
  })
})
