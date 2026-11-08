<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Sidebar from '../components/Sidebar.vue'
import DocPanel from '../components/DocPanel.vue'
import Turn from '../components/Turn.vue'
import StepBar from '../components/StepBar.vue'
import Splitter from '../components/Splitter.vue'
import Icon from '../components/Icon.vue'
import { api, uid } from '../lib/api.js'
import { auth } from '../lib/auth.js'
import { openStream } from '../lib/sse.js'
import { createTimeline, STEP_NAMES } from '../lib/timeline.js'
import { toast } from '../lib/toast.js'

const route = useRoute()
const router = useRouter()
const convs = ref([])
const listLoading = ref(true)
const sideCollapsed = ref(localStorage.getItem('acc.side') === '1')
// 可拖拽布局：侧栏宽度 / 文档面板宽度（持久化）
const defaultPanel = Math.min(760, Math.round(window.innerWidth * 0.42))
const sideW = ref(Number(localStorage.getItem('acc.sideW')) || 264)
const panelW = ref(Number(localStorage.getItem('acc.panelW')) || defaultPanel)
const winW = ref(window.innerWidth)
const wide = computed(() => winW.value > 1100)
const maxPanel = computed(() => Math.max(420, winW.value - (sideCollapsed.value ? 60 : sideW.value) - 420))
const onResize = () => { winW.value = window.innerWidth; if (panelW.value > maxPanel.value) panelW.value = maxPanel.value }
window.addEventListener('resize', onResize)
const panelOpen = ref(localStorage.getItem('acc.panel') !== '0')
const conv = ref(null)
const loading = ref(false)
const input = ref('')
const sending = ref(false)
const stopping = ref(false)
const downloading = ref(false)
const streamState = ref('closed')
const doc = shallowRef(null)
const prevDoc = shallowRef(null)
const docKind = ref('outline')
const docLive = ref(false)
const tl = shallowRef(createTimeline())
const chatEl = ref(null)
const textareaEl = ref(null)
let stream = null
let liveTimer = null
let stickBottom = true
let pendingDoc = null
let docRaf = 0

const S = computed(() => tl.value.state)
const id = computed(() => route.params.id || null)
const status = computed(() => S.value.status || conv.value?.status || 'idle')
const busy = computed(() => S.value.running || status.value === 'running')
const closed = computed(() => ['completed', 'closed'].includes(status.value))
const canResume = computed(() => ['paused', 'failed'].includes(status.value) && !busy.value)
const canSend = computed(() => !!input.value.trim() && !sending.value && !busy.value && !closed.value)
const placeholder = computed(() => {
  if (closed.value) return '该任务已完成并关闭，如需新合同请新建任务'
  if (busy.value) return 'AI 正在处理中，可点击右侧按钮停止…'
  if (status.value === 'paused' || status.value === 'failed') return '已暂停：可点击“继续生成”，或直接输入新的要求'
  if (S.value.step === 4) return '对大纲提出修改意见，或输入「确认」进入起草'
  if (S.value.step === 8) return '对合同提出修改意见，或输入「确认」完成'
  return '描述你的合同需求，例如：我是房东，要出租上海松江的一套住房，月租 2500 元押一付三…'
})
const SAMPLES = [
  '我是房东，需要一份上海松江区住房租赁合同（个人对个人、居住），租期 1 年，月租 2500 元押一付三，允许养一只猫，违约金 2 个月租金，逾期付租宽限 5 天。',
  '我们公司要与 IT 服务商签订软件运维技术服务合同（企业对企业，北京），服务期 2 年，年费 36 万元按季度支付，7×24 小时响应，可用率不低于 99.9%。',
  '游戏公司授权渠道公司在江苏地区使用手游角色形象、宣传视频做推广，期限一年，不得转授权，按推广效果分成，需包含保密与品牌保护条款。',
]

