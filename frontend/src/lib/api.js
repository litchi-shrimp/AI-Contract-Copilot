import { apiBase } from './config.js'
import { auth } from './auth.js'

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

// 统一请求：超时、重试（仅幂等 GET 与网络错误）、401 自动登出
export async function request(path, { method = 'GET', body, timeout = 20000, retries, raw = false } = {}) {
  const maxRetries = retries ?? (method === 'GET' ? 2 : 0)
  let lastErr
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), timeout)
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (auth.token.value) headers.Authorization = `Bearer ${auth.token.value}`
      const res = await fetch(apiBase() + path, {
        method, headers, body: body ? JSON.stringify(body) : undefined, signal: ctrl.signal,
      })
      clearTimeout(timer)
      if (res.status === 401 && !path.startsWith('/api/auth/')) {
        auth.clear()
        location.hash = '#/login'
        throw new ApiError(401, '登录已过期，请重新登录')
      }
      if (raw) {
        if (!res.ok) throw new ApiError(res.status, await errText(res))
        return res
      }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        if (res.status === 402) window.dispatchEvent(new CustomEvent('acc:quota', { detail: detail(data) }))
        throw new ApiError(res.status, detail(data) || `请求失败（${res.status}）`)
      }
      return data
    } catch (e) {
      clearTimeout(timer)
      lastErr = e instanceof ApiError ? e : new ApiError(0, e.name === 'AbortError' ? '请求超时' : '网络异常，请检查连接')
      if (e instanceof ApiError || attempt === maxRetries) throw lastErr
      await new Promise((r) => setTimeout(r, 600 * (attempt + 1)))
    }
  }
  throw lastErr
}

function detail(d) {
  if (!d) return ''
  if (typeof d.detail === 'string') return d.detail
  if (Array.isArray(d.detail)) return d.detail.map((x) => x.msg).join('；')
  return ''
}

async function errText(res) {
  try { return detail(await res.json()) || `请求失败（${res.status}）` } catch { return `请求失败（${res.status}）` }
}

export const api = {
  register: (b) => request('/api/auth/register', { method: 'POST', body: b }),
  login: (b) => request('/api/auth/login', { method: 'POST', body: b }),
  me: () => request('/api/auth/me'),
  list: () => request('/api/conversations'),
  create: (title) => request('/api/conversations', { method: 'POST', body: { title } }),
  get: (id) => request(`/api/conversations/${id}`),
  rename: (id, title) => request(`/api/conversations/${id}`, { method: 'PATCH', body: { title } }),
  remove: (id) => request(`/api/conversations/${id}`, { method: 'DELETE' }),
  send: (id, text, clientMsgId) =>
    request(`/api/conversations/${id}/messages`, { method: 'POST', body: { text, client_msg_id: clientMsgId }, retries: 2 }),
  stop: (id) => request(`/api/conversations/${id}/stop`, { method: 'POST', timeout: 15000, retries: 1 }),
  resume: (id) => request(`/api/conversations/${id}/resume`, { method: 'POST' }),
  account: () => request('/api/account'),
  updateProfile: (b) => request('/api/account/profile', { method: 'PATCH', body: b }),
  changePassword: (b) => request('/api/account/password', { method: 'POST', body: b }),
  createModel: (b) => request('/api/account/models', { method: 'POST', body: b }),
  updateModel: (id, b) => request(`/api/account/models/${id}`, { method: 'PATCH', body: b }),
  deleteModel: (id) => request(`/api/account/models/${id}`, { method: 'DELETE' }),
  activateModel: (id) => request(`/api/account/models/${id}/activate`, { method: 'POST' }),
  useFree: () => request('/api/account/models/use-free', { method: 'POST' }),
  testModel: (b, modelId) => request(`/api/account/models/test${modelId ? `?model_id=${modelId}` : ''}`, { method: 'POST', body: b, timeout: 45000 }),
  async exportDocx(id, filename) {
    const res = await request(`/api/conversations/${id}/export.docx`, { raw: true, timeout: 30000 })
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = filename
    document.body.appendChild(a)
    a.click()
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove() }, 1000)
  },
}

export function uid() {
  return (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/-/g, '').slice(0, 32)
}
