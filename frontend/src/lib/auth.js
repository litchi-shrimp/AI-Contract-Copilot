import { ref } from 'vue'

const KEY = 'acc.token'
const UKEY = 'acc.user'

export const auth = {
  token: ref(localStorage.getItem(KEY) || ''),
  user: ref(JSON.parse(localStorage.getItem(UKEY) || 'null')),
  set(token, user) {
    this.token.value = token
    this.user.value = user
    localStorage.setItem(KEY, token)
    localStorage.setItem(UKEY, JSON.stringify(user))
  },
  clear() {
    this.token.value = ''
    this.user.value = null
    localStorage.removeItem(KEY)
    localStorage.removeItem(UKEY)
  },
}
