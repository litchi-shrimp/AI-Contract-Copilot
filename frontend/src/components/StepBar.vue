<script setup>
import { STEP_NAMES } from '../lib/timeline.js'
defineProps({ step: Number, done: Array, running: Boolean, status: String })
</script>
<template>
  <ol class="steps" aria-label="生成进度">
    <li v-for="n in 8" :key="n" :class="{ done: done?.includes(n), cur: n === step && !done?.includes(n), live: n === step && running }">
      <span class="b">{{ done?.includes(n) ? '✓' : n }}</span><span class="l">{{ STEP_NAMES[n] }}</span>
    </li>
  </ol>
</template>
<style scoped>
.steps { display: flex; list-style: none; margin: 0; padding: 0; gap: 2px; overflow-x: auto; scrollbar-width: none; }
@media (max-width: 1800px) { .steps li:not(.cur) .l { display: none; } }
.steps li { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--muted); padding: 3px 8px 3px 4px; border-radius: 999px; white-space: nowrap; flex: none; }
.b { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; font-size: 11px; background: var(--line-2); color: var(--muted); }
li.done { color: var(--ink-2); } li.done .b { background: var(--brand); color: #fff; }
li.cur { color: var(--brand); background: var(--brand-soft); } li.cur .b { background: #fff; color: var(--brand); box-shadow: inset 0 0 0 1.5px var(--brand); }
li.live .b { animation: pulse 1.4s infinite; }
@keyframes pulse { 50% { box-shadow: inset 0 0 0 1.5px var(--brand), 0 0 0 4px rgba(99, 102, 241, .18); } }
</style>
