import { describe, expect, it, vi } from 'vitest'

import { createHttpApi } from './api'

describe('createHttpApi', () => {
  it('encodes search input and keeps the request on the first-party API', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ query: 'a&b', groups: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const api = createHttpApi('/api/v1', fetcher)

    await api.search('a&b', 'netease', 2)

    expect(fetcher).toHaveBeenCalledWith('/api/v1/search?q=a%26b&source=netease&page=2', expect.any(Object))
    expect(api.previewUrl('qq', 'mid/a')).toBe('/api/v1/tracks/qq/mid%2Fa/preview')
  })

  it('normalizes transport failures without exposing request internals', async () => {
    const api = createHttpApi('/api/v1', vi.fn<typeof fetch>().mockRejectedValue(new Error('secret URL')))

    await expect(api.getSessions()).rejects.toEqual(expect.objectContaining({
      code: 'api_unavailable',
      message: '无法连接 musicdl-web API',
      status: 0,
    }))
  })

  it('uses the scoped QR challenge endpoints without sending secret material', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        challengeId: 'challenge/a',
        state: 'waiting',
        imageUrl: '/api/v1/sessions/netease/qr/challenge%2Fa/image',
        expiresAt: '2099-07-31T08:05:00Z',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'scanned' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const api = createHttpApi('/api/v1', fetcher)

    const challenge = await api.beginQr('netease')
    await api.pollQr('netease', 'challenge/a')
    await api.cancelQr('netease', 'challenge/a')

    expect(challenge.imageUrl).toBe('/api/v1/sessions/netease/qr/challenge%2Fa/image')
    expect(challenge.imageUrl).not.toMatch(/^data:/)
    expect(challenge).not.toHaveProperty('imageDataUrl')
    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/v1/sessions/netease/qr', expect.objectContaining({ method: 'POST' }))
    expect(fetcher).toHaveBeenNthCalledWith(2, '/api/v1/sessions/netease/qr/challenge%2Fa', expect.objectContaining({}))
    expect(fetcher).toHaveBeenNthCalledWith(3, '/api/v1/sessions/netease/qr/challenge%2Fa', expect.objectContaining({ method: 'DELETE' }))
  })
})
