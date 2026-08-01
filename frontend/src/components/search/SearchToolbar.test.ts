import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import SearchToolbar from './SearchToolbar.vue'

describe('SearchToolbar', () => {
  it('has labelled search and mutually exclusive source controls', async () => {
    const screen = render(SearchToolbar, {
      props: { query: '', source: 'all', loading: false },
    })

    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索词' }), '晴天 周杰伦')
    await fireEvent.click(screen.getByRole('radio', { name: 'QQ 音乐' }))
    await fireEvent.click(screen.getByRole('button', { name: '搜索' }))

    expect(screen.emitted()['update:query']?.[0]).toEqual(['晴天 周杰伦'])
    expect(screen.emitted()['update:source']?.[0]).toEqual(['qq'])
    expect(screen.emitted().submit).toHaveLength(1)
  })
})
