<script setup lang="ts">
import { inject, shallowRef } from 'vue'

import { usePreview } from '@/composables/usePreview'
import { useQrLogin } from '@/composables/useQrLogin'
import { useWorkbench } from '@/composables/useWorkbench'
import { apiKey } from '@/services/api'
import type { Source, Track } from '@/types'

import SelectionActionBar from '../download/SelectionActionBar.vue'
import SearchToolbar from '../search/SearchToolbar.vue'
import SourceResultGroup from '../search/SourceResultGroup.vue'
import SessionDialog from '../session/SessionDialog.vue'
import SessionStrip from '../session/SessionStrip.vue'
import HistoryPanel from '../tasks/HistoryPanel.vue'
import QueuePanel from '../tasks/QueuePanel.vue'

const api = inject(apiKey)
if (!api) throw new Error('Music API 未注入')

const workbench = useWorkbench(api)
const preview = usePreview(api)
const dialogSource = shallowRef<Source | null>(null)
const dialogMode = shallowRef<'qr' | 'import'>('import')
const dialogBusy = shallowRef(false)
const dialogError = shallowRef<string | null>(null)

const qrLogin = useQrLogin(api, {
  onSuccess: async () => {
    try {
      await workbench.refreshSessions()
    } finally {
      // Challenge is already terminal on the server; dismiss like Cookie import.
      dialogSource.value = null
      dialogError.value = null
    }
  },
})

function openImport(source: Source): void {
  dialogSource.value = source
  dialogMode.value = 'import'
  dialogError.value = null
}

function openQr(source: Source): void {
  dialogSource.value = source
  dialogMode.value = 'qr'
  dialogError.value = null
  void qrLogin.start(source)
}

function closeDialog(): void {
  if (dialogMode.value === 'qr') void qrLogin.cancel()
  dialogSource.value = null
  dialogError.value = null
}

function refreshQr(): void {
  if (dialogSource.value) void qrLogin.start(dialogSource.value)
}

async function submitImport(value: string): Promise<void> {
  if (!dialogSource.value) return
  dialogBusy.value = true
  dialogError.value = null
  try {
    await workbench.importSession(dialogSource.value, value)
    closeDialog()
  } catch (error) {
    dialogError.value = error instanceof Error ? error.message : '导入登录 Cookie 失败'
  } finally {
    dialogBusy.value = false
  }
}

function togglePreview(track: Track): void {
  preview.toggle(track.source, track.trackId)
}
</script>

<template>
  <div class="app-shell">
    <SessionStrip
      :sessions="workbench.sessions.value"
      :api-unavailable="workbench.apiState.value === 'unavailable'"
      @qr="openQr"
      @import="openImport"
      @logout="workbench.clearSession"
    />

    <div v-if="workbench.apiState.value === 'unavailable'" class="api-banner" role="alert">
      <strong>musicdl-web API 当前不可用</strong>
      <span>页面已加载，但搜索、会话、音质和下载操作不会被伪装为可用。请检查后端版本或网络连接。</span>
    </div>

    <div class="workspace">
      <main class="main-column">
        <div class="workbench-heading">
          <div>
            <span class="eyebrow">统一搜索</span>
            <h1>查找并获取平台歌曲</h1>
          </div>
          <p>结果按音乐源独立排序；音质仅代表当前会话下的短期确认。</p>
        </div>

        <SearchToolbar
          v-model:query="workbench.query.value"
          v-model:source="workbench.sourceScope.value"
          :loading="workbench.searchState.value === 'loading'"
          :liked-enabled="workbench.sessions.value.some((s) => s.source === 'netease' && s.state === 'authenticated')"
          :liked-active="workbench.catalogMode.value === 'liked'"
          @submit="workbench.search()"
          @liked="workbench.loadLiked()"
        />

        <p v-if="workbench.searchMessage.value" class="inline-error" role="alert">{{ workbench.searchMessage.value }}</p>
        <p
          v-if="workbench.submitMessage.value && !workbench.selectedCount.value"
          :class="['download-status', { error: workbench.submitState.value === 'error' }]"
          role="status"
        >{{ workbench.submitMessage.value }}</p>

        <div v-if="workbench.groups.value.length" class="result-stack">
          <SourceResultGroup
            v-for="group in workbench.groups.value"
            :key="group.source"
            :group="group"
            :selected-tracks="workbench.selectedTracks.value"
            :qualities="workbench.qualities.value"
            :selected-quality-ids="workbench.selectedQualityIds.value"
            :row-download-keys="workbench.rowDownloadKeys.value"
            :preview-key="preview.activeKey.value"
            :preview-state="preview.state.value"
            @toggle="workbench.toggleTrack"
            @request-quality="workbench.requestQuality"
            @select-quality="workbench.setQuality"
            @preview="togglePreview"
            @download="workbench.downloadTrack"
            @load-more="workbench.loadMore(group.source)"
            @open-artist="(source, artistId, title) => workbench.openArtist(source, artistId, title)"
            @open-album="(source, albumId, title) => workbench.openAlbum(source, albumId, title)"
            @set-liked="(track, liked) => workbench.setTrackLiked(track, liked)"
          />
        </div>
        <div v-else class="welcome-state">
          <div class="welcome-glyph" aria-hidden="true">⌕</div>
          <h2>从一条搜索词开始</h2>
          <p>全部来源会分别返回网易云音乐和 QQ 音乐结果。选择歌曲后才会确认当前可选音质。</p>
          <div class="truth-row">
            <span>不合并跨平台歌曲</span>
            <span>不自动降级音质</span>
            <span>仅短试听确认</span>
          </div>
        </div>

        <SelectionActionBar
          v-if="workbench.selectedCount.value"
          v-model:delivery="workbench.delivery.value"
          :selected-count="workbench.selectedCount.value"
          :ready-count="workbench.readyCount.value"
          :submitting="workbench.submitState.value === 'submitting'"
          :message="workbench.submitMessage.value"
          :message-error="workbench.submitState.value === 'error'"
          @clear="workbench.clearSelection"
          @submit="workbench.submit"
        />
      </main>

      <aside class="side-column" aria-label="任务状态">
        <p v-if="workbench.operationMessage.value" class="inline-error operation-error" role="alert">{{ workbench.operationMessage.value }}</p>
        <QueuePanel :tasks="workbench.activeTasks.value" @cancel="workbench.cancelTask" @retry="workbench.retryTask" />
        <HistoryPanel :tasks="workbench.history.value" @retry="workbench.retryTask" @clear="workbench.clearHistory" />
        <section class="boundary-note">
          <strong>权益与文件边界</strong>
          <p>仅请求公开资源或当前平台账号的合法权益。保存到服务器与浏览器取回互斥；任何音质失效都明确失败。</p>
        </section>
      </aside>
    </div>

    <SessionDialog
      v-if="dialogSource"
      :source="dialogSource"
      :mode="dialogMode"
      :challenge="qrLogin.challenge.value"
      :qr-state="qrLogin.state.value"
      :busy="dialogMode === 'qr' ? qrLogin.busy.value : dialogBusy"
      :error="dialogMode === 'qr' ? qrLogin.error.value : dialogError"
      @close="closeDialog"
      @refresh-qr="refreshQr"
      @submit-import="submitImport"
    />
  </div>
