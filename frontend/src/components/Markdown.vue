<script setup>
// 安全 Markdown 渲染（marked + DOMPurify，防 XSS）
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({ text: { type: String, default: '' } })
marked.setOptions({ breaks: true, gfm: true })
const html = computed(() => DOMPurify.sanitize(marked.parse(props.text || ''), { USE_PROFILES: { html: true } }))
</script>
<template>
  <div class="md" v-html="html" />
</template>
<style scoped>
.md :deep(p) { margin: 0 0 8px; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(ol), .md :deep(ul) { margin: 4px 0 8px; padding-left: 22px; }
.md :deep(code) { background: #f2f3f5; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
.md :deep(table) { border-collapse: collapse; margin: 6px 0; }
.md :deep(td), .md :deep(th) { border: 1px solid var(--line); padding: 4px 8px; }
</style>
