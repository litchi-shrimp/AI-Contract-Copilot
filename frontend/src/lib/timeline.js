// 事件流 → 对话时间线（与豆包/DeepSeek 网页版一致的结构：用户消息 / AI 回合[思考·工具·回答]）
import { reactive } from 'vue'

export const STEP_NAMES = { 1: '需求提取', 2: '模板匹配', 3: '大纲生成', 4: '大纲确认', 5: '合同起草', 6: '多维审查', 7: '修订', 8: '终稿确认' }

const AGENT_NAMES = {
  UserFieldExtractionAgent: '需求分析',
  TemplateMatcher: '模板匹配',
  OutlineGenerationAgent: '大纲生成',
  AdversarialOutlineAgent: '对抗式大纲（生成方）',
  ModelB_Reviewer: '对抗式大纲（审查方）',
  OutlineModificationAgent: '大纲修改',
  ReviewCompletenessAgent: '完整性审查',
  ReviewConsistencyAgent: '一致性审查',
  ReviewLegalAgent: '合法性审查',
  ReviewUsageAgent: '需求符合性审查',
  LeaderAgent: '修订主管',
  ContractModificationAgent: '合同修改',
  ChunkMerger: '分块合并',
}

export function agentLabel(a) {
  if (!a) return 'AI'
  if (a.startsWith('DrafterAgent_')) return `起草 #${a.split('_')[1]}`
  return AGENT_NAMES[a] || a
}

const TOOL_NAMES = {
  update_clause: '修改条款', insert_clause: '新增条款', delete_clause: '删除条款', update_header_footer: '修改首尾部',
  web_search: '联网搜索', retrieve_template_reference: '检索模板', get_contract: '读取合同',
}
export const toolLabel = (t) => TOOL_NAMES[t] || t

