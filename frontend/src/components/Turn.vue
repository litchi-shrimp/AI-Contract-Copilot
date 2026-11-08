<script setup>
// AI 回合：步骤 / 思考（默认折叠）/ 工具调用 / 检索 / 并行进度 / 回答
import { computed } from 'vue'
import Icon from './Icon.vue'
import Markdown from './Markdown.vue'
import { agentLabel, STEP_NAMES, toolLabel } from '../lib/timeline.js'

const props = defineProps({ turn: Object, live: Boolean })

const visible = computed(() => props.turn.blocks.filter((b) => !(b.kind === 'think' && !b.text.trim()) && !(b.kind === 'reason' && !b.text.trim())))

function paramsPreview(p) {
  if (!p) return ''
  try {
    const loc = p.locator || p.locators
    const parts = []
    if (loc) parts.push(`位置 ${[].concat(loc).slice(0, 6).join('、')}${[].concat(loc).length > 6 ? ' …' : ''}`)
    if (p.positions) parts.push([].concat(p.positions).slice(0, 3).map((x) => ({ after: '之后', before: '之前', child: '子级' }[x] || x)).join('/'))
    if (p.query) parts.push(`“${p.query}”`)
    if (p.target) parts.push(p.target === 'header' ? '合同首部' : '合同尾部')
    return parts.join(' · ')
  } catch { return '' }
}
const statusText = { ok: '成功', partial: '部分成功', rejected: '已拦截', error: '失败', timeout: '超时', running: '执行中', stopped: '已中断' }
const levelTone = { 高: 'err', 中: 'warn', 低: '' }
</script>

