<script setup>
// 合同首部 / 尾部排版块（由 lib/layout.js 解析）：标题、编号信息、当事人信息栏、鉴于条款、签章区、表格
defineProps({ blocks: { type: Array, default: () => [] } })
</script>

<template>
  <template v-for="(b, i) in blocks" :key="i">
    <div v-if="b.t === 'meta'" class="lb-meta">
      <div v-for="(r, j) in b.rows" :key="j"><span>{{ r[0] }}：</span>{{ r[1] }}</div>
    </div>
    <dl v-else-if="b.t === 'party'" class="lb-party" :class="{ plain: b.plain }">
      <template v-for="(r, j) in b.rows" :key="j">
        <dt :class="{ lead: j === 0 }">{{ r[0] }}</dt><dd :class="{ lead: j === 0 }">{{ r[1] }}</dd>
      </template>
    </dl>
    <ol v-else-if="b.t === 'list'" class="lb-list"><li v-for="(x, j) in b.items" :key="j">{{ x }}</li></ol>
    <div v-else-if="b.t === 'sign'" class="lb-sign" :style="{ gridTemplateColumns: `repeat(${b.cols.length}, 1fr)` }">
      <div v-for="(col, j) in b.cols" :key="j" class="col">
        <p v-for="(r, k) in col" :key="k" :class="{ lead: k === 0 }">
          <template v-if="r[1] !== ''"><span>{{ r[0] }}：</span><span class="fill">{{ r[1] }}</span></template>
          <template v-else>{{ r[0] }}</template>
        </p>
      </div>
    </div>
    <p v-else-if="b.t === 'center'" class="lb-center">{{ b.text }}</p>
    <table v-else-if="b.t === 'table'" class="lb-tbl">
      <thead><tr><th v-for="(h, k) in b.head" :key="k">{{ h }}</th></tr></thead>
      <tbody><tr v-for="(r, k) in b.rows" :key="k"><td v-for="(c, m) in r" :key="m">{{ c }}</td></tr></tbody>
    </table>
    <p v-else class="lb-para" :class="{ indent: b.indent }">{{ b.text }}</p>
  </template>
</template>

<style scoped>
.lb-meta { text-align: right; font-size: 13.5px; color: #333; margin: -10px 0 16px; line-height: 1.8; }
.lb-meta span { color: #555; }
.lb-party { display: grid; grid-template-columns: max-content 1fr; column-gap: 4px; margin: 0 0 12px; line-height: 1.85; }
.lb-party dt { color: #333; white-space: nowrap; }
.lb-party dt::after { content: "："; }
.lb-party dd { margin: 0; min-width: 0; word-break: break-all; }
.lb-party .lead { font-weight: 700; }
.lb-party dt:not(.lead) { padding-left: 2em; }
.lb-party.plain .lead { font-weight: 400; }
.lb-party.plain dt { padding-left: 0; }
.lb-list { margin: 0 0 8px; padding-left: 4em; }
.lb-list li { text-align: justify; padding-left: .2em; }
.lb-para { margin: 0 0 4px; text-align: justify; }
.lb-para.indent { text-indent: 2em; }
.lb-center { text-align: center; margin: 14px 0; letter-spacing: .1em; }
.lb-sign { display: grid; column-gap: 48px; margin: 22px 0 10px; }
.lb-sign .col p { margin: 0 0 10px; line-height: 1.7; }
.lb-sign .col p.lead { font-weight: 700; }
.lb-sign .fill { display: inline-block; min-width: 7em; border-bottom: 1px solid #999; padding: 0 4px; }
.lb-tbl { width: 100%; border-collapse: collapse; margin: 8px 0 12px; font-size: 14px; line-height: 1.6; }
.lb-tbl th, .lb-tbl td { border: 1px solid #333; padding: 5px 8px; text-align: left; }
.lb-tbl th { background: #f5f5f5; text-align: center; }
@media (max-width: 1100px) { .lb-sign { grid-template-columns: 1fr !important; } }
</style>