export function createTimeline() {
  const s = reactive({
    items: [],          // { type:'user'|'turn'|'notice', ... }
    step: 1,
    stepDone: [],
    running: false,
    waiting: false,
    status: 'idle',
    lastAsk: '',
  })
  let turn = null

  function ensureTurn(step) {
    if (!turn || turn.closed) {
      s.items.push({ type: 'turn', id: `t${s.items.length}`, step, blocks: [], closed: false, answer: '', startedAt: Date.now() })
      // 必须持有响应式代理（而非原始对象），否则后续对 turn 的修改不会触发视图更新
      turn = s.items[s.items.length - 1]
    }
    return turn
  }

  function block(kind, agent, step, extra = {}) {
    const t = ensureTurn(step)
    let b = t.blocks[t.blocks.length - 1]
    if (!b || b.kind !== kind || b.agent !== agent || b.done) {
      t.blocks.push({ kind, agent, step, text: '', done: false, open: kind === 'answer', ...extra })
      b = t.blocks[t.blocks.length - 1]
    }
    return b
  }

  function closeStreams() {
    if (!turn) return
    for (const b of turn.blocks) if (b.kind === 'think' || b.kind === 'reason') b.done = true
  }

  function findStep(step) {
    for (let i = s.items.length - 1; i >= 0; i--) {
      const it = s.items[i]
      if (it.type !== 'turn') continue
      for (let j = it.blocks.length - 1; j >= 0; j--) if (it.blocks[j].kind === 'step' && it.blocks[j].step === step) return it.blocks[j]
    }
    return null
  }

  // 运行被中断：未完成的步骤 / 并行任务 / 工具调用标记为“已中断”，不再显示加载动画
  function interrupt() {
    for (const it of s.items) {
      if (it.type !== 'turn') continue
      for (const b of it.blocks) {
        if (b.kind === 'step' && !b.done) b.interrupted = true
        if (b.kind === 'parallel') for (const a of b.agents) if (a.state !== 'done') a.state = 'stopped'
        if (b.kind === 'tool' && b.status === 'running') b.status = 'stopped'
        if (b.kind === 'think' || b.kind === 'reason') b.done = true
      }
    }
  }

  // 并行阶段（5 起草 / 6 审查）：多个 Agent 同时流式输出思考与工具调用，逐条渲染会产生上千个 DOM 节点导致页面卡顿。
  // 这两个阶段只展示一条聚合进度（“起草中 / 审查中” + 子任务完成数 + 修改条款计数），不渲染思考流与逐条工具调用。
  const PARALLEL_STEPS = new Set([5, 6])
  const PARALLEL_DROP = new Set(['agent.reasoning.stream', 'agent.reasoning.text', 'agent.think.stream', 'agent.think.text',
    'agent.act.stream', 'agent.answer.stream', 'agent.answer.text', 'llm.token', 'tool.call', 'retrieval.web'])

  function apply(ev, { replay = false } = {}) {
    const t = ev.event_type
    const d = ev.data || {}
    const step = ev.step ?? s.step
    if (PARALLEL_STEPS.has(step)) {
      if (PARALLEL_DROP.has(t)) return
      if (t === 'tool.result') {
        const pb = [...ensureTurn(step).blocks].reverse().find((b) => b.kind === 'parallel' && b.step === step)
        if (pb && (d.status === 'ok' || d.status === 'partial')) pb.edits = (pb.edits || 0) + 1
        return
      }
    }
    switch (t) {
      case 'user.message': {
        if (replay) break
        // 去重：本地已乐观插入的同文消息不再重复显示
        const lastUser = [...s.items].reverse().find((x) => x.type === 'user')
        if (lastUser && lastUser.text === d.text && lastUser.pending) { lastUser.pending = false; break }
        pushUser(d.text, step)
        break
      }
      case 'run.started':
        s.running = true; s.waiting = false; s.status = 'running'
        break
      case 'step.start':
        if (ev.step) s.step = ev.step
        ensureTurn(step).blocks.push({ kind: 'step', step, text: d.step_name || STEP_NAMES[step], done: false, resumed: d.resumed })
        break
      case 'step.complete': {
        if (ev.step && !s.stepDone.includes(ev.step)) s.stepDone.push(ev.step)
        // 交互步骤可能跨多个回合：在所有回合中查找该步骤块（不为“完成”事件新建空回合）
        const sb = findStep(step)
        if (sb) { sb.done = true; sb.duration = d.duration; sb.interrupted = false }
        closeStreams()
        break
      }
      case 'agent.reasoning.stream':
      case 'agent.reasoning.text':
        block('reason', ev.agent, step).text += d.text || ''
        break
      case 'agent.think.stream':
      case 'agent.think.text':
      case 'agent.act.stream':
        if (t === 'agent.act.stream') break
        block('think', ev.agent, step).text += d.text || ''
        break
      case 'agent.answer.stream':
      case 'agent.answer.text':
        block('answer', ev.agent, step).text += d.text || ''
        break
      case 'tool.call': {
        closeStreams()
        ensureTurn(step).blocks.push({ kind: 'tool', agent: ev.agent, step, seq: d.seq, tool: d.tool_name,
          params: d.params, status: 'running', open: false })
        break
      }
      case 'tool.result': {
        const tt = ensureTurn(step)
        const tb = [...tt.blocks].reverse().find((b) => b.kind === 'tool' && b.agent === ev.agent && b.seq === d.seq)
          || [...tt.blocks].reverse().find((b) => b.kind === 'tool' && b.agent === ev.agent && b.status === 'running')
        let target = tb
        if (!target) { tt.blocks.push({ kind: 'tool', agent: ev.agent, step, tool: d.tool_name, open: false }); target = tt.blocks[tt.blocks.length - 1] }
        Object.assign(target, { status: d.status || (d.success ? 'ok' : 'error'), summary: d.summary, error_code: d.error_code,
          duration_ms: d.duration_ms })
        break
      }
      case 'retrieval.web':
        ensureTurn(step).blocks.push({ kind: 'search', agent: ev.agent, step, query: d.query, results: d.results || [], open: false })
        break
      case 'parallel.start': {
        const tt = ensureTurn(step)
        if (tt.blocks.some((b) => b.kind === 'parallel' && b.step === step)) break
        tt.blocks.push({ kind: 'parallel', step, group: d.group, edits: 0, agents: (d.agents || []).map((a) => ({ name: a, state: 'pending' })) })
        break
      }
      case 'parallel.complete': {
        const pb = [...ensureTurn(step).blocks].reverse().find((b) => b.kind === 'parallel' && b.step === step)
        if (pb) { pb.done = true; for (const a of pb.agents) if (a.state !== 'stopped') a.state = 'done' }
        break
      }
      case 'parallel.agent_start':
      case 'parallel.agent_complete': {
        const tt = ensureTurn(step)
        const pb = [...tt.blocks].reverse().find((b) => b.kind === 'parallel')
        const a = pb?.agents.find((x) => x.name === ev.agent)
        if (a) a.state = t.endsWith('start') ? 'running' : 'done'
        break
      }
      case 'artifact.templates':
        ensureTurn(step).blocks.push({ kind: 'info', step, text: d.count ? `匹配到 ${d.count} 个参考模板：${(d.names || []).slice(0, 3).join('、')}${d.count > 3 ? ' 等' : ''}` : '模板库中未找到同类模板，将启用联网检索 + 双模型对抗生成大纲' })
        break
      case 'artifact.reviews': {
        const total = Object.values(d).reduce((n, arr) => n + (arr?.length || 0), 0)
        ensureTurn(step).blocks.push({ kind: 'reviews', step, total, reviews: d, open: false })
        break
      }
      case 'artifact.user_need':
        ensureTurn(step).blocks.push({ kind: 'need', step, collected: d.collected, summary: d.summary, extra: d.extra_need, open: false })
        break
      case 'step.degraded':
        ensureTurn(step).blocks.push({ kind: 'warn', step, text: `已启用降级策略：${d.step_name || ''} → ${d.strategy}` })
        break
      case 'llm.fallback':
        ensureTurn(step).blocks.push({ kind: 'warn', step, text: `模型 ${d.from} 响应异常，已自动切换至 ${d.to}` })
        break
      case 'system':
        if (d.message) ensureTurn(step).blocks.push({ kind: 'warn', step, text: d.message })
        break
      case 'run.waiting':
        s.running = false; s.waiting = true; s.status = 'waiting_input'; s.lastAsk = d.prompt || ''
        if (turn) { closeStreams(); if (!replay) pushAssistant(d.prompt, d.step) ; turn.closed = true }
        else if (!replay) pushAssistant(d.prompt, d.step)
        break
      case 'run.resumed_input':
        s.running = true; s.waiting = false; s.status = 'running'
        break
      case 'run.stopped':
        s.running = false; s.waiting = false; s.status = 'paused'
        interrupt()
        if (turn) turn.closed = true
        s.items.push({ type: 'notice', tone: 'warn', text: `已停止（第 ${d.step || s.step} 步）。点击“继续生成”将从最近的检查点恢复。` })
        break
      case 'run.failed':
        s.running = false; s.waiting = false; s.status = 'failed'
        interrupt()
        if (turn) turn.closed = true
        s.items.push({ type: 'notice', tone: 'error', text: `运行出错：${d.error || ''}。可点击“继续生成”重试。` })
        break
      case 'run.completed':
      case 'workflow.complete':
        if (t === 'run.completed') { s.running = false; s.waiting = false; s.status = 'completed' }
        for (let n = 1; n <= 8; n++) if (!s.stepDone.includes(n)) s.stepDone.push(n)
        closeStreams()
        if (turn) turn.closed = true
        break
      default:
        break
    }
  }

  function pushUser(text, step, pending = false) {
    if (turn) { closeStreams(); turn.closed = true }
    s.items.push({ type: 'user', text, step, pending })
  }

  function pushAssistant(text, step) {
    if (!text) return
    const t = turn && !turn.closed ? turn : ensureTurn(step)
    // 流式回答与最终回答内容重复时，只保留最终回答
    const norm = (x) => String(x || '').replace(/\s+/g, '')
    for (const b of t.blocks) if (b.kind === 'answer' && norm(text).includes(norm(b.text))) b.text = ''
    t.answer = text
    t.closed = true
  }

  // 由数据库历史（消息 + 聚合事件）重建时间线：按时间合并
  function hydrate(messages, events) {
    s.items.splice(0)
    turn = null
    const merged = [
      ...messages.map((m) => ({ ts: Date.parse(m.created_at), kind: 'msg', m })),
      ...events.map((e) => ({ ts: Date.parse(e.created_at), kind: 'ev', e })),
    ].sort((x, y) => x.ts - y.ts || (x.kind === 'msg' ? -1 : 1))
    for (const it of merged) {
      if (it.kind === 'msg') {
        if (it.m.role === 'user') pushUser(it.m.content, it.m.step)
        else if (it.m.role === 'assistant') pushAssistant(it.m.content, it.m.step)
      } else {
        const e = it.e
        if (e.event_type === 'run.waiting' || e.event_type === 'agent.ask') continue
        apply({ event_type: e.event_type, step: e.step, agent: e.agent, data: e.data }, { replay: true })
      }
    }
    // 历史中未完成的步骤（被停止 / 服务重启中断）统一标记为已中断
    for (const it of s.items) if (it.type === 'turn') for (const b of it.blocks) if (b.kind === 'step' && !b.done && s.stepDone.includes(b.step)) b.done = true
    interrupt()
    s.running = false
  }

  return { state: s, apply, hydrate, pushUser }
}