<template>
  <div class="turn">
    <div class="avatar" aria-hidden="true"><Icon name="sparkle" /></div>
    <div class="body">
      <template v-for="(b, i) in visible" :key="i">
        <div v-if="b.kind === 'step'" class="step">
          <span class="dot" :class="{ done: b.done, stopped: b.interrupted && !b.done }"><Icon v-if="b.done" name="check" /><Icon v-else-if="b.interrupted" name="stop" /><span v-else class="spinner" /></span>
          <b>第 {{ b.step }} 步 · {{ b.text || STEP_NAMES[b.step] }}</b>
          <span v-if="b.interrupted && !b.done" class="chip">已中断</span>
          <span v-if="b.resumed" class="chip">从检查点恢复</span>
          <span v-if="b.done && b.duration" class="muted">{{ b.duration }}s</span>
        </div>

        <details v-else-if="b.kind === 'reason' || b.kind === 'think'" class="think" :open="b.open" @toggle="b.open = $event.target.open">
          <summary>
            <Icon name="brain" />
            <span>{{ b.done ? (b.kind === 'reason' ? '已深度思考' : '思考过程') : '思考中…' }}</span>
            <span class="who">{{ agentLabel(b.agent) }}</span>
            <Icon name="chevDown" class="chev" />
          </summary>
          <div class="think-text" :class="{ caret: !b.done && live }">{{ b.text }}</div>
        </details>

        <details v-else-if="b.kind === 'tool'" class="tool" :class="'s-' + b.status" :open="b.open" @toggle="b.open = $event.target.open">
          <summary>
            <span class="ticon"><span v-if="b.status === 'running'" class="spinner" /><Icon v-else name="tool" /></span>
            <b>{{ toolLabel(b.tool) }}</b>
            <span class="muted ell">{{ paramsPreview(b.params) }}</span>
            <span class="who">{{ agentLabel(b.agent) }}</span>
            <span class="chip" :class="{ ok: b.status === 'ok', warn: b.status === 'partial', err: ['error', 'rejected', 'timeout'].includes(b.status) }">{{ statusText[b.status] || b.status }}</span>
          </summary>
          <div class="tool-body">
            <div v-if="b.params" class="kv"><span>参数</span><pre>{{ JSON.stringify(b.params, null, 2) }}</pre></div>
            <div v-if="b.summary" class="kv"><span>结果</span><pre>{{ b.summary }}</pre></div>
            <div v-if="b.error_code && b.error_code !== 'noop'" class="kv"><span>拦截原因</span><code>{{ b.error_code }}</code></div>
          </div>
        </details>

        <details v-else-if="b.kind === 'search'" class="tool" :open="b.open" @toggle="b.open = $event.target.open">
          <summary>
            <span class="ticon"><Icon name="search" /></span><b>联网搜索</b>
            <span class="muted ell">“{{ b.query }}” · {{ b.results.length }} 条结果</span>
          </summary>
          <ol class="results">
            <li v-for="(r, j) in b.results" :key="j"><a :href="r.url" target="_blank" rel="noopener noreferrer">{{ r.title || r.url }}</a><p>{{ r.summary }}</p></li>
          </ol>
        </details>

        <div v-else-if="b.kind === 'parallel'" class="parallel" :class="{ done: b.done }">
          <span v-if="!b.done && !b.agents.some((a) => a.state === 'stopped')" class="spinner" />
          <Icon v-else-if="b.done" name="check" />
          <b>{{ b.group === 'review' ? (b.done ? '审查完成' : '审查中…') : (b.done ? '起草完成' : '起草中…') }}</b>
          <span class="muted">
            {{ b.group === 'review' ? '完整性 · 一致性 · 合法性 · 需求符合度 四维并行审查' : `${b.agents.length} 个起草 Agent 分块并行撰写` }}
            · 已完成 {{ b.agents.filter((a) => a.state === 'done').length }}/{{ b.agents.length }}
            <template v-if="b.group !== 'review' && b.edits"> · 已修改 {{ b.edits }} 处条款</template>
          </span>
        </div>

        <details v-else-if="b.kind === 'reviews'" class="tool" :open="b.open" @toggle="b.open = $event.target.open">
          <summary><span class="ticon"><Icon name="shield" /></span><b>审查发现 {{ b.total }} 个问题</b><span class="muted">将由修订主管统一处理</span></summary>
          <div class="reviews">
            <div v-for="(arr, name) in b.reviews" :key="name" class="rv">
              <b>{{ agentLabel(name) }}</b>
              <ul><li v-for="p in arr" :key="p.id"><span class="chip" :class="levelTone[p.level]">{{ p.level }}</span>{{ p.desc }}</li></ul>
            </div>
          </div>
        </details>

        <details v-else-if="b.kind === 'need'" class="tool" :open="b.open" @toggle="b.open = $event.target.open">
          <summary><span class="ticon"><Icon name="list" /></span><b>需求摘要</b><span class="muted ell">{{ b.summary }}</span></summary>
          <div class="tool-body">
            <div class="need-grid">
              <span v-for="(v, k) in b.collected" :key="k"><em>{{ { contract_type: '合同类型', party_type: '签约主体', region: '地区', complexity: '复杂度', scene: '场景' }[k] || k }}</em>{{ v }}</span>
            </div>
            <p>{{ b.summary }}</p>
          </div>
        </details>

        <div v-else-if="b.kind === 'info'" class="info"><Icon name="layers" />{{ b.text }}</div>
        <div v-else-if="b.kind === 'warn'" class="info warn"><Icon name="alert" />{{ b.text }}</div>
        <div v-else-if="b.kind === 'answer' && b.text.trim()" class="answer"><Markdown :text="b.text" /></div>
      </template>
      <div v-if="turn.answer" class="answer final"><Markdown :text="turn.answer" /></div>
      <div v-else-if="live && !turn.closed" class="typing" aria-label="AI 正在处理"><i /><i /><i /></div>
    </div>
  </div>
</template>

