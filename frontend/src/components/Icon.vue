<script setup>
// 线性图标（内联 SVG，不依赖图标字体/外部资源）
defineProps({ name: { type: String, required: true } })
const P = {
  plus: 'M12 5v14M5 12h14',
  send: 'M5 12h14M13 6l6 6-6 6',
  stop: 'M7 7h10v10H7z',
  play: 'M8 5l11 7-11 7z',
  doc: 'M7 3h7l5 5v13H7zM14 3v5h5M9.5 13h6M9.5 17h6',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  chev: 'M9 6l6 6-6 6',
  chevDown: 'M6 9l6 6 6-6',
  panel: 'M4 5h16v14H4zM14 5v14',
  panelClose: 'M4 5h16v14H4zM14 5v14M9 10l-2 2 2 2',
  trash: 'M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  edit: 'M4 20h4l11-11-4-4L4 16zM14 6l4 4',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11',
  brain: 'M12 5a3 3 0 0 0-5.6 1.5A3 3 0 0 0 4 11a3 3 0 0 0 2 5.2A3 3 0 0 0 12 18zM12 5a3 3 0 0 1 5.6 1.5A3 3 0 0 1 20 11a3 3 0 0 1-2 5.2A3 3 0 0 1 12 18zM12 5v13',
  tool: 'M14.5 6.5a4 4 0 0 0-5 5L4 17l3 3 5.5-5.5a4 4 0 0 0 5-5L15 12l-3-3z',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4',
  check: 'M5 12l5 5 9-10',
  x: 'M6 6l12 12M18 6L6 18',
  alert: 'M12 4l9 16H3zM12 10v4M12 17v.5',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  sparkle: 'M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2zM19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z',
  menu: 'M4 7h16M4 12h16M4 17h16',
  layers: 'M12 4l8 4-8 4-8-4zM4 12l8 4 8-4M4 16l8 4 8-4',
  clock: 'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 8v4l3 2',
}
</script>
<template>
  <svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path :d="P[name] || ''" /></svg>
</template>