</template>

<style scoped>
.app-shell { min-width: 1024px; min-height: 100vh; background: var(--canvas); }
.api-banner { display: flex; align-items: center; justify-content: center; gap: 9px; min-height: 34px; padding: 5px 20px; color: var(--danger); background: var(--danger-soft); font-size: 11px; }
.workspace { display: grid; grid-template-columns: minmax(700px, 1fr) 294px; gap: 14px; width: min(1500px, 100%); margin: 0 auto; padding: 18px 20px 32px; }
.main-column { min-width: 0; }
.workbench-heading { display: flex; align-items: end; justify-content: space-between; margin-bottom: 12px; }
.workbench-heading h1 { margin: 3px 0 0; font-size: 19px; letter-spacing: -.02em; }
.workbench-heading p { max-width: 420px; margin: 0 0 2px; color: var(--muted); font-size: 11px; text-align: right; }
.eyebrow { color: var(--accent); font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.result-stack { display: grid; gap: 12px; margin-top: 12px; }
.welcome-state { display: grid; min-height: 350px; place-items: center; align-content: center; margin-top: 12px; padding: 48px; border: 1px dashed var(--line-strong); border-radius: 9px; color: var(--muted); text-align: center; }
.welcome-glyph { display: grid; width: 48px; height: 48px; place-items: center; margin-bottom: 12px; border-radius: 12px; color: var(--accent); background: var(--accent-soft); font-size: 28px; transform: rotate(-20deg); }
.welcome-state h2 { margin: 0; color: var(--text); font-size: 15px; }
.welcome-state p { max-width: 490px; margin: 8px 0 16px; font-size: 12px; line-height: 1.6; }
.truth-row { display: flex; gap: 6px; }
.truth-row span { padding: 4px 7px; border: 1px solid var(--line); border-radius: 4px; background: var(--surface); font-size: 10px; }
.side-column { display: grid; align-content: start; gap: 12px; }
.operation-error { margin: 0; }
.boundary-note { padding: 12px; border: 1px solid var(--line); border-radius: 8px; color: var(--muted); background: var(--canvas-subtle); font-size: 10px; line-height: 1.5; }
.boundary-note strong { color: var(--text); }
.boundary-note p { margin: 5px 0 0; }
.inline-error { margin: 10px 0 0; }
.download-status {
  margin: 10px 0 0;
  padding: 8px 12px;
  border: 1px solid color-mix(in srgb, var(--success) 35%, var(--line));
  border-radius: 7px;
  color: var(--success);
  background: var(--success-soft, color-mix(in srgb, var(--success) 12%, var(--surface)));
  font-size: 12px;
}
.download-status.error {
  border-color: color-mix(in srgb, var(--danger) 35%, var(--line));
  color: var(--danger);
  background: var(--danger-soft);
}
</style>