// ------------------------------------------------------------ 额度
const quota = ref(null)
const usingOwn = ref(false)
const quotaMsg = ref('')
async function loadQuota() {
  try { const a = await api.account(); quota.value = a.quota; usingOwn.value = !!a.active_model_id; auth.set(auth.token.value, a.user) } catch { /* 非关键 */ }
}
const onQuota = (e) => { quotaMsg.value = e.detail || '免费额度已用完'; loadQuota() }
window.addEventListener('acc:quota', onQuota)

// ------------------------------------------------------------ 列表
async function loadList() {
  try { convs.value = (await api.list()).items } catch (e) { toast(e.message, 'err') } finally { listLoading.value = false }
}
function patchConv(cid, patch) {
  const c = convs.value.find((x) => x.id === cid)
  if (c) Object.assign(c, patch)
  if (conv.value?.id === cid) conv.value = { ...conv.value, ...patch }
}

// ------------------------------------------------------------ 打开会话
async function open(cid) {
  closeStream()
  tl.value = createTimeline()
  doc.value = null; prevDoc.value = null; conv.value = null
  if (!cid) return
  loading.value = true
  try {
    const d = await api.get(cid)
    if (id.value !== cid) return
    conv.value = d.conversation
    const t = tl.value
    t.state.stepDone.splice(0, t.state.stepDone.length, ...(d.pipeline.step_done || []))
    t.state.step = d.pipeline.current_step || 1
    t.hydrate(d.messages, d.events)
    t.state.status = d.conversation.status
    t.state.running = d.conversation.running
    if (d.contract) { doc.value = d.contract.content; docKind.value = d.contract.kind }
    stream = openStream(cid, { onEvent, onState: (s) => (streamState.value = s), lastEventId: d.live_event_id })
    await nextTick(); scrollBottom(true)
  } catch (e) {
    toast(e.message, 'err')
    if (e.status === 404) router.replace('/')
  } finally { loading.value = false }
}
function closeStream() { stream?.close(); stream = null }

// ------------------------------------------------------------ 实时事件
function onEvent(ev) {
  const t = ev.event_type
  if (t === 'contract.snapshot' || t === 'contract.update') {
    const next = ev.data.outline
    if (next) queueDoc(next, ev.data.kind || (ev.step >= 5 ? 'contract' : 'outline'))
    return
  }
  tl.value.apply(ev)
  if (t === 'run.started') { patchConv(conv.value.id, { status: 'running', running: true }); docLive.value = false }
  if (t === 'run.waiting') { patchConv(conv.value.id, { status: 'waiting_input', running: false, current_step: ev.data.step }); focusInput() }
  if (t === 'run.stopped') patchConv(conv.value.id, { status: 'paused', running: false })
  if (t === 'run.failed') patchConv(conv.value.id, { status: 'failed', running: false })
  if (t === 'run.completed') { patchConv(conv.value.id, { status: 'completed', running: false }); docLive.value = false; toast('合同已完成，可下载 Word 文档'); loadQuota() }
  if (t === 'run.failed' && ev.data?.quota) { quotaMsg.value = ev.data.error; loadQuota() }
  if (t === 'run.stopped') loadQuota()
  if (t === 'step.start') patchConv(conv.value.id, { current_step: ev.step })
  if (stickBottom) scrollBottom()
}

// 文档更新节流到每帧一次；diff 基准 = 本轮修改前的版本，修改停止 2.5s 后清除高亮基准
function queueDoc(next, kind) {
  if (!docLive.value) prevDoc.value = doc.value && kind === docKind.value ? doc.value : null
  pendingDoc = { next, kind }
  docLive.value = true
  // 节流：并行起草时每次工具编辑都会推送整份合同，限制为每 400ms 最多渲染一次（diff 计算为 O(n·m)）
  if (!docRaf) docRaf = setTimeout(() => {
    docRaf = 0
    if (!pendingDoc) return
    doc.value = Object.freeze(pendingDoc.next)
    docKind.value = pendingDoc.kind
    pendingDoc = null
  }, 400)
  clearTimeout(liveTimer)
  liveTimer = setTimeout(() => { docLive.value = false }, 2500)
  if (!panelOpen.value && window.innerWidth > 1100) panelOpen.value = true
}

