<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import { api } from '../lib/api.js'
import { auth } from '../lib/auth.js'
import { toast } from '../lib/toast.js'

const router = useRouter()
const tab = ref('models')
const data = ref(null)
const loading = ref(true)

async function load() {
  try {
    data.value = await api.account()
    auth.set(auth.token.value, data.value.user)
  } catch (e) { toast(e.message, 'err') } finally { loading.value = false }
}
onMounted(load)

const q = computed(() => data.value?.quota || {})
const runsPct = computed(() => q.value.free_runs_total ? q.value.free_runs_used / q.value.free_runs_total : 0)
const tokPct = computed(() => q.value.free_tokens_total ? q.value.free_tokens_used / q.value.free_tokens_total : 0)
const fmt = (n) => (n || 0).toLocaleString()
const wan = (n) => `${+((n || 0) / 10000).toFixed(1)} 万`
const formatName = (id) => (data.value?.formats || []).find((f) => f.id === id)?.name || id
const presetName = (id) => (data.value?.presets || []).find((p) => p.id === id)?.name || id

// ------------------------------------------------------------ 资料
const prof = reactive({ display_name: '', saving: false })
const fileEl = ref(null)
function pickAvatar() { fileEl.value?.click() }
async function onAvatar(e) {
  const f = e.target.files?.[0]
  e.target.value = ''
  if (!f) return
  if (!/^image\/(png|jpeg|webp|gif)$/.test(f.type)) return toast('仅支持 PNG / JPG / WebP / GIF', 'err')
  try {
    const dataUrl = await resizeImage(f, 256)
    const r = await api.updateProfile({ avatar: dataUrl })
    data.value.user = r.user; auth.set(auth.token.value, r.user); toast('头像已更新')
  } catch (err) { toast(err.message, 'err') }
}
// 前端压缩为 256px 正方形 webp/jpeg，控制体积（服务端仍会校验 ≤300KB）
function resizeImage(file, size) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = c.height = size
      const s = Math.min(img.width, img.height)
      c.getContext('2d').drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, size, size)
      resolve(c.toDataURL('image/jpeg', 0.86)); URL.revokeObjectURL(img.src)
    }
    img.onerror = () => reject(new Error('图片读取失败'))
    img.src = URL.createObjectURL(file)
  })
}
async function clearAvatar() {
  const r = await api.updateProfile({ clear_avatar: true }); data.value.user = r.user; auth.set(auth.token.value, r.user)
}
async function saveName() {
  if (!prof.display_name.trim()) return
  prof.saving = true
  try {
    const r = await api.updateProfile({ display_name: prof.display_name.trim() })
    data.value.user = r.user; auth.set(auth.token.value, r.user); prof.display_name = ''; toast('昵称已更新')
  } catch (e) { toast(e.message, 'err') } finally { prof.saving = false }
}

// ------------------------------------------------------------ 密码
const pw = reactive({ old: '', new1: '', new2: '', saving: false, err: '' })
async function savePw() {
  pw.err = ''
  if (pw.new1.length < 8 || !/[A-Za-z]/.test(pw.new1) || !/\d/.test(pw.new1)) { pw.err = '新密码至少 8 位，需包含字母和数字'; return }
  if (pw.new1 !== pw.new2) { pw.err = '两次输入的新密码不一致'; return }
  pw.saving = true
  try {
    await api.changePassword({ old_password: pw.old, new_password: pw.new1 })
    Object.assign(pw, { old: '', new1: '', new2: '' }); toast('密码已修改')
  } catch (e) { pw.err = e.message } finally { pw.saving = false }
}

// ------------------------------------------------------------ 模型配置（类 TRAE：服务商 → 自动填 Base URL/格式 → 模型 → Key → 测试）
const dlg = reactive({ open: false, id: null, provider: 'deepseek', display_name: '', api_format: 'openai_chat', base_url: '',
  model: '', api_key: '', showKey: false, testing: false, test: null, saving: false, err: '' })
