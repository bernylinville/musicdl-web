<script setup lang="ts">
import type { SessionStatus, Source } from '@/types'
import { sourceLabels } from '@/utils/format'

defineProps<{ sessions: readonly SessionStatus[]; apiUnavailable: boolean }>()

const emit = defineEmits<{
  qr: [source: Source]
  import: [source: Source]
  logout: [source: Source]
}>()

const stateLabels: Record<SessionStatus['state'], string> = {
  anonymous: '匿名使用',
  authenticated: '会话有效',
  expired: '会话失效',
  unavailable: '状态不可用',
}
</script>

<template>
  <header class="session-strip">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">M</span>
      <div>
        <strong>musicdl-web</strong>
        <span>下载工作台</span>
      </div>
    </div>
    <div class="session-list" aria-label="平台会话状态">
      <article v-for="session in sessions" :key="session.source" class="session-card">
        <div class="session-summary">
          <span class="source-dot" :class="`source-${session.source}`" aria-hidden="true" />
          <div>
            <strong>{{ sourceLabels[session.source] }}</strong>
            <span :class="['session-state', `state-${session.state}`]">{{ stateLabels[session.state] }}</span>
          </div>
        </div>
        <span v-if="session.displayName" class="account-name">{{ session.displayName }}</span>
        <span v-else-if="session.message" class="session-message">{{ session.message }}</span>
        <div class="session-actions">
          <button v-if="session.qrEnabled" class="button button-quiet" type="button" @click="emit('qr', session.source)">扫码登录</button>
          <button v-else-if="session.source === 'qq'" class="button button-quiet" type="button" disabled>尚未支持</button>
          <button class="button button-quiet" type="button" @click="emit('import', session.source)">导入登录 Cookie</button>
          <button v-if="session.state === 'authenticated'" class="button button-quiet danger" type="button" @click="emit('logout', session.source)">退出</button>
        </div>
      </article>
      <p v-if="apiUnavailable" class="session-placeholder" role="status">会话 API 不可用</p>
    </div>
  </header>
</template>

<style scoped>
.session-strip { display: flex; align-items: stretch; gap: 22px; min-height: 74px; padding: 12px 20px; border-bottom: 1px solid var(--line); background: var(--surface); }
.brand { display: flex; align-items: center; gap: 10px; min-width: 188px; }
.brand-mark { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 8px; color: #fff; background: var(--accent); font-size: 15px; font-weight: 800; }
.brand div { display: grid; gap: 2px; }
.brand strong { font-size: 14px; letter-spacing: -.01em; }
.brand span:last-child { color: var(--muted); font-size: 12px; }
.session-list { display: flex; flex: 1; gap: 10px; }
.session-card { display: grid; grid-template-columns: minmax(118px, 1fr) auto; align-items: center; column-gap: 14px; flex: 1; max-width: 430px; min-width: 290px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-raised); }
.session-summary { display: flex; align-items: center; gap: 9px; }
.session-summary div { display: grid; gap: 2px; }
.session-summary strong { font-size: 13px; }
.source-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
.source-netease { background: #d83b32; }
.source-qq { background: #e5a829; }
.session-state, .account-name, .session-message { color: var(--muted); font-size: 11px; }
.state-authenticated { color: var(--success); }
.state-expired, .state-unavailable { color: var(--danger); }
.session-message { overflow: hidden; max-width: 170px; text-overflow: ellipsis; white-space: nowrap; }
.session-actions { display: flex; gap: 4px; grid-column: 2; grid-row: 1 / span 2; }
.session-placeholder { align-self: center; margin: 0; color: var(--danger); font-size: 12px; }
@media (max-width: 1100px) { .session-strip { gap: 12px; } .brand { min-width: 156px; } .session-card { min-width: 260px; } }
</style>
