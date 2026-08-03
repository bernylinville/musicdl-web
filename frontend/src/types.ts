export type Source = 'netease' | 'qq'
export type SourceScope = 'all' | Source
export type Delivery = 'server' | 'browser'

export interface SessionStatus {
  source: Source
  state: 'anonymous' | 'authenticated' | 'expired' | 'unavailable'
  displayName: string | null
  qrEnabled: boolean
  checkedAt: string | null
  message: string | null
}

export interface Track {
  source: Source
  trackId: string
  title: string
  artists: string[]
  album: string
  durationMs: number
  coverUrl: string | null
  library: null | {
    state: 'available' | 'missing'
    qualityLabel: string
  }
  /** Parallel to artists when the platform exposes catalog ids (Netease). */
  artistIds?: string[]
  albumId?: string | null
  /** Netease red-heart; null when unknown or no session. */
  liked?: boolean | null
}

export interface SearchGroup {
  source: Source
  tracks: Track[]
  page: number
  hasMore: boolean
  status: 'ready' | 'unavailable'
  message: string | null
}

export interface SearchResponse {
  query: string
  groups: SearchGroup[]
}

export interface QualityOption {
  id: string
  label: string
  fidelity: 'standard' | 'high' | 'lossless' | 'hi_res' | 'master' | 'spatial'
  codec: string | null
  estimatedSizeBytes: number | null
  requiresSession: boolean
  upgrade: boolean
}

export interface QualitySnapshot {
  snapshotId: string
  expiresAt: string
  sessionVersion: string | null
  options: QualityOption[]
}

export type QualityState =
  | { status: 'idle'; options: []; snapshotId: null; message: null }
  | { status: 'loading'; options: []; snapshotId: null; message: null }
  | { status: 'ready'; options: QualityOption[]; snapshotId: string; expiresAt: string; message: null }
  | { status: 'stale' | 'session_required' | 'unavailable'; options: []; snapshotId: null; message: string }

export interface DownloadSelection {
  source: Source
  trackId: string
  qualityId: string
  qualitySnapshotId: string
}

export type TaskStage =
  | 'queued'
  | 'resolving'
  | 'downloading'
  | 'tagging'
  | 'publishing'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface DownloadTask {
  id: string
  track: Pick<Track, 'source' | 'trackId' | 'title' | 'artists'>
  qualityLabel: string
  delivery: Delivery
  stage: TaskStage
  progress: number | null
  error: string | null
  warning: string | null
  browserFileUrl: string | null
  createdAt: string
}

export interface SessionImportPayload {
  value: string
}

export type QrLoginState = 'waiting' | 'scanned' | 'success' | 'expired'

export interface QrChallenge {
  challengeId: string
  state: 'waiting'
  imageUrl: string
  expiresAt: string
}

export interface QrPollResult {
  state: QrLoginState
  session?: SessionStatus
}

export interface CreateBatchPayload {
  delivery: Delivery
  items: DownloadSelection[]
}
