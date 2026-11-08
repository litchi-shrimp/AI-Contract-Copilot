<script setup>
// 右侧文档面板：A4 纸张式只读渲染（类 docx），AI 增量修改以 新增(绿)/删除(红删除线)/修改(黄+旧文) 高亮
import { computed, nextTick, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { diff, stats, tpl } from '../lib/contract.js'

const props = defineProps({
  doc: { type: Object, default: null },      // 当前合同/大纲
  prev: { type: Object, default: null },     // 上一版本（用于 diff）
  kind: { type: String, default: 'outline' },
  title: { type: String, default: '' },
  live: { type: Boolean, default: false },   // AI 正在修改
  canDownload: { type: Boolean, default: false },
  downloading: { type: Boolean, default: false },
})
const emit = defineEmits(['download', 'collapse'])

const showDiff = ref(true)
const view = ref('doc') // doc | outline
const scroller = ref(null)

const ops = computed(() => {
  if (!props.doc) return []
  if (!showDiff.value || !props.prev) return diff(null, props.doc).map((o) => ({ ...o, status: 'same' }))
  return diff(props.prev, props.doc)
})
const st = computed(() => stats(ops.value))
const header = computed(() => {
  const t = tpl(props.doc)
  const lines = String(t?.['合同首部'] || '').split('\n').map((x) => x.trim()).filter(Boolean)
  const title = lines[0] && lines[0].length <= 40 ? lines[0] : props.title || '合同'
  return { title, rest: lines[0] && lines[0].length <= 40 ? lines.slice(1) : lines }
})
const outlineTree = computed(() => ops.value.filter((o) => o.kind === 'section' || o.level === 2))

// 修改发生时自动滚动到第一处变更
watch(() => props.doc, async () => {
  if (!showDiff.value) return
  await nextTick()
  const el = scroller.value?.querySelector('.chg')
  if (el && props.live) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
})

function jump(key) {
  view.value = 'doc'
  nextTick(() => scroller.value?.querySelector(`[data-key="${CSS.escape(key)}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
}
</script>

<template>
  <aside class="panel" aria-label="合同预览">
    <header class="bar">
      <div class="ttl">
        <Icon name="doc" />
        <b>{{ kind === 'outline' ? '合同大纲' : '合同文本' }}</b>
        <span v-if="live" class="chip brand"><span class="spinner" style="width:10px;height:10px;border-width:1.5px" />AI 修改中</span>
        <span v-else-if="doc" class="chip">只读预览</span>
      </div>
      <div class="tools">
        <div class="seg" role="tablist" aria-label="视图">
          <button :class="{ on: view === 'doc' }" @click="view = 'doc'" role="tab" :aria-selected="view === 'doc'">文档</button>
          <button :class="{ on: view === 'outline' }" @click="view = 'outline'" role="tab" :aria-selected="view === 'outline'">目录</button>
        </div>
        <label class="toggle" title="高亮显示本次 AI 修改">
          <input type="checkbox" v-model="showDiff" /> <span>显示修改</span>
        </label>
        <button class="btn sm" :disabled="!canDownload || downloading" @click="emit('download')" data-testid="download">
          <span v-if="downloading" class="spinner" /><Icon v-else name="download" />下载 Word
        </button>
        <button class="icon-btn" @click="emit('collapse')" title="收起面板" aria-label="收起面板"><Icon name="panelClose" /></button>
      </div>
    </header>
    <div v-if="doc && showDiff && prev && (st.added || st.removed || st.modified)" class="diffbar">
      本次修改：<span class="d add">+{{ st.added }} 新增</span><span class="d mod">~{{ st.modified }} 修改</span><span class="d del">−{{ st.removed }} 删除</span>
    </div>
    <div ref="scroller" class="scroll">
      <div v-if="!doc" class="empty">
        <div class="ph"><Icon name="doc" /></div>
        <p>合同大纲与正文将在这里实时生成</p>
        <span>AI 的每一次增删改都会以高亮显示</span>
      </div>
      <template v-else-if="view === 'doc'">
        <article class="page" :class="{ outline: kind === 'outline' }">
          <h1 class="doc-title">{{ header.title }}</h1>
          <template v-for="(o, i) in ops" :key="o.key + ':' + o.status + ':' + i">
            <div v-if="o.kind === 'header'" class="hf" :class="['st-' + o.status, { chg: o.status !== 'same' }]">
              <p v-for="(ln, j) in header.rest" :key="j">{{ ln }}</p>
            </div>
            <h2 v-else-if="o.kind === 'section'" class="sec" :data-key="o.key" :class="['st-' + o.status, { chg: o.status !== 'same' }]">
              <span class="num">{{ o.num }}</span>{{ o.text }}
              <del v-if="o.status === 'modified'" class="old">{{ o.oldText }}</del>
            </h2>
            <p v-else-if="o.kind === 'clause'" class="cl" :data-key="o.key" :class="['lv' + o.level, 'st-' + o.status, { chg: o.status !== 'same' }]">
              <span class="num">{{ o.num }}</span>
              <span class="txt">{{ o.text }}</span>
              <del v-if="o.status === 'modified'" class="old">{{ o.oldText }}</del>
            </p>
            <div v-else-if="o.kind === 'footer'" class="hf foot" :class="['st-' + o.status, { chg: o.status !== 'same' }]">
              <p v-for="(ln, j) in o.text.split('\n').filter(Boolean)" :key="j">{{ ln }}</p>
            </div>
          </template>
        </article>
      </template>
      <nav v-else class="toc">
        <button v-for="o in outlineTree" :key="o.key" class="toc-item" :class="['lv' + o.level, 'st-' + o.status]" @click="jump(o.key)">
          <span class="num">{{ o.num }}</span>{{ o.level === 1 ? o.text : o.text.slice(0, 40) + (o.text.length > 40 ? '…' : '') }}
        </button>
      </nav>
    </div>
  </aside>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; height: 100%; background: #eceef2; border-left: 1px solid var(--line); min-width: 0; }
.bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px 10px 16px; background: #fff; border-bottom: 1px solid var(--line); }
.ttl { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.seg { display: inline-flex; background: var(--line-2); border-radius: 8px; padding: 2px; }
.seg button { border: 0; background: transparent; padding: 3px 10px; border-radius: 6px; font-size: 12.5px; color: var(--ink-2); }
.seg button.on { background: #fff; color: var(--ink); box-shadow: 0 1px 2px rgba(0, 0, 0, .08); }
.toggle { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; color: var(--ink-2); cursor: pointer; user-select: none; }
.diffbar { background: #fff; border-bottom: 1px solid var(--line); padding: 6px 16px; font-size: 12.5px; color: var(--ink-2); display: flex; gap: 10px; align-items: center; }
.d { padding: 1px 8px; border-radius: 999px; }
.d.add { background: var(--add-bg); color: var(--add-ink); } .d.mod { background: var(--mod-bg); color: #8a5a00; } .d.del { background: var(--del-bg); color: var(--del-ink); }
.scroll { flex: 1; overflow: auto; padding: 24px 20px 60px; }
.empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--muted); text-align: center; }
.empty .ph { width: 64px; height: 64px; border-radius: 16px; background: #fff; display: grid; place-items: center; margin-bottom: 14px; box-shadow: var(--shadow); }
.empty .ph :deep(svg) { width: 30px; height: 30px; color: var(--brand); }
.empty p { margin: 0 0 4px; color: var(--ink-2); font-size: 15px; }
.empty span { font-size: 12.5px; }

/* A4 纸张 */
.page { background: #fff; max-width: 794px; margin: 0 auto; padding: 72px 76px 96px; box-shadow: 0 2px 10px rgba(15, 23, 42, .08);
  font-family: var(--doc-font); color: #111; font-size: 15px; line-height: 1.9; user-select: text; }
.doc-title { text-align: center; font-size: 22px; font-weight: 700; margin: 0 0 22px; letter-spacing: .04em; font-family: var(--doc-font); }
.hf p { margin: 0; }
.hf.foot { margin-top: 28px; }
.sec { font-size: 16px; font-weight: 700; margin: 22px 0 8px; font-family: var(--doc-font); }
.sec .num { margin-right: 8px; }
.cl { margin: 4px 0; text-align: justify; }
.cl .num { margin-right: 6px; color: #333; font-variant-numeric: tabular-nums; }
.cl.lv2 { text-indent: 2em; }
.cl.lv3 { padding-left: 2em; }
.cl.lv4 { padding-left: 4em; }
.page.outline .cl.lv2 { text-indent: 0; padding-left: 1em; }
.page.outline .cl.lv3 { padding-left: 3em; color: #333; }
.page.outline .cl.lv4 { padding-left: 5em; color: #444; }
.page.outline .sec { border-bottom: 1px solid #eee; padding-bottom: 4px; }
.st-added { background: var(--add-bg); box-shadow: inset 3px 0 0 #34c759; border-radius: 3px; }
.st-removed { background: var(--del-bg); color: var(--del-ink); text-decoration: line-through; border-radius: 3px; box-shadow: inset 3px 0 0 var(--err); }
.st-modified { background: var(--mod-bg); box-shadow: inset 3px 0 0 #f5a623; border-radius: 3px; }
.old { display: block; color: var(--del-ink); font-size: 13px; text-indent: 0; opacity: .8; }
.toc { background: #fff; max-width: 794px; margin: 0 auto; padding: 18px; border-radius: 8px; box-shadow: var(--shadow); }
.toc-item { display: block; width: 100%; text-align: left; border: 0; background: transparent; padding: 6px 10px; border-radius: 6px; color: var(--ink); }
.toc-item:hover { background: var(--line-2); }
.toc-item.lv1 { font-weight: 600; margin-top: 6px; }
.toc-item.lv2 { padding-left: 28px; color: var(--ink-2); font-size: 13px; }
.toc-item .num { color: var(--muted); margin-right: 8px; }
@media (max-width: 1280px) { .page { padding: 48px 40px 72px; } }
</style>
