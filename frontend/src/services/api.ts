import type { InjectionKey } from 'vue'

import type {
  CreateBatchPayload,
  DownloadTask,
  QrChallenge,
  QrPollResult,
  QualitySnapshot,
  SearchResponse,
  SessionImportPayload,
  SessionStatus,
  Source,
  SourceScope,
} from '@/types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface MusicApi {
  getSessions(signal?: AbortSignal): Promise<SessionStatus[]>
  beginQr(source: Source, signal?: AbortSignal): Promise<QrChallenge>
  pollQr(source: Source, challengeId: string, signal?: AbortSignal): Promise<QrPollResult>
  cancelQr(source: Source, challengeId: string, signal?: AbortSignal): Promise<void>
  importSession(source: Source, payload: SessionImportPayload, signal?: AbortSignal): Promise<SessionStatus>
  clearSession(source: Source, signal?: AbortSignal): Promise<void>
  search(query: string, source: SourceScope, page: number, signal?: AbortSignal): Promise<SearchResponse>
  getQualities(source: Source, trackId: string, signal?: AbortSignal): Promise<QualitySnapshot>
  previewUrl(source: Source, trackId: string): string
  createBatch(payload: CreateBatchPayload, signal?: AbortSignal): Promise<DownloadTask[]>
  getTasks(scope: 'active' | 'history', signal?: AbortSignal): Promise<DownloadTask[]>
  cancelTask(taskId: string, signal?: AbortSignal): Promise<DownloadTask>
  retryTask(taskId: string, signal?: AbortSignal): Promise<DownloadTask>
  clearHistory(signal?: AbortSignal): Promise<void>
}

export const apiKey: InjectionKey<MusicApi> = Symbol('music-api')

interface ErrorBody {
  detail?: string
  code?: string
}

function encode(value: string): string {
  return encodeURIComponent(value)
}

export function createHttpApi(baseUrl = '/api/v1', fetcher: typeof fetch = fetch): MusicApi {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers: { Accept: 'application/json', ...init.headers },
      })
    } catch {
      throw new ApiError('无法连接 musicdl-web API', 0, 'api_unavailable')
    }
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ErrorBody
      throw new ApiError(body.detail ?? `API 请求失败（${response.status}）`, response.status, body.code ?? 'request_failed')
    }
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  const json = (body: unknown): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  const withSignal = (signal?: AbortSignal): RequestInit => signal ? { signal } : {}

  return {
    getSessions: (signal) => request('/sessions', withSignal(signal)),
    beginQr: (source, signal) => request(`/sessions/${source}/qr`, { method: 'POST', ...withSignal(signal) }),
    pollQr: (source, challengeId, signal) => request(`/sessions/${source}/qr/${encode(challengeId)}`, withSignal(signal)),
    cancelQr: (source, challengeId, signal) => request(`/sessions/${source}/qr/${encode(challengeId)}`, { method: 'DELETE', ...withSignal(signal) }),
    importSession: (source, payload, signal) => request(`/sessions/${source}/import`, { ...json(payload), ...withSignal(signal) }),
    clearSession: (source, signal) => request(`/sessions/${source}`, { method: 'DELETE', ...withSignal(signal) }),
    search: (query, source, page, signal) =>
      request(`/search?q=${encode(query)}&source=${source}&page=${page}`, withSignal(signal)),
    getQualities: (source, trackId, signal) => request(`/tracks/${source}/${encode(trackId)}/qualities`, withSignal(signal)),
    previewUrl: (source, trackId) => `${baseUrl}/tracks/${source}/${encode(trackId)}/preview`,
    createBatch: (payload, signal) => request('/batches', { ...json(payload), ...withSignal(signal) }),
    getTasks: (scope, signal) => request(`/tasks?scope=${scope}`, withSignal(signal)),
    cancelTask: (taskId, signal) => request(`/tasks/${encode(taskId)}/cancel`, { method: 'POST', ...withSignal(signal) }),
    retryTask: (taskId, signal) => request(`/tasks/${encode(taskId)}/retry`, { method: 'POST', ...withSignal(signal) }),
    clearHistory: (signal) => request('/history', { method: 'DELETE', ...withSignal(signal) }),
  }
}