<style scoped>
.turn { display: flex; gap: 12px; padding: 4px 0 18px; }
.avatar { width: 30px; height: 30px; border-radius: 9px; flex: none; background: linear-gradient(135deg, var(--brand), #8b5cf6); color: #fff; display: grid; place-items: center; }
.avatar :deep(svg) { width: 17px; height: 17px; }
.body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.step { display: flex; align-items: center; gap: 8px; color: var(--ink); font-size: 13.5px; margin-top: 2px; }
.dot { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; color: var(--brand); }
.dot.done { background: var(--brand); color: #fff; }
.dot.done :deep(svg) { width: 12px; height: 12px; stroke-width: 3; }
.dot.stopped { background: var(--line-2); color: var(--muted); }
.dot.stopped :deep(svg) { width: 10px; height: 10px; }
.dot .spinner { width: 12px; height: 12px; }
.muted { color: var(--muted); font-size: 12.5px; }
.ell { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; flex: 1; }
.who { color: var(--muted); font-size: 12px; margin-left: auto; white-space: nowrap; }
details > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 8px; }
details > summary::-webkit-details-marker { display: none; }
.think { border-left: 2px solid var(--line); padding-left: 12px; }
.think summary { color: var(--ink-2); font-size: 13px; padding: 2px 0; width: fit-content; max-width: 100%; }
.think summary .who { margin-left: 4px; }
.chev { width: 14px; height: 14px; transition: transform .15s; }
.think[open] .chev { transform: rotate(180deg); }
.think-text { color: var(--muted); font-size: 13px; white-space: pre-wrap; word-break: break-word; margin: 6px 0 2px; max-height: 360px; overflow: auto; line-height: 1.7; }
.tool { border: 1px solid var(--line); border-radius: 10px; background: #fff; }
.tool > summary { padding: 8px 12px; font-size: 13px; }
.tool .ticon { width: 22px; height: 22px; border-radius: 6px; background: var(--brand-soft); color: var(--brand); display: grid; place-items: center; flex: none; }
.tool .ticon :deep(svg) { width: 14px; height: 14px; }
.tool.s-rejected .ticon, .tool.s-error .ticon { background: var(--err-soft); color: var(--err); }
.tool-body { border-top: 1px solid var(--line-2); padding: 8px 12px 10px; font-size: 12.5px; }
.kv { display: grid; grid-template-columns: 56px 1fr; gap: 8px; margin: 4px 0; }
.kv > span { color: var(--muted); }
.kv pre { margin: 0; white-space: pre-wrap; word-break: break-all; font: 12px/1.55 ui-monospace, Consolas, monospace; background: #f7f8fa; padding: 6px 8px; border-radius: 6px; max-height: 240px; overflow: auto; }
.results { margin: 0; padding: 6px 14px 10px 34px; font-size: 12.5px; border-top: 1px solid var(--line-2); }
.results li { margin: 6px 0; }
.results p { margin: 2px 0 0; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.parallel { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 12px; border-radius: 10px;
  background: var(--brand-soft); color: var(--brand); font-size: 13px; }
.parallel.done { background: var(--ok-soft); color: #0e7a2a; }
.parallel .muted { color: var(--ink-2); }
.parallel :deep(svg) { width: 15px; height: 15px; }
.reviews { border-top: 1px solid var(--line-2); padding: 8px 12px; display: grid; gap: 8px; font-size: 12.5px; }
.rv ul { margin: 4px 0 0; padding-left: 0; list-style: none; }
.rv li { margin: 3px 0; display: flex; gap: 6px; align-items: baseline; }
.need-grid { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-bottom: 6px; }
.need-grid em { font-style: normal; color: var(--muted); margin-right: 4px; }
.info { display: flex; align-items: center; gap: 6px; color: var(--ink-2); font-size: 13px; }
.info :deep(svg) { width: 15px; height: 15px; color: var(--brand); }
.info.warn { color: #b25000; } .info.warn :deep(svg) { color: var(--warn); }
.answer { font-size: 14.5px; line-height: 1.75; }
.answer.final { margin-top: 2px; }
.typing { display: flex; gap: 4px; padding: 6px 0; }
.typing i { width: 6px; height: 6px; border-radius: 50%; background: var(--brand-2); opacity: .4; animation: bounce 1.2s infinite; }
.typing i:nth-child(2) { animation-delay: .15s; } .typing i:nth-child(3) { animation-delay: .3s; }
@keyframes bounce { 0%, 80%, 100% { opacity: .3; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
</style>