const preset = computed(() => (data.value?.presets || []).find((p) => p.id === dlg.provider))
const isCustom = computed(() => dlg.provider === 'custom')
function applyPreset(id) {
  dlg.provider = id
  const p = (data.value?.presets || []).find((x) => x.id === id)
  if (!p) return
  if (id !== 'custom') { dlg.api_format = p.format; dlg.base_url = p.base_url }
  if (!dlg.id) dlg.model = p.models?.[0] || ''
  if (!dlg.id || !dlg.display_name) dlg.display_name = id === 'custom' ? '' : p.name
  dlg.test = null
}
function openNew() {
  Object.assign(dlg, { open: true, id: null, api_key: '', showKey: false, test: null, err: '', display_name: '', model: '' })
  applyPreset('deepseek')
}
function openEdit(m) {
  Object.assign(dlg, { open: true, id: m.id, provider: m.provider, display_name: m.display_name, api_format: m.api_format,
    base_url: m.base_url, model: m.model, api_key: '', showKey: false, test: null, err: '' })
}
function payload() {
  return { provider: dlg.provider, display_name: (dlg.display_name || dlg.model).trim(), api_format: dlg.api_format,
    base_url: dlg.base_url.trim(), model: dlg.model.trim(), api_key: dlg.api_key.trim() || null }
}
function validate() {
  if (!/^https?:\/\/.+/.test(dlg.base_url.trim())) return '请填写正确的 Base URL（http:// 或 https:// 开头）'
  if (!dlg.model.trim()) return '请填写模型名称'
  if (!dlg.id && !dlg.api_key.trim()) return '请填写 API Key'
  return ''
}
async function test() {
  dlg.err = validate(); if (dlg.err) return
  dlg.testing = true; dlg.test = null
  try { dlg.test = await api.testModel(payload(), dlg.id) } catch (e) { dlg.test = { ok: false, error: e.message } } finally { dlg.testing = false }
}
async function save() {
  dlg.err = validate(); if (dlg.err) return
  dlg.saving = true
  try {
    if (dlg.id) await api.updateModel(dlg.id, payload()); else await api.createModel(payload())
    dlg.open = false; toast('模型配置已保存'); await load()
  } catch (e) { dlg.err = e.message } finally { dlg.saving = false }
}
async function activate(m) { await api.activateModel(m.id); await load(); toast(`已切换为「${m.display_name}」`) }
async function useFree() { await api.useFree(); await load(); toast('已切换为平台免费额度') }
async function removeModel(m) {
  if (!confirm(`删除模型配置「${m.display_name}」？`)) return
  await api.deleteModel(m.id); await load()
}
async function quickTest(m) {
  m._testing = true
  try {
    const r = await api.testModel({ provider: m.provider, display_name: m.display_name, api_format: m.api_format,
      base_url: m.base_url, model: m.model, api_key: null }, m.id)
    m.last_test_ok = r.ok ? 1 : 0
    toast(r.ok ? `连接成功 · ${r.latency_ms}ms` : `连接失败：${r.error}`, r.ok ? '' : 'err')
  } finally { m._testing = false }
}
</script>

