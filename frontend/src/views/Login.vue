<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../lib/api.js'
import { auth } from '../lib/auth.js'
import Icon from '../components/Icon.vue'

const router = useRouter()
const route = useRoute()
const mode = ref('login')
const username = ref('')
const password = ref('')
const password2 = ref('')
const loading = ref(false)
const error = ref('')
const showPw = ref(false)

const pwHint = computed(() => {
  const p = password.value
  if (!p) return ''
  const ok = p.length >= 8 && /[A-Za-z]/.test(p) && /\d/.test(p)
  return ok ? '' : '至少 8 位，需同时包含字母和数字'
})

function validate() {
  if (!/^[A-Za-z0-9_\u4e00-\u9fa5]{3,32}$/.test(username.value)) return '用户名需为 3-32 位字母、数字、下划线或中文'
  if (mode.value === 'register') {
    if (pwHint.value) return '密码' + pwHint.value
    if (password.value !== password2.value) return '两次输入的密码不一致'
  } else if (!password.value) return '请输入密码'
  return ''
}

async function submit() {
  error.value = validate()
  if (error.value) return
  loading.value = true
  try {
    const fn = mode.value === 'login' ? api.login : api.register
    const res = await fn({ username: username.value.trim(), password: password.value })
    auth.set(res.token, res.user)
    router.replace(route.query.next || '/')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function switchMode(m) {
  mode.value = m
  error.value = ''
  password2.value = ''
}
</script>

<template>
  <div class="auth">
    <section class="brand-side" aria-hidden="true">
      <div class="logo"><Icon name="doc" /></div>
      <h1>AI Contract Copilot</h1>
      <p class="tag">多智能体协作 · 从需求到终稿的合同生成</p>
      <ul class="feats">
        <li><Icon name="layers" /><div><b>8 步流水线</b><span>需求提取 → 模板匹配 → 大纲 → 起草 → 四维审查 → 修订 → 确认</span></div></li>
        <li><Icon name="shield" /><div><b>四维审查</b><span>完整性 · 一致性 · 合法性 · 需求符合度并行审查</span></div></li>
        <li><Icon name="sparkle" /><div><b>实时可视</b><span>思考过程、工具调用与合同增量修改实时呈现</span></div></li>
        <li><Icon name="clock" /><div><b>随时停止与继续</b><span>检查点自动保存，中断后从断点恢复</span></div></li>
      </ul>
    </section>
    <main class="form-side">
      <form class="card" @submit.prevent="submit" novalidate>
        <div class="tabs" role="tablist">
          <button type="button" role="tab" :aria-selected="mode === 'login'" :class="{ on: mode === 'login' }" @click="switchMode('login')">登录</button>
          <button type="button" role="tab" :aria-selected="mode === 'register'" :class="{ on: mode === 'register' }" @click="switchMode('register')">注册</button>
        </div>
        <h2>{{ mode === 'login' ? '欢迎回来' : '创建账号' }}</h2>
        <p class="sub">{{ mode === 'login' ? '登录后继续你的合同任务' : '注册后即可开始生成合同' }}</p>

        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="3-32 位字母、数字或中文" maxlength="32" data-testid="username" />
        </label>
        <label class="field">
          <span>密码</span>
          <div class="pw">
            <input v-model="password" :type="showPw ? 'text' : 'password'" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
                   placeholder="请输入密码" maxlength="72" data-testid="password" />
            <button type="button" class="eye" @click="showPw = !showPw" :aria-label="showPw ? '隐藏密码' : '显示密码'">{{ showPw ? '隐藏' : '显示' }}</button>
          </div>
          <em v-if="mode === 'register' && pwHint" class="hint">{{ pwHint }}</em>
        </label>
        <label v-if="mode === 'register'" class="field">
          <span>确认密码</span>
          <input v-model="password2" :type="showPw ? 'text' : 'password'" autocomplete="new-password" placeholder="再次输入密码" maxlength="72" data-testid="password2" />
        </label>

        <p v-if="error" class="err" role="alert">{{ error }}</p>
        <button class="btn primary submit" :disabled="loading" data-testid="submit">
          <span v-if="loading" class="spinner" />{{ mode === 'login' ? '登录' : '注册并登录' }}
        </button>
        <p class="foot">登录即表示同意仅将本系统用于合法的合同起草用途。生成内容仅供参考，请经专业律师审核后使用。</p>
      </form>
    </main>
  </div>
</template>

<style scoped>
.auth { display: grid; grid-template-columns: 1.05fr 1fr; min-height: 100%; }
.brand-side { background: radial-gradient(1200px 600px at -10% -20%, #6366f1 0%, transparent 60%), linear-gradient(160deg, #1e1b4b, #312e81 55%, #3730a3);
  color: #fff; padding: 72px 64px; display: flex; flex-direction: column; justify-content: center; }
.logo { width: 52px; height: 52px; border-radius: 14px; background: rgba(255, 255, 255, .14); display: grid; place-items: center; margin-bottom: 22px; }
.logo :deep(svg) { width: 28px; height: 28px; }
.brand-side h1 { font-size: 34px; margin: 0 0 8px; letter-spacing: -.01em; }
.tag { opacity: .82; margin: 0 0 40px; font-size: 15px; }
.feats { list-style: none; padding: 0; margin: 0; display: grid; gap: 18px; max-width: 460px; }
.feats li { display: flex; gap: 14px; align-items: flex-start; }
.feats :deep(svg) { width: 22px; height: 22px; margin-top: 2px; opacity: .9; }
.feats b { display: block; font-size: 15px; }
.feats span { opacity: .75; font-size: 13px; }
.form-side { display: grid; place-items: center; padding: 32px; }
.card { width: 100%; max-width: 380px; }
.tabs { display: inline-flex; background: var(--line-2); border-radius: 10px; padding: 3px; margin-bottom: 26px; }
.tabs button { border: 0; background: transparent; padding: 6px 20px; border-radius: 8px; color: var(--ink-2); }
.tabs button.on { background: #fff; color: var(--ink); box-shadow: 0 1px 2px rgba(0, 0, 0, .08); font-weight: 600; }
h2 { margin: 0; font-size: 24px; }
.sub { color: var(--muted); margin: 4px 0 24px; }
.field { display: block; margin-bottom: 16px; }
.field > span { display: block; font-size: 13px; color: var(--ink-2); margin-bottom: 6px; }
.field input { width: 100%; height: 42px; border: 1px solid var(--line); border-radius: 10px; padding: 0 12px; background: #fff; transition: border-color .15s, box-shadow .15s; }
.field input:focus { outline: none; border-color: var(--brand-2); box-shadow: 0 0 0 3px rgba(99, 102, 241, .15); }
.pw { position: relative; }
.eye { position: absolute; right: 6px; top: 6px; height: 30px; border: 0; background: transparent; color: var(--muted); font-size: 12px; padding: 0 8px; border-radius: 6px; }
.eye:hover { background: var(--line-2); }
.hint { display: block; font-style: normal; color: var(--warn); font-size: 12px; margin-top: 4px; }
.err { background: var(--err-soft); color: var(--err); padding: 8px 12px; border-radius: 8px; font-size: 13px; margin: 0 0 12px; }
.submit { width: 100%; height: 42px; font-size: 15px; }
.foot { color: var(--muted); font-size: 12px; margin-top: 18px; }
@media (max-width: 860px) { .auth { grid-template-columns: 1fr; } .brand-side { display: none; } }
</style>
