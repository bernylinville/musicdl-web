import type { MusicApi } from '@/services/api'
import type {
  CreateBatchPayload,
  DownloadTask,
  QrChallenge,
  QualitySnapshot,
  SearchResponse,
  SessionStatus,
  Source,
} from '@/types'

export const mockSessions: SessionStatus[] = [
  {
    source: 'netease',
    state: 'authenticated',
    displayName: '本机会话',
    qrEnabled: true,
    checkedAt: '2026-07-31T08:00:00Z',
    message: null,
  },
  {
    source: 'qq',
    state: 'anonymous',
    displayName: null,
    qrEnabled: false,
    checkedAt: '2026-07-31T08:00:00Z',
    message: '二维码登录尚未支持，可导入登录 Cookie',
  },
]

export const mockSearch: SearchResponse = {
  query: '晴天',
  groups: [
    {
      source: 'netease',
      page: 1,
      hasMore: true,
      status: 'ready',
      message: null,
      tracks: [
        {
          source: 'netease',
          trackId: '186016',
          title: '晴天',
          artists: ['周杰伦'],
          album: '叶惠美',
          durationMs: 269000,
          coverUrl: '/api/v1/covers/netease/186016',
          library: null,
          artistIds: ['6452'],
          albumId: '32311',
          liked: true,
        },
      ],
    },
    {
      source: 'qq',
      page: 1,
      hasMore: false,
      status: 'unavailable',
      message: 'QQ 搜索接口当前不可用',
      tracks: [],
    },
  ],
}

const activeTask: DownloadTask = {
  id: 'task-1',
  track: { source: 'netease', trackId: '186016', title: '晴天', artists: ['周杰伦'] },
  qualityLabel: '无损',
  delivery: 'server',
  stage: 'downloading',
  progress: 42,
  error: null,
  warning: null,
  browserFileUrl: null,
  createdAt: '2026-07-31T08:00:00Z',
}

export function createMockApi(overrides: Partial<MusicApi> = {}): MusicApi {
  const api: MusicApi = {
    getSessions: async () => mockSessions,
    getLikedTracks: async () => ({
      query: '我喜欢的音乐',
      groups: [
        {
          source: 'netease',
          page: 1,
          hasMore: false,
          status: 'ready',
          message: null,
          tracks: mockSearch.groups[0]!.tracks,
        },
      ],
    }),
    getArtistTracks: async (_source, _artistId, _page, title) => ({
      query: `歌手 · ${title ?? 'Artist'}`,
      groups: [
        {
          source: 'netease',
          page: 1,
          hasMore: false,
          status: 'ready',
          message: null,
          tracks: mockSearch.groups[0]!.tracks,
        },
      ],
    }),
    getAlbumTracks: async (_source, _albumId, _page, title) => ({
      query: `专辑 · ${title ?? 'Album'}`,
      groups: [
        {
          source: 'netease',
          page: 1,
          hasMore: false,
          status: 'ready',
          message: null,
          tracks: mockSearch.groups[0]!.tracks,
        },
      ],
    }),
    setTrackLiked: async (source, trackId, liked) => ({ source, trackId, liked }),
    beginQr: async (source: Source): Promise<QrChallenge> => ({
      challengeId: `${source}-qr`,
      state: 'waiting',
      imageUrl: `/api/v1/sessions/${source}/qr/${source}-qr/image`,
      expiresAt: '2026-07-31T08:05:00Z',
    }),
    pollQr: async () => ({ state: 'waiting' }),
    cancelQr: async () => undefined,
    importSession: async (source) => ({ ...mockSessions.find((item) => item.source === source)!, state: 'authenticated' }),
    clearSession: async () => undefined,
    search: async () => mockSearch,
    getQualities: async (): Promise<QualitySnapshot> => ({
      snapshotId: 'snapshot-1',
      expiresAt: '2099-07-31T08:05:00Z',
      sessionVersion: 'session-1',
      options: [
        { id: 'lossless', label: '无损', fidelity: 'lossless', codec: 'FLAC', estimatedSizeBytes: 31_000_000, requiresSession: true, upgrade: false },
      ],
    }),
    previewUrl: (source, trackId) => `/api/v1/tracks/${source}/${trackId}/preview`,
    createBatch: async (payload: CreateBatchPayload) => payload.items.map((_, index) => ({ ...activeTask, id: `created-${index}` })),
    getTasks: async (scope) => (scope === 'active' ? [activeTask] : []),
    cancelTask: async () => ({ ...activeTask, stage: 'cancelled' }),
    retryTask: async () => ({ ...activeTask, stage: 'queued', progress: 0 }),
    clearHistory: async () => undefined,
  }
  return { ...api, ...overrides }
}
