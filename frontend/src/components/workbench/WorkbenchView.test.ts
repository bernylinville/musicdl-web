import { fireEvent, render, waitFor } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import { apiKey, ApiError } from '@/services/api'
import { createMockApi } from '@/test/mockApi'

import WorkbenchView from './WorkbenchView.vue'

function renderWorkbench(api = createMockApi()) {
  return render(WorkbenchView, { global: { provide: { [apiKey as symbol]: api } } })
}

describe('WorkbenchView', () => {
  it('renders the desktop workbench and keeps unavailable source explicit', async () => {
    const screen = renderWorkbench()
    await waitFor(() => expect(screen.getByText('会话有效')).toBeInTheDocument())

    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索词' }), '晴天')
    await fireEvent.click(screen.getByRole('button', { name: '搜索' }))

    expect(await screen.findByRole('heading', { name: '网易云音乐' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'QQ 音乐' })).toBeInTheDocument()
    expect(screen.getByText('QQ 搜索接口当前不可用')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '短试听' })).toBeInTheDocument()
    const cover = screen.container.querySelector('img[alt=""]')
    expect(cover).not.toBeNull()
    expect(cover).toHaveAttribute('src', '/api/v1/covers/netease/186016')
    await fireEvent.error(cover!)
    expect(screen.getByText('♪')).toBeInTheDocument()
  })

  it('selects across a result, confirms quality, and submits one exact task', async () => {
    const createBatch = vi.fn().mockResolvedValue([])
    const screen = renderWorkbench(createMockApi({ createBatch }))
    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索词' }), '晴天')
    await fireEvent.click(screen.getByRole('button', { name: '搜索' }))
    await fireEvent.click(await screen.findByRole('checkbox', { name: '选择 晴天' }))

    await waitFor(() => expect(screen.getByRole('combobox', { name: '晴天 的精确音质' })).toHaveValue('lossless'))
    expect(screen.getByRole('radio', { name: /保存到服务器/ })).toBeChecked()
    await fireEvent.click(screen.getByRole('radio', { name: /浏览器取回/ }))
    expect(screen.getByRole('radio', { name: /保存到服务器/ })).not.toBeChecked()
    await fireEvent.click(screen.getByRole('button', { name: '创建 1 个任务' }))

    await waitFor(() => expect(createBatch).toHaveBeenCalledWith({
      delivery: 'browser',
      items: [{ source: 'netease', trackId: '186016', qualityId: 'lossless', qualitySnapshotId: 'snapshot-1' }],
    }))
  })

  it('never presents API failure as an available feature', async () => {
    const unavailable = new ApiError('服务仍是旧状态页', 404, 'request_failed')
    const api = createMockApi({
      getSessions: vi.fn().mockRejectedValue(unavailable),
      getTasks: vi.fn().mockRejectedValue(unavailable),
      search: vi.fn().mockRejectedValue(unavailable),
    })
    const screen = renderWorkbench(api)

    expect(await screen.findByRole('alert')).toHaveTextContent('musicdl-web API 当前不可用')
    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索词' }), '测试')
    await fireEvent.click(screen.getByRole('button', { name: '搜索' }))
    expect(await screen.findByText('服务仍是旧状态页')).toBeInTheDocument()
  })

  it('imports a session without rendering password fields or retaining the value', async () => {
    const importSession = vi.fn(createMockApi().importSession)
    const screen = renderWorkbench(createMockApi({ importSession }))
    await waitFor(() => expect(screen.getByText('会话有效')).toBeInTheDocument())
    await fireEvent.click(screen.getAllByRole('button', { name: '导入登录 Cookie' })[0]!)

    expect(screen.queryByLabelText(/密码/)).not.toBeInTheDocument()
    const input = screen.getByRole('textbox', { name: '登录 Cookie 请求头' })
    await fireEvent.update(input, 'MUSIC_U=local-session')
    await fireEvent.click(screen.getByRole('button', { name: '验证并替换会话' }))

    await waitFor(() => expect(importSession).toHaveBeenCalledWith('netease', { value: 'MUSIC_U=local-session' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows supported QR login and keeps QQ explicitly disabled', async () => {
    const screen = renderWorkbench()
    await waitFor(() => expect(screen.getByText('会话有效')).toBeInTheDocument())

    expect(screen.getByRole('button', { name: '扫码登录' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '尚未支持' })).toBeDisabled()
    expect(screen.getAllByRole('button', { name: '导入登录 Cookie' })).toHaveLength(2)
  })

  it('presents the QR image and cancels the challenge when closed', async () => {
    const cancelQr = vi.fn().mockResolvedValue(undefined)
    const screen = renderWorkbench(createMockApi({ cancelQr }))
    await waitFor(() => expect(screen.getByText('会话有效')).toBeInTheDocument())

    await fireEvent.click(screen.getByRole('button', { name: '扫码登录' }))
    const qrImage = await screen.findByRole('img', { name: '网易云音乐登录二维码' })
    expect(qrImage).toHaveAttribute('src', '/api/v1/sessions/netease/qr/netease-qr/image')
    expect(qrImage.getAttribute('src')).not.toMatch(/^data:/)
    expect(screen.getByText('会话有效')).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '关闭会话窗口' }))

    await waitFor(() => expect(cancelQr).toHaveBeenCalledWith('netease', 'netease-qr'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
