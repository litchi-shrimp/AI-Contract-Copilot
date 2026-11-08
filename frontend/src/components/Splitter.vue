<script setup>
// 可拖拽分隔条：拖动调整相邻面板宽度；双击恢复默认；键盘 ←/→ 微调（无障碍）
const props = defineProps({ min: { type: Number, default: 200 }, max: { type: Number, default: 1200 },
  modelValue: { type: Number, required: true }, side: { type: String, default: 'left' }, defaultValue: Number })
const emit = defineEmits(['update:modelValue', 'dragging'])

function clamp(v) { return Math.max(props.min, Math.min(props.max, Math.round(v))) }
function down(e) {
  e.preventDefault()
  const startX = e.clientX
  const start = props.modelValue
  emit('dragging', true)
  document.body.classList.add('resizing')
  const move = (ev) => {
    const dx = ev.clientX - startX
    emit('update:modelValue', clamp(props.side === 'left' ? start + dx : start - dx))
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    document.body.classList.remove('resizing')
    emit('dragging', false)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
function key(e) {
  const step = e.shiftKey ? 40 : 12
  if (e.key === 'ArrowLeft') emit('update:modelValue', clamp(props.modelValue + (props.side === 'left' ? -step : step)))
  if (e.key === 'ArrowRight') emit('update:modelValue', clamp(props.modelValue + (props.side === 'left' ? step : -step)))
}
</script>
<template>
  <div class="splitter" role="separator" aria-orientation="vertical" tabindex="0" title="拖动调整宽度，双击恢复默认"
       @pointerdown="down" @keydown="key" @dblclick="defaultValue && emit('update:modelValue', defaultValue)" />
</template>
<style scoped>
.splitter { width: 6px; margin: 0 -3px; flex: none; cursor: col-resize; position: relative; z-index: 5; touch-action: none; }
.splitter::after { content: ""; position: absolute; top: 0; bottom: 0; left: 2px; width: 2px; background: transparent; transition: background .15s; }
.splitter:hover::after, .splitter:focus-visible::after { background: var(--brand-2); }
</style>