// ------------------------------------------------------------ 操作
async function newTask() {
  try {
    const c = await api.create()
    convs.value.unshift(c)
    router.push(`/c/${c.id}`)
  } catch (e) { toast(e.message, 'err') }
}

async function send(textArg) {
  const text = (textArg ?? input.value).trim()
  if (!text || sending.value || busy.value || closed.value) return
  sending.value = true
  let cid = id.value
  try {
    if (!cid) {
      const c = await api.create()
      convs.value.unshift(c)
      cid = c.id
      await router.push(`/c/${cid}`)
      await waitOpen(cid)
    }
    const msgId = uid()
    input.value = ''
    tl.value.pushUser(text, S.value.step, true)
    S.value.running = true; S.value.status = 'running'
    scrollBottom(true)
    await api.send(cid, text, msgId)
    patchConv(cid, { status: 'running', running: true })
    loadList()
  } catch (e) {
    S.value.running = false
    S.value.status = conv.value?.status || 'idle'
    input.value = input.value || text
    toast(e.message, 'err')
  } finally { sending.value = false; autosize() }
}
function waitOpen(cid) {
  return new Promise((resolve) => {
    const t0 = Date.now()
    const tick = () => (conv.value?.id === cid || Date.now() - t0 > 8000 ? resolve() : setTimeout(tick, 50))
    tick()
  })
}

async function stop() {
  if (!id.value || stopping.value) return
  stopping.value = true
  // 前端立即停止：结束流式动画与“处理中”状态；后端同步取消并保存检查点
  S.value.running = false
  try {
    const r = await api.stop(id.value)
    if (!r.stopped) S.value.status = conv.value?.status
    patchConv(id.value, { status: 'paused', running: false })
    S.value.status = 'paused'
  } catch (e) { toast(e.message, 'err') } finally { stopping.value = false }
}

async function resume() {
  if (!id.value) return
  try {
    S.value.running = true; S.value.status = 'running'
    await api.resume(id.value)
    patchConv(id.value, { status: 'running', running: true })
  } catch (e) { S.value.running = false; S.value.status = conv.value?.status; toast(e.message, 'err') }
}

async function download() {
  if (!id.value) return
  downloading.value = true
  try { await api.exportDocx(id.value, `${conv.value?.title || '合同'}.docx`) } catch (e) { toast(e.message, 'err') } finally { downloading.value = false }
}

async function rename(c, title) {
  try { await api.rename(c.id, title); patchConv(c.id, { title }) } catch (e) { toast(e.message, 'err') }
}
async function remove(c) {
  try {
    await api.remove(c.id)
    convs.value = convs.value.filter((x) => x.id !== c.id)
    if (c.id === id.value) router.replace('/')
  } catch (e) { toast(e.message, 'err') }
}
function logout() { closeStream(); auth.clear(); router.replace('/login') }

// ------------------------------------------------------------ UI 细节
function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() }
}
function autosize() {
  nextTick(() => {
    const el = textareaEl.value
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  })
}
function focusInput() { nextTick(() => textareaEl.value?.focus()) }
function onScroll() {
  const el = chatEl.value
  if (el) stickBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}
// 滚动合并到每帧一次（流式 token 每秒可达上百个事件，逐个滚动会触发大量布局计算）
let scrollRaf = 0
function scrollBottom(force = false) {
  if (scrollRaf && !force) return
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0
    const el = chatEl.value
    if (el && (force || stickBottom)) el.scrollTop = el.scrollHeight
  })
}
function quickReply(t) { input.value = t; send() }

