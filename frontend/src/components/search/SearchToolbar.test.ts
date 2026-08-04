import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import SearchToolbar from './SearchToolbar.vue'

describe('SearchToolbar', () => {
  it('has labelled search and mutually exclusive source controls', async () => {
    const screen = render(SearchToolbar, {
      props: {
        query: '',
        source: 'all',
        loading: false,
        likedEnabled: true,
        likedActive: false,
        playRecordActive: false,
        likedSort: 'default',
      },
    })

    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索词' }), '晴天 周杰伦')
    await fireEvent.click(screen.getByRole('radio', { name: 'QQ 音乐' }))
    await fireEvent.click(screen.getByRole('button', { name: '搜索' }))

    expect(screen.emitted()['update:query']?.[0]).toEqual(['晴天 周杰伦'])
    expect(screen.emitted()['update:source']?.[0]).toEqual(['qq'])
    expect(screen.emitted().submit).toHaveLength(1)
  })

  it('emits liked when the operator opens the red-heart catalog', async () => {
    const screen = render(SearchToolbar, {
      props: {
        query: '',
        source: 'all',
        loading: false,
        likedEnabled: true,
        likedActive: false,
        playRecordActive: false,
        likedSort: 'default',
      },
    })

    await fireEvent.click(screen.getByRole('button', { name: '我喜欢的' }))
    expect(screen.emitted().liked).toHaveLength(1)
  })

  it('exposes play-record entry and liked sort when active', async () => {
    const screen = render(SearchToolbar, {
      props: {
        query: '',
        source: 'netease',
        loading: false,
        likedEnabled: true,
        likedActive: true,
        playRecordActive: false,
        likedSort: 'default',
      },
    })

    await fireEvent.click(screen.getByRole('button', { name: '听歌排行' }))
    expect(screen.emitted().playRecord).toHaveLength(1)
    await fireEvent.change(screen.getByRole('combobox', { name: '我喜欢的排序' }), {
      target: { value: 'liked_at_desc' },
    })
    expect(screen.emitted()['update:likedSort']?.[0]).toEqual(['liked_at_desc'])
  })
})
