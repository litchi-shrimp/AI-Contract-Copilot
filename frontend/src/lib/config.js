// 后端地址解析优先级：public/config.js（运行时）> VITE_API_BASE（构建时）> 同域
export function apiBase() {
  const rt = (window.__APP_CONFIG__ && window.__APP_CONFIG__.apiBase) || ''
  const bt = import.meta.env.VITE_API_BASE || ''
  return (rt || bt || '').replace(/\/+$/, '')
}