<template>
  <div class="page">
    <header class="top">
      <button class="btn ghost" @click="router.push('/')" data-testid="back"><Icon name="chev" style="transform:rotate(180deg)" />返回工作台</button>
      <b>个人主页</b><span />
    </header>
    <div v-if="loading" class="muted center">加载中…</div>
    <main v-else-if="data" class="wrap">
      <section class="hero card">
        <div class="av-wrap" @click="pickAvatar" title="更换头像">
          <img v-if="data.user.avatar" :src="data.user.avatar" class="av" alt="头像" />
          <span v-else class="av ph">{{ (data.user.display_name || data.user.username).slice(0, 1).toUpperCase() }}</span>
          <span class="cam">更换</span>
          <input ref="fileEl" type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden @change="onAvatar" data-testid="avatar-input" />
        </div>
        <div class="who">
          <h1>{{ data.user.display_name || data.user.username }}</h1>
          <p class="muted">账号 {{ data.user.username }} · 注册于 {{ new Date(data.user.created_at).toLocaleDateString() }}</p>
          <button v-if="data.user.avatar" class="btn sm ghost" @click="clearAvatar">移除头像</button>
        </div>
        <div class="quota">
          <div class="qrow"><span>免费完整生成</span><b>{{ q.free_runs_left }} / {{ q.free_runs_total }} 次剩余</b></div>
          <div class="bar"><i :style="{ width: Math.min(100, runsPct * 100) + '%' }" /></div>
          <div class="qrow"><span>免费 Token</span><b>{{ wan(q.free_tokens_left) }} / {{ wan(q.free_tokens_total) }}</b></div>
          <div class="bar"><i :style="{ width: Math.min(100, tokPct * 100) + '%' }" /></div>
          <p class="muted small">平台为每位用户提供 {{ q.free_runs_total }} 次免费完整合同生成（约 {{ wan(q.tokens_per_run_estimate) }} tokens/次，
            总额度约可完成 {{ Math.round(q.free_tokens_total / q.tokens_per_run_estimate) }} 次）。用完后请配置自己的模型 API。</p>
        </div>
      </section>

      <nav class="tabs" role="tablist">
        <button :class="{ on: tab === 'models' }" @click="tab = 'models'" role="tab">模型配置</button>
        <button :class="{ on: tab === 'profile' }" @click="tab = 'profile'" role="tab">基本资料</button>
        <button :class="{ on: tab === 'security' }" @click="tab = 'security'" role="tab">账号安全</button>
      </nav>

      <!-- 模型配置 -->
      <section v-if="tab === 'models'" class="card">
        <div class="sec-h">
          <div><h2>模型配置</h2><p class="muted">使用你自己的模型服务（API Key 加密存储，仅用于你的任务）。启用后不再消耗平台免费额度。</p></div>
          <button class="btn primary" @click="openNew" data-testid="add-model"><Icon name="plus" />添加模型</button>
        </div>
        <div class="mlist">
          <div class="mitem" :class="{ active: !data.active_model_id }">
            <div class="mic free"><Icon name="sparkle" /></div>
            <div class="minfo"><b>平台免费模型</b><span class="muted">deepseek-flash · 剩余 {{ q.free_runs_left }} 次 / {{ wan(q.free_tokens_left) }} tokens</span></div>
            <span v-if="!data.active_model_id" class="chip brand">使用中</span>
            <button v-else class="btn sm" @click="useFree" :disabled="q.exhausted">{{ q.exhausted ? '额度已用完' : '切换使用' }}</button>
          </div>
          <div v-for="m in data.models" :key="m.id" class="mitem" :class="{ active: m.is_active }" :data-testid="'model-' + m.id">
            <div class="mic">{{ presetName(m.provider).slice(0, 1) }}</div>
            <div class="minfo">
              <b>{{ m.display_name }}</b>
              <span class="muted">{{ presetName(m.provider) }} · {{ m.model }} · {{ formatName(m.api_format) }} · Key {{ m.api_key_hint }}</span>
              <span class="muted small">{{ m.base_url }}</span>
            </div>
            <span v-if="m.last_test_ok === 1" class="chip ok">连接正常</span>
            <span v-else-if="m.last_test_ok === 0" class="chip err">连接失败</span>
            <span v-if="m.is_active" class="chip brand">使用中</span>
            <div class="mact">
              <button class="btn sm" @click="quickTest(m)" :disabled="m._testing"><span v-if="m._testing" class="spinner" />测试</button>
              <button v-if="!m.is_active" class="btn sm" @click="activate(m)">启用</button>
              <button class="icon-btn" @click="openEdit(m)" title="编辑" aria-label="编辑"><Icon name="edit" /></button>
              <button class="icon-btn danger" @click="removeModel(m)" title="删除" aria-label="删除"><Icon name="trash" /></button>
            </div>
          </div>
          <p v-if="!data.models.length" class="muted empty">还没有自定义模型。支持 OpenAI Chat Completions / OpenAI Responses / Anthropic Messages / Gemini 四种协议，
            预置 DeepSeek、OpenAI、Anthropic、Gemini、通义千问、Kimi、智谱、豆包、硅基流动、OpenRouter、Ollama 等服务商，也可完全自定义。</p>
        </div>
      </section>

      <!-- 基本资料 -->
      <section v-if="tab === 'profile'" class="card form">
        <h2>基本资料</h2>
        <label class="field"><span>昵称</span>
          <div class="row"><input v-model="prof.display_name" :placeholder="data.user.display_name" maxlength="32" />
            <button class="btn primary" @click="saveName" :disabled="prof.saving || !prof.display_name.trim()">保存</button></div></label>
        <label class="field"><span>头像</span>
          <div class="row"><button class="btn" @click="pickAvatar">上传图片</button><span class="muted small">PNG / JPG / WebP，自动裁剪为正方形</span></div></label>
      </section>

      <!-- 安全 -->
      <section v-if="tab === 'security'" class="card form">
        <h2>修改密码</h2>
        <label class="field"><span>原密码</span><input v-model="pw.old" type="password" autocomplete="current-password" maxlength="72" data-testid="old-pw" /></label>
        <label class="field"><span>新密码</span><input v-model="pw.new1" type="password" autocomplete="new-password" maxlength="72" placeholder="至少 8 位，包含字母和数字" data-testid="new-pw" /></label>
        <label class="field"><span>确认新密码</span><input v-model="pw.new2" type="password" autocomplete="new-password" maxlength="72" data-testid="new-pw2" /></label>
        <p v-if="pw.err" class="err">{{ pw.err }}</p>
        <button class="btn primary" @click="savePw" :disabled="pw.saving || !pw.old" data-testid="save-pw">修改密码</button>
      </section>
    </main>

    <!-- 添加 / 编辑模型（TRAE 风格） -->
    <div v-if="dlg.open" class="mask" @click.self="dlg.open = false">
      <div class="dialog" role="dialog" aria-modal="true" aria-label="模型配置">
        <header><b>{{ dlg.id ? '编辑模型' : '添加模型' }}</b><button class="icon-btn" @click="dlg.open = false" aria-label="关闭"><Icon name="x" /></button></header>
        <div class="body">
          <label class="field"><span>服务商</span>
            <div class="providers">
              <button v-for="p in data.presets" :key="p.id" class="prov" :class="{ on: dlg.provider === p.id }" @click="applyPreset(p.id)"
                      :data-testid="'prov-' + p.id">{{ p.name }}</button>
            </div></label>
          <div class="grid2">
            <label class="field"><span>API 格式</span>
              <select v-model="dlg.api_format" :disabled="!isCustom" data-testid="api-format">
                <option v-for="f in data.formats" :key="f.id" :value="f.id">{{ f.name }}</option>
              </select>
              <em v-if="!isCustom" class="hint">由服务商决定；如需修改请选择“自定义”</em></label>
            <label class="field"><span>显示名称</span><input v-model="dlg.display_name" maxlength="64" placeholder="用于区分不同配置" /></label>
          </div>
          <label class="field"><span>Base URL</span>
            <input v-model="dlg.base_url" :readonly="!isCustom && !!preset?.base_url" placeholder="https://api.example.com/v1" data-testid="base-url" />
            <em class="hint">{{ { openai_chat: '将请求 {Base URL}/chat/completions', openai_responses: '将请求 {Base URL}/responses',
              anthropic: '将请求 {Base URL}/v1/messages', gemini: '将请求 {Base URL}/v1beta/models/{模型}:streamGenerateContent' }[dlg.api_format] }}</em></label>
          <label class="field"><span>模型</span>
            <input v-model="dlg.model" list="model-sugg" placeholder="输入或选择模型 ID" maxlength="200" data-testid="model-id" />
            <datalist id="model-sugg"><option v-for="m in preset?.models || []" :key="m" :value="m" /></datalist>
            <div v-if="preset?.models?.length" class="sugg"><button v-for="m in preset.models" :key="m" class="chip" @click="dlg.model = m">{{ m }}</button></div></label>
          <label class="field"><span>API Key</span>
            <div class="pw"><input v-model="dlg.api_key" :type="dlg.showKey ? 'text' : 'password'" autocomplete="off"
                                   :placeholder="dlg.id ? '留空则保持原 Key 不变' : '请输入 API Key'" data-testid="api-key" />
              <button class="eye" @click="dlg.showKey = !dlg.showKey">{{ dlg.showKey ? '隐藏' : '显示' }}</button></div></label>
          <div v-if="dlg.test" class="test" :class="dlg.test.ok ? 'ok' : 'bad'" data-testid="test-result">
            <Icon :name="dlg.test.ok ? 'check' : 'alert'" />
            <span v-if="dlg.test.ok">连接成功 · {{ dlg.test.latency_ms }} ms · 模型回复：{{ dlg.test.reply }}</span>
            <span v-else>连接失败：{{ dlg.test.error }}</span>
          </div>
          <p v-if="dlg.err" class="err">{{ dlg.err }}</p>
        </div>
        <footer>
          <button class="btn" @click="test" :disabled="dlg.testing" data-testid="test-btn"><span v-if="dlg.testing" class="spinner" />测试连接</button>
          <span />
          <button class="btn ghost" @click="dlg.open = false">取消</button>
          <button class="btn primary" @click="save" :disabled="dlg.saving" data-testid="save-model">保存</button>
        </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { height: 100%; overflow: auto; background: var(--bg); }
