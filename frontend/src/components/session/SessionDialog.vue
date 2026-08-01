<script setup lang="ts">
import { computed, shallowRef, useTemplateRef } from 'vue'

import type { QrChallenge, QrLoginState, Source } from '@/types'
import { sourceLabels } from '@/utils/format'

const props = defineProps<{
  source: Source
  mode: 'qr' | 'import'
  challenge: QrChallenge | null
  qrState: QrLoginState | 'idle' | 'loading' | 'error'
  busy: boolean
  error: string | null
}>()

const emit = defineEmits<{
  close: []
  refreshQr: []
  submitImport: [value: string]
}>()

const sessionValue = shallowRef('')
const input = useTemplateRef<HTMLTextAreaElement>('sessionInput')
const title = computed(() => `${sourceLabels[props.source]} · ${props.mode === 'qr' ? '扫码登录' : '导入登录 Cookie'}`)
const showQrImage = computed(() => props.challenge !== null && (props.qrState === 'waiting' || props.qrState === 'scanned'))
const qrStatus = computed(() => {
  if (props.qrState === 'scanned') return '已扫描，请在手机上确认'
  if (props.qrState === 'success') return '登录成功'
  if (props.qrState === 'expired') return '二维码已过期'
  if (props.qrState === 'loading') return '正在获取二维码…'
  if (props.qrState === 'error') return '二维码不可用'
  return '等待扫描'
})

function submit(): void {
  if (!sessionValue.value.trim()) {
    input.value?.focus()
    return
  }
  emit('submitImport', sessionValue.value.trim())
  sessionValue.value = ''
}
</script>

<template>
  <div class="dialog-backdrop" @click.self="emit('close')">
    <section class="dialog" role="dialog" aria-modal="true" :aria-labelledby="`${source}-dialog-title`">
      <header class="dialog-header">
        <div>
          <span class="eyebrow">平台会话</span>
          <h2 :id="`${source}-dialog-title`">{{ title }}</h2>
        </div>
        <button class="icon-button" type="button" aria-label="关闭会话窗口" @click="emit('close')">×</button>
      </header>

      <div v-if="mode === 'qr'" class="qr-content">
        <p class="dialog-copy">请使用你本人的网易云音乐 App 扫描二维码。二维码只用于本次登录。</p>
        <div class="qr-frame" aria-live="polite">
          <img v-if="showQrImage && challenge" :src="challenge.imageUrl" alt="网易云音乐登录二维码" />
          <span v-else>{{ qrStatus }}</span>
        </div>
        <p class="muted" role="status">状态：{{ qrStatus }}</p>
        <button v-if="qrState === 'expired'" class="button button-primary refresh-button" type="button" @click="emit('refreshQr')">刷新二维码</button>
      </div>

      <form v-else class="import-form" @submit.prevent="submit">
        <p class="dialog-copy">请从你本人已登录的平台网页复制 Cookie 请求头并粘贴到这里。musicdl-web 不会要求账号或密码。</p>
        <label for="session-value">登录 Cookie 请求头</label>
        <textarea id="session-value" ref="sessionInput" v-model="sessionValue" rows="6" autocomplete="off" spellcheck="false" placeholder="粘贴 Cookie 请求头；提交后输入框立即清空" />
        <button class="button button-primary" type="submit" :disabled="busy">{{ busy ? '正在验证…' : '验证并替换会话' }}</button>
      </form>
      <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    </section>
  </div>
</template>

<style scoped>
.dialog-backdrop { position: fixed; z-index: 20; inset: 0; display: grid; place-items: center; padding: 24px; background: rgb(0 0 0 / .5); }
.dialog { width: min(480px, 100%); border: 1px solid var(--line-strong); border-radius: 10px; background: var(--surface); box-shadow: var(--shadow-lg); }
.dialog-header { display: flex; align-items: flex-start; justify-content: space-between; padding: 20px 20px 14px; border-bottom: 1px solid var(--line); }
.dialog-header h2 { margin: 3px 0 0; font-size: 18px; }
.eyebrow { color: var(--muted); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.icon-button { width: 30px; height: 30px; border: 0; border-radius: 6px; color: var(--muted); background: transparent; font-size: 22px; cursor: pointer; }
.icon-button:hover { background: var(--surface-hover); color: var(--text); }
.qr-content, .import-form { display: grid; gap: 14px; padding: 20px; }
.dialog-copy, .muted { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.qr-frame { display: grid; width: 208px; height: 208px; place-items: center; justify-self: center; border: 1px dashed var(--line-strong); border-radius: 8px; background: #fff; color: #555; }
.qr-frame img { width: 192px; height: 192px; image-rendering: pixelated; }
.import-form label { font-size: 12px; font-weight: 700; }
.import-form textarea { resize: vertical; min-height: 112px; font: 12px/1.5 var(--font-mono); }
.import-form .button { justify-self: end; }
.refresh-button { justify-self: center; }
.inline-error { margin: 0 20px 20px; }
</style>