watch(sideCollapsed, (v) => localStorage.setItem('acc.side', v ? '1' : '0'))
watch(sideW, (v) => localStorage.setItem('acc.sideW', String(v)))
watch(panelW, (v) => localStorage.setItem('acc.panelW', String(v)))
watch(panelOpen, (v) => localStorage.setItem('acc.panel', v ? '1' : '0'))
watch(id, (v) => open(v))
watch(input, autosize)
onMounted(() => { loadList(); loadQuota(); open(id.value) })
onBeforeUnmount(() => { closeStream(); clearTimeout(liveTimer); clearTimeout(docRaf); window.removeEventListener('resize', onResize); window.removeEventListener('acc:quota', onQuota) })
const statusChip = computed(() => ({
  running: ['AI 处理中', 'brand'], waiting_input: ['等待你的回复', 'warn'], paused: ['已暂停', ''], failed: ['运行出错', 'err'],
  completed: ['已完成', 'ok'], closed: ['已关闭', ''], idle: ['未开始', ''],
}[status.value] || ['', '']))
</script>

<template>
  <div class="ws" :class="{ 'panel-open': panelOpen }">
    <Sidebar :items="convs" :active-id="id" :collapsed="sideCollapsed" :loading="listLoading" @new="newTask"
             :style="sideCollapsed ? null : { width: sideW + 'px' }"
             @open="(c) => router.push(`/c/${c.id}`)" @rename="rename" @remove="remove" @logout="logout"
             @profile="router.push('/me')" @toggle="sideCollapsed = !sideCollapsed" />
    <Splitter v-if="!sideCollapsed" v-model="sideW" side="left" :min="200" :max="420" :default-value="264" />

    <section class="main" aria-label="对话">
      <header class="head">
        <div class="title">
          <b class="ell">{{ conv?.title || '新的合同任务' }}</b>
          <span v-if="conv" class="chip" :class="statusChip[1]">{{ statusChip[0] }}</span>
          <span v-if="streamState === 'reconnecting'" class="chip warn" title="实时连接中断，正在自动重连">重连中…</span>
        </div>
        <StepBar v-if="conv" class="sb" :step="S.step" :done="S.stepDone" :running="busy" :status="status" />
        <button v-if="!panelOpen" class="btn sm" @click="panelOpen = true" data-testid="open-panel"><Icon name="doc" />合同预览</button>
      </header>

      <div ref="chatEl" class="chat" @scroll="onScroll">
        <div class="inner">
          <div v-if="!id || (!loading && !S.items.length && ['idle', undefined].includes(conv?.status) && !busy)" class="welcome">
            <div class="hero-ic"><Icon name="sparkle" /></div>
            <h1>今天要起草什么合同？</h1>
            <p>描述你的业务场景与关键条件，AI 将完成需求梳理、模板匹配、大纲、起草、四维审查与修订，全程可视、可随时停止。</p>
            <div class="samples">
              <button v-for="(s, i) in SAMPLES" :key="i" class="sample" @click="input = s; focusInput()">{{ s }}</button>
            </div>
          </div>
          <div v-else-if="loading" class="muted center">加载中…</div>
          <template v-else>
            <template v-for="(it, i) in S.items" :key="i">
              <div v-if="it.type === 'user'" class="umsg"><div class="bubble">{{ it.text }}</div></div>
              <Turn v-else-if="it.type === 'turn' && (it.blocks.length || it.answer || (busy && i === S.items.length - 1))" :turn="it" :live="busy && i === S.items.length - 1" />
              <div v-else-if="it.type === 'notice'" class="notice" :class="it.tone"><Icon :name="it.tone === 'error' ? 'alert' : 'stop'" />{{ it.text }}</div>
            </template>
            <div v-if="busy && (!S.items.length || S.items[S.items.length - 1].type === 'user')" class="pending"><span class="spinner" />正在准备…</div>
            <div v-if="closed" class="done-card">
              <Icon name="check" /><div><b>合同已完成</b><span>本任务已关闭，不再接受输入。可下载 Word，或新建任务继续。</span></div>
              <button class="btn sm primary" @click="download" :disabled="downloading">下载 Word</button>
            </div>
          </template>
        </div>
      </div>

      <footer class="composer">
        <div class="quick" v-if="!busy && !closed && (S.step === 4 || S.step === 8) && status === 'waiting_input'">
          <button class="btn sm" @click="quickReply('确认')" data-testid="confirm"><Icon name="check" />确认{{ S.step === 4 ? '大纲，开始起草' : '终稿' }}</button>
        </div>
        <div v-if="quotaMsg" class="quota-tip err-tip">
          <Icon name="alert" /><span>{{ quotaMsg }}</span>
          <button class="btn sm primary" @click="router.push('/me')">去配置我的 API</button>
          <button class="icon-btn" @click="quotaMsg = ''" aria-label="关闭"><Icon name="x" /></button>
        </div>
        <div class="box" :class="{ disabled: closed }">
          <textarea ref="textareaEl" v-model="input" rows="1" :placeholder="placeholder" :disabled="closed || sending"
                    maxlength="4000" @keydown="onKey" aria-label="输入消息" data-testid="composer" />
          <div class="actions">
            <span class="muted cnt" v-if="input.length > 3000">{{ input.length }}/4000</span>
            <button v-if="canResume" class="btn sm" @click="resume" data-testid="resume"><Icon name="play" />继续生成</button>
            <button v-if="busy" class="send stop" @click="stop" :disabled="stopping" title="停止生成" aria-label="停止生成" data-testid="stop">
              <span v-if="stopping" class="spinner" /><Icon v-else name="stop" />
            </button>
            <button v-else class="send" :disabled="!canSend" @click="send()" title="发送（Enter）" aria-label="发送" data-testid="send">
              <span v-if="sending" class="spinner" /><Icon v-else name="send" />
            </button>
          </div>
        </div>
        <p class="disclaimer">
          <template v-if="usingOwn">正在使用你配置的模型 API · </template>
          <template v-else-if="quota">免费额度：完整生成剩余 <b>{{ quota.free_runs_left }}</b>/{{ quota.free_runs_total }} 次 · Token 剩余 {{ (quota.free_tokens_left / 10000).toFixed(0) }} 万 ·
            <a href="#/me">配置自己的 API</a> · </template>
          AI 生成内容仅供参考，重要合同请经专业律师审核。Enter 发送，Shift+Enter 换行。</p>
      </footer>
    </section>

    <Splitter v-if="panelOpen && wide" v-model="panelW" side="right" :min="360" :max="maxPanel" :default-value="defaultPanel" />
    <DocPanel v-if="panelOpen" class="docpanel" :doc="doc" :prev="prevDoc" :kind="docKind" :title="conv?.title" :live="docLive"
              :style="wide ? { width: panelW + 'px' } : null"
              :can-download="!!doc && !!id" :downloading="downloading" @download="download" @collapse="panelOpen = false" />
  </div>
