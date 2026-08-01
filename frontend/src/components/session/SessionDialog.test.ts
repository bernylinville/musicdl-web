import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import type { QrLoginState } from '@/types'

import SessionDialog from './SessionDialog.vue'

const statusLabels: Record<QrLoginState, string> = {
  waiting: '等待扫描',
  scanned: '已扫描，请在手机上确认',
  success: '登录成功',
  expired: '二维码已过期',
}

describe('SessionDialog', () => {
  it.each(Object.entries(statusLabels) as [QrLoginState, string][])(
    'presents the %s QR state',
    (qrState, label) => {
      const screen = render(SessionDialog, {
        props: {
          source: 'netease',
          mode: 'qr',
          challenge: {
            challengeId: 'challenge-1',
            state: 'waiting',
            imageUrl: '/api/v1/sessions/netease/qr/challenge-1/image',
            expiresAt: '2099-07-31T08:05:00Z',
          },
          qrState,
          busy: false,
          error: null,
        },
      })

      expect(screen.getByRole('status')).toHaveTextContent(label)
      expect(screen.queryByRole('button', { name: '刷新二维码' }) !== null).toBe(qrState === 'expired')
      expect(screen.queryByRole('img', { name: '网易云音乐登录二维码' }) !== null)
        .toBe(qrState === 'waiting' || qrState === 'scanned')
    },
  )

  it('requests a manual refresh after expiry', async () => {
    const onRefreshQr = vi.fn()
    const screen = render(SessionDialog, {
      props: {
        source: 'netease',
        mode: 'qr',
        challenge: null,
        qrState: 'expired',
        busy: false,
        error: null,
        onRefreshQr,
      },
    })

    await fireEvent.click(screen.getByRole('button', { name: '刷新二维码' }))

    expect(onRefreshQr).toHaveBeenCalledOnce()
  })
})
