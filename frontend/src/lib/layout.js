// 合同排版：把首部 / 尾部 / 条款的纯文本解析成“像合同”的展示块（纯函数，不改数据结构）
//
// 块类型：
//   { t: 'title', text }                      合同标题（居中）
//   { t: 'meta', rows: [[k, v]] }             合同编号 / 签订地点 等（右对齐小字）
//   { t: 'party', rows: [[k, v]] }            当事人信息（标签：内容 两列对齐）
//   { t: 'para', text, indent }               普通段落（鉴于 / 前言，首行缩进）
//   { t: 'list', items }                      鉴于条款下的 1. 2. 3.
//   { t: 'sign', cols: [[ [k, v] ]] }         签章区（甲乙方左右两栏）
//   { t: 'center', text }                     （以下无正文）
//   { t: 'table', head: [], rows: [[]] }      Markdown 表格
//   { t: 'formula', text }                    计算公式行

const KV = /^([^：:，。；]{1,18})[：:]\s*(.*)$/
const PARTY_HEAD = /^(甲方|乙方|丙方|丁方|出租方|承租方|买方|卖方|委托方|受托方|服务方|授权方|被授权方|出借人|借款人|发包人|承包人|转让方|受让方|基金管理人|基金托管人|用人单位|劳动者)/
const META_KEY = /^(合同编号|协议编号|编号|签订地点|签订日期|合同签订地点|合同签订日期|签署地点|适用地区)$/
const SIGN_HINT = /(签字|签章|盖章|签署日期|授权代表)/
const NO_BODY = /^[（(]?以下无正文[）)]?$/
const LIST_ITEM = /^(\d{1,2}[.、．]|[（(][一二三四五六七八九十\d]{1,3}[）)]|[一二三四五六七八九十]{1,3}、)\s*/
const FORMULA = /[=＝][^=＝]*[×÷*/＋+－-]|[×÷][^，。；]*[=＝]/

function stripTitle(s) {
  return s.replace(/^(协议名称|合同名称)[：:]\s*/, '').replace(/^《(.+)》$/, '$1').trim()
}

// ---------------------------------------------------------------- 表格
function isTableRow(s) { return /^\s*\|.*\|\s*$/.test(s) }
function isSepRow(s) { return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(s) }
function cells(s) { return s.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()) }

// 把一段文本中的 Markdown 表格切出来，其余仍是文本行
export function splitRich(text) {
  const lines = String(text || '').split('\n')
  const out = []
  let buf = []
  const flush = () => { if (buf.length) { out.push({ t: 'text', lines: buf }); buf = [] } }
  for (let i = 0; i < lines.length; i++) {
    if (isTableRow(lines[i]) && i + 1 < lines.length && isSepRow(lines[i + 1])) {
      flush()
      const head = cells(lines[i])
      const rows = []
      i += 2
      while (i < lines.length && isTableRow(lines[i])) { rows.push(cells(lines[i])); i++ }
      i--
      out.push({ t: 'table', head, rows })
    } else buf.push(lines[i])
  }
  flush()
  return out
}

// 条款正文：段落 / 表格 / 公式（供 DocPanel 渲染单个条款）
export function clauseBlocks(text) {
  const out = []
  for (const part of splitRich(text)) {
    if (part.t === 'table') { out.push(part); continue }
    for (const ln of part.lines) {
      const s = ln.trim()
      if (!s) continue
      out.push(FORMULA.test(s) && s.length <= 160 ? { t: 'formula', text: s } : { t: 'para', text: s })
    }
  }
  return out
}