</template>

<style scoped>
.ws { display: flex; height: 100%; overflow: hidden; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; background: #fff; }
.head { display: flex; align-items: center; gap: 12px; padding: 10px 18px; border-bottom: 1px solid var(--line-2); min-height: 54px; }
.title { display: flex; align-items: center; gap: 8px; min-width: 0; flex: 0 1 auto; max-width: 30%; }
.title .chip { white-space: nowrap; flex: none; }
.ell { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.sb { flex: 1; min-width: 0; justify-content: flex-end; }
.chat { flex: 1; overflow-y: auto; }
.inner { max-width: 820px; margin: 0 auto; padding: 24px 24px 12px; }
.welcome { text-align: center; padding: 9vh 8px 0; }
.hero-ic { width: 52px; height: 52px; margin: 0 auto 18px; border-radius: 16px; background: linear-gradient(135deg, var(--brand), #8b5cf6); color: #fff; display: grid; place-items: center; }
.hero-ic :deep(svg) { width: 26px; height: 26px; }
.welcome h1 { font-size: 26px; margin: 0 0 8px; }
.welcome p { color: var(--ink-2); max-width: 560px; margin: 0 auto 28px; }
.samples { display: grid; gap: 10px; text-align: left; }
.sample { border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 12px 14px; color: var(--ink-2); font-size: 13.5px; line-height: 1.6; text-align: left; }
.sample:hover { border-color: var(--brand-2); color: var(--ink); background: #fafbff; }
.umsg { display: flex; justify-content: flex-end; margin: 6px 0 18px; }
.bubble { max-width: 78%; background: #f2f3f7; border-radius: 16px 16px 4px 16px; padding: 10px 14px; white-space: pre-wrap; word-break: break-word; }
.notice { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 8px 12px; border-radius: 10px; margin: 0 0 16px 42px; background: var(--warn-soft); color: #9a4b00; }
.notice.error { background: var(--err-soft); color: var(--err); }
.notice :deep(svg) { width: 15px; height: 15px; }
.pending { display: flex; align-items: center; gap: 8px; color: var(--muted); margin-left: 42px; font-size: 13px; }
.done-card { display: flex; align-items: center; gap: 12px; margin: 8px 0 16px 42px; padding: 12px 14px; border: 1px solid #c6f0cf; background: var(--ok-soft); border-radius: 12px; }
.done-card > :deep(svg) { color: var(--ok); width: 22px; height: 22px; }
.done-card div { flex: 1; display: flex; flex-direction: column; }
.done-card span { color: var(--ink-2); font-size: 12.5px; }
.center { text-align: center; padding: 40px; }
.muted { color: var(--muted); font-size: 12.5px; }
.composer { padding: 6px 24px 14px; }
.quick { max-width: 820px; margin: 0 auto 8px; display: flex; gap: 8px; }
.box { max-width: 820px; margin: 0 auto; border: 1px solid #dcdfe6; border-radius: 16px; background: #fff; padding: 10px 10px 8px 14px; box-shadow: 0 2px 12px rgba(15, 23, 42, .05); transition: border-color .15s, box-shadow .15s; }
.box:focus-within { border-color: var(--brand-2); box-shadow: 0 0 0 3px rgba(99, 102, 241, .12); }
.box.disabled { background: #f7f8fa; }
textarea { width: 100%; border: 0; outline: none; resize: none; background: transparent; line-height: 1.6; max-height: 200px; min-height: 26px; display: block; }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.send { width: 34px; height: 34px; border-radius: 50%; border: 0; background: var(--brand); color: #fff; display: grid; place-items: center; }
.send:disabled { background: #c9cdd4; opacity: 1; }
.send.stop { background: #1f2329; }
.send :deep(svg) { width: 17px; height: 17px; }
.send.stop :deep(svg) { fill: #fff; stroke: none; width: 13px; height: 13px; }
.disclaimer { text-align: center; color: var(--muted); font-size: 11.5px; margin: 8px 0 0; }
.disclaimer a { color: var(--brand); text-decoration: none; }
.quota-tip { max-width: 820px; margin: 0 auto 8px; display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 12px; font-size: 13px; }
.err-tip { background: var(--warn-soft); color: #9a4b00; }
.quota-tip span { flex: 1; }
.quota-tip :deep(svg) { width: 16px; height: 16px; flex: none; }
.docpanel { width: min(46vw, 760px); flex: none; min-width: 0; }
@media (max-width: 1100px) {
  .docpanel { position: fixed; inset: 0 0 0 auto; width: min(92vw, 720px); z-index: 30; box-shadow: -8px 0 24px rgba(0, 0, 0, .12); }
  .sb { display: none; }
  .title { max-width: none; flex: 1; }
}
</style>