.top { position: sticky; top: 0; z-index: 5; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; padding: 10px 18px; background: #fff; border-bottom: 1px solid var(--line); }
.wrap { max-width: 920px; margin: 0 auto; padding: 24px 20px 60px; }
.card { background: #fff; border: 1px solid var(--line); border-radius: 16px; padding: 22px; box-shadow: var(--shadow); margin-bottom: 16px; }
.hero { display: grid; grid-template-columns: auto 1fr 300px; gap: 22px; align-items: center; }
.av-wrap { position: relative; width: 84px; height: 84px; cursor: pointer; border-radius: 50%; overflow: hidden; }
.av { width: 84px; height: 84px; border-radius: 50%; object-fit: cover; display: grid; place-items: center; }
.av.ph { background: #c7d2fe; color: #3730a3; font-size: 32px; font-weight: 700; }
.cam { position: absolute; inset: auto 0 0 0; background: rgba(0, 0, 0, .5); color: #fff; font-size: 12px; text-align: center; padding: 3px 0; opacity: 0; transition: opacity .15s; }
.av-wrap:hover .cam { opacity: 1; }
.who h1 { margin: 0 0 4px; font-size: 22px; }
.who p { margin: 0 0 6px; }
.quota { background: var(--brand-soft); border-radius: 12px; padding: 12px 14px; }
.qrow { display: flex; justify-content: space-between; font-size: 13px; margin-top: 4px; }
.bar { height: 6px; background: #dfe3ff; border-radius: 99px; overflow: hidden; margin: 5px 0 6px; }
.bar i { display: block; height: 100%; background: linear-gradient(90deg, var(--brand), #8b5cf6); }
.tabs { display: inline-flex; background: var(--line-2); border-radius: 10px; padding: 3px; margin: 4px 0 14px; }
.tabs button { border: 0; background: transparent; padding: 6px 18px; border-radius: 8px; color: var(--ink-2); }
.tabs button.on { background: #fff; color: var(--ink); box-shadow: 0 1px 2px rgba(0, 0, 0, .08); font-weight: 600; }
h2 { margin: 0 0 4px; font-size: 17px; }
.sec-h { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.sec-h p { margin: 0; }
.mlist { display: grid; gap: 10px; }
.mitem { display: flex; align-items: center; gap: 12px; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }
.mitem.active { border-color: #c7d2fe; background: #fafbff; }
.mic { width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; background: #eef0f5; font-weight: 700; color: var(--ink-2); flex: none; }
.mic.free { background: linear-gradient(135deg, var(--brand), #8b5cf6); color: #fff; }
.mic :deep(svg) { width: 18px; height: 18px; }
.minfo { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.minfo span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mact { display: flex; align-items: center; gap: 6px; }
.icon-btn.danger { color: var(--err); }
.empty { padding: 8px 2px; line-height: 1.7; }
.form { max-width: 560px; }
.field { display: block; margin: 14px 0; }
.field > span { display: block; font-size: 13px; color: var(--ink-2); margin-bottom: 6px; }
.field input, .field select { width: 100%; height: 38px; border: 1px solid var(--line); border-radius: 10px; padding: 0 12px; background: #fff; }
.field input:focus, .field select:focus { outline: none; border-color: var(--brand-2); box-shadow: 0 0 0 3px rgba(99, 102, 241, .15); }
.field input[readonly], .field select:disabled { background: #f7f8fa; color: var(--ink-2); }
.row { display: flex; gap: 8px; align-items: center; }
.hint { display: block; font-style: normal; color: var(--muted); font-size: 12px; margin-top: 4px; }
.err { background: var(--err-soft); color: var(--err); padding: 8px 12px; border-radius: 8px; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.small { font-size: 12px; }
.center { text-align: center; padding: 60px; }
.mask { position: fixed; inset: 0; background: rgba(15, 23, 42, .35); display: grid; place-items: center; z-index: 50; padding: 20px; }
.dialog { width: min(640px, 100%); max-height: 92vh; display: flex; flex-direction: column; background: #fff; border-radius: 16px; box-shadow: 0 20px 60px rgba(0, 0, 0, .2); }
.dialog header, .dialog footer { display: flex; align-items: center; gap: 8px; padding: 14px 18px; }
.dialog header { justify-content: space-between; border-bottom: 1px solid var(--line-2); }
.dialog footer { border-top: 1px solid var(--line-2); }
.dialog footer > span { flex: 1; }
.dialog .body { padding: 4px 18px 10px; overflow: auto; }
.providers { display: flex; flex-wrap: wrap; gap: 6px; }
.prov { border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 5px 10px; font-size: 13px; color: var(--ink-2); }
.prov:hover { border-color: #c7d2fe; }
.prov.on { border-color: var(--brand); color: var(--brand); background: var(--brand-soft); font-weight: 600; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.sugg { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.sugg .chip { border: 0; cursor: pointer; }
.pw { position: relative; }
.eye { position: absolute; right: 6px; top: 4px; height: 30px; border: 0; background: transparent; color: var(--muted); font-size: 12px; padding: 0 8px; border-radius: 6px; }
.test { display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px; border-radius: 10px; font-size: 13px; word-break: break-all; }
.test.ok { background: var(--ok-soft); color: #0e7a2a; }
.test.bad { background: var(--err-soft); color: var(--err); }
.test :deep(svg) { width: 16px; height: 16px; margin-top: 2px; flex: none; }
@media (max-width: 820px) { .hero { grid-template-columns: auto 1fr; } .quota { grid-column: 1 / -1; } .grid2 { grid-template-columns: 1fr; } }
</style>
