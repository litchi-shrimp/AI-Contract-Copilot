import { reactive } from 'vue'

export const toasts = reactive([])
let n = 0
export function toast(text, tone = '') {
  const id = ++n
  toasts.push({ id, text, tone })
  setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id)
    if (i >= 0) toasts.splice(i, 1)
  }, tone === 'err' ? 4500 : 2600)
}
