import { fireEvent, render } from '@testing-library/vue'
import { defineComponent, h, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { MusicApi } from '@/services/api'
import { createMockApi } from '@/test/mockApi'

import { useQrLogin } from './useQrLogin'

async function flushPromises(): Promise<void> {
  await Promise.resolve()
  await nextTick()
}

function renderHarness(api: MusicApi, onSuccess = vi.fn().mockResolvedValue(undefined)) {
  const Harness = defineComponent({
    setup() {
      const qr = useQrLogin(api, { onSuccess })
      return () => h('div', [
        h('button', { onClick: () => { void qr.start('netease') } }, '开始'),
        h('button', { onClick: () => { void qr.cancel() } }, '关闭'),
        h('output', { 'aria-label': '二维码状态' }, qr.state.value),
        h('output', { 'aria-label': '二维码地址' }, qr.challenge.value?.imageUrl ?? ''),
      ])
    },
  })
  return { screen: render(Harness), onSuccess }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('useQrLogin', () => {
  it('polls recursively every second and stops after success', async () => {
    vi.useFakeTimers()
    const pollQr = vi.fn()
      .mockResolvedValueOnce({ state: 'scanned' })
      .mockResolvedValueOnce({ state: 'success' })
    const { screen, onSuccess } = renderHarness(createMockApi({ pollQr }))

    await fireEvent.click(screen.getByRole('button', { name: '开始' }))
    await flushPromises()
    expect(screen.getByRole('status', { name: '二维码状态' })).toHaveTextContent('waiting')
    expect(screen.getByRole('status', { name: '二维码地址' })).toHaveTextContent('/api/v1/sessions/netease/qr/netease-qr/image')
    expect(screen.getByRole('status', { name: '二维码地址' })).not.toHaveTextContent('data:')

    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(screen.getByRole('status', { name: '二维码状态' })).toHaveTextContent('scanned')
    expect(pollQr).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(screen.getByRole('status', { name: '二维码状态' })).toHaveTextContent('success')
    expect(onSuccess).toHaveBeenCalledOnce()

    await vi.advanceTimersByTimeAsync(5000)
    expect(pollQr).toHaveBeenCalledTimes(2)
  })

  it('stops on expiry and only starts a new challenge after another user action', async () => {
    vi.useFakeTimers()
    const beginQr = vi.fn(createMockApi().beginQr)
    const pollQr = vi.fn().mockResolvedValue({ state: 'expired' })
    const { screen } = renderHarness(createMockApi({ beginQr, pollQr }))

    await fireEvent.click(screen.getByRole('button', { name: '开始' }))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(screen.getByRole('status', { name: '二维码状态' })).toHaveTextContent('expired')

    await vi.advanceTimersByTimeAsync(5000)
    expect(beginQr).toHaveBeenCalledTimes(1)
    expect(pollQr).toHaveBeenCalledTimes(1)

    await fireEvent.click(screen.getByRole('button', { name: '开始' }))
    await flushPromises()
    expect(beginQr).toHaveBeenCalledTimes(2)
  })

  it('cancels the active challenge on close', async () => {
    const cancelQr = vi.fn().mockResolvedValue(undefined)
    const { screen } = renderHarness(createMockApi({ cancelQr }))

    await fireEvent.click(screen.getByRole('button', { name: '开始' }))
    await flushPromises()
    await fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    await flushPromises()

    expect(cancelQr).toHaveBeenCalledWith('netease', 'netease-qr')
    expect(screen.getByRole('status', { name: '二维码状态' })).toHaveTextContent('idle')
  })
})
