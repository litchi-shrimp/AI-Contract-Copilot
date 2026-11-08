import { apiBase } from './config.js'
import { auth } from './auth.js'

/**
 * SSE 客户端（基于 fetch 流，可携带 Authorization 头）
 * - 自动重连（指数退避，上限 10s），重连时携带 Last-Event-ID，后端补发断线期间的事件
 * - close() 立即中止读取
 */
export function openStream(conversationId, { onEvent, onState, lastEventId = null }) {
  let closed = false
  let ctrl = null
  let last = lastEventId
  let backoff = 500

  async function loop() {
    while (!closed) {
      ctrl = new AbortController()
      try {
        onState?.('connecting')
        const headers = { Authorization: `Bearer ${auth.token.value}`, Accept: 'text/event-stream' }
        if (last != null) headers['Last-Event-ID'] = String(last)
        const res = await fetch(`${apiBase()}/api/conversations/${conversationId}/events`, { headers, signal: ctrl.signal })
        if (res.status === 401 || res.status === 404) { onState?.('closed'); return }
        if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`)
        onState?.('open')
        backoff = 500
        const reader = res.body.getReader()
        const dec = new TextDecoder()
        let buf = ''
        while (!closed) {
          const { value, done } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          let idx
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const chunk = buf.slice(0, idx)
            buf = buf.slice(idx + 2)
            let data = ''
            let id = null
            for (const line of chunk.split('\n')) {
              if (line.startsWith('data: ')) data += line.slice(6)
              else if (line.startsWith('id: ')) id = Number(line.slice(4))
            }
            if (!data) continue
            try {
              const ev = JSON.parse(data)
              if (id != null) last = id
              onEvent(ev)
            } catch { /* 忽略损坏的帧 */ }
          }
        }
      } catch (e) {
        if (closed) return
      }
      if (closed) return
      onState?.('reconnecting')
      await new Promise((r) => setTimeout(r, backoff))
      backoff = Math.min(backoff * 2, 10000)
    }
  }
  loop()
  return {
    close() { closed = true; ctrl?.abort(); onState?.('closed') },
    get lastEventId() { return last },
  }
}