// ---------------------------------------------------------------- 首部
export function headerBlocks(text, fallbackTitle = '合同') {
  const lines = String(text || '').split('\n').map((x) => x.trim())
  const blocks = []
  let i = 0
  while (i < lines.length && !lines[i]) i++
  const first = lines[i] || ''
  if (first && first.length <= 40 && !KV.test(first.replace(/^(协议名称|合同名称)[：:]/, ''))) {
    blocks.push({ t: 'title', text: stripTitle(first) }); i++
  } else if (/^(协议名称|合同名称)[：:]/.test(first)) {
    blocks.push({ t: 'title', text: stripTitle(first) }); i++
  } else blocks.push({ t: 'title', text: fallbackTitle })

  let cur = null
  const push = (b) => { cur = null; blocks.push(b) }
  for (; i < lines.length; i++) {
    const s = lines[i]
    if (!s) { cur = null; continue }
    if (s.startsWith('|')) {   // 首部里的表格交给 splitRich
      const rest = lines.slice(i).join('\n')
      for (const p of splitRich(rest)) {
        if (p.t === 'table') blocks.push(p)
        else p.lines.filter(Boolean).forEach((x) => blocks.push({ t: 'para', text: x.trim(), indent: true }))
      }
      break
    }
    // 一行里用；串起来的当事人信息（甲方：xx；统一社会信用代码：xx；住所：xx）
    const semi = s.split(/[；;]/).map((x) => x.trim()).filter(Boolean)
    if (semi.length >= 2 && semi.every((x) => KV.test(x)) && PARTY_HEAD.test(semi[0])) {
      push({ t: 'party', rows: semi.map((x) => { const m = x.match(KV); return [m[1], m[2].replace(/。$/, '')] }) })
      continue
    }
    const m = s.match(KV)
    if (m && s.length <= 80) {
      const [, k, v] = m
      if (META_KEY.test(k)) {
        if (cur?.t !== 'meta') { cur = { t: 'meta', rows: [] }; blocks.push(cur) }
        cur.rows.push([k, v]); continue
      }
      if (PARTY_HEAD.test(k)) { cur = { t: 'party', rows: [[k, v]] }; blocks.push(cur); continue }
      if (cur?.t === 'party' || cur?.t === 'meta') { cur.rows.push([k, v]); continue }
      if (k === '鉴于' && !v) { push({ t: 'para', text: '鉴于：' }); cur = { t: 'list', items: [] }; blocks.push(cur); continue }
      cur = { t: 'party', rows: [[k, v]], plain: true }; blocks.push(cur); continue
    }
    if (/^鉴于[：:]?$/.test(s)) { push({ t: 'para', text: '鉴于：' }); cur = { t: 'list', items: [] }; blocks.push(cur); continue }
    if (cur?.t === 'list') { cur.items.push(s.replace(LIST_ITEM, '')); continue }
    push({ t: 'para', text: s, indent: true })
  }
  return blocks
}

// ---------------------------------------------------------------- 尾部（签章区）
export function footerBlocks(text) {
  const groups = []
  let g = []
  for (const raw of String(text || '').split('\n')) {
    const s = raw.trim()
    if (!s) { if (g.length) groups.push(g); g = []; continue }
    g.push(s)
  }
  if (g.length) groups.push(g)
  const blocks = []
  const signCols = []
  const flushSign = () => {
    if (!signCols.length) return
    blocks.push({ t: 'sign', cols: signCols.splice(0) })
  }
  const sideBySide = (x) => /\S\s{2,}\S/.test(x) && x.split(/\s{2,}/).length >= 2 && x.split(/\s{2,}/).every((y) => KV.test(y))
  for (const grp of groups) {
    // 同一行里并排写的两方签字：“甲方确认（签字）：xx    乙方确认（签字）：yy”——拆成左右两栏，其余行照常处理
    const pairIdx = grp.findIndex(sideBySide)
    if (pairIdx >= 0 && !(PARTY_HEAD.test(grp[0]) && grp.some((x) => SIGN_HINT.test(x)) && pairIdx > 0 && !sideBySide(grp[0]))) {
      flushSign()
      grp.slice(0, pairIdx).forEach((x) => blocks.push({ t: 'para', text: x, indent: !KV.test(x) }))
      blocks.push({ t: 'sign', cols: grp[pairIdx].split(/\s{2,}/).map((x) => { const m = x.match(KV); return [[m[1], m[2]]] }) })
      grp.slice(pairIdx + 1).forEach((x) => blocks.push(NO_BODY.test(x) ? { t: 'center', text: x } : { t: 'para', text: x, indent: !KV.test(x) }))
      continue
    }
    const isSign = PARTY_HEAD.test(grp[0]) && grp.some((x) => SIGN_HINT.test(x))
    if (isSign) {
      signCols.push(grp.map((x) => { const m = x.match(KV); return m ? [m[1], m[2]] : [x, ''] }))
      if (signCols.length === 2) flushSign()
      continue
    }
    flushSign()
    // “卖方收款账户：”+ 若干“键：值”行 → 小标题 + 信息栏
    if (grp.length >= 2 && /[：:]$/.test(grp[0]) && !/^附件/.test(grp[0]) && grp.slice(1).every((x) => KV.test(x) && x.length <= 80)) {
      blocks.push({ t: 'para', text: grp[0] })
      blocks.push({ t: 'party', plain: true, rows: grp.slice(1).map((x) => { const m = x.match(KV); return [m[1], m[2]] }) })
      continue
    }
    for (const s of grp) {
      if (NO_BODY.test(s)) blocks.push({ t: 'center', text: s })
      else if (/^附件[：:]?$/.test(s)) blocks.push({ t: 'para', text: '附件：' })
      else if (blocks.length && blocks[blocks.length - 1].t === 'list') blocks[blocks.length - 1].items.push(s.replace(LIST_ITEM, ''))
      else if (/^附件[：:]?$/.test(grp[0]) && s !== grp[0]) {
        blocks.push({ t: 'list', items: [s.replace(LIST_ITEM, '')] })
      } else blocks.push({ t: 'para', text: s, indent: !KV.test(s) })
    }
  }
  flushSign()
  return blocks
}
