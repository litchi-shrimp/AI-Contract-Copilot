// 合同结构工具：扁平化、增量 diff（新增 / 修改 / 删除）

export function tpl(doc) {
  if (!doc) return null
  return doc['标准化模板文本'] || doc
}

// 扁平化为节点列表：{ key, level, num, title, text }，key 优先用 locator
export function flatten(doc) {
  const t = tpl(doc)
  if (!t) return []
  const out = []
  const header = String(t['合同首部'] || '')
  if (header) out.push({ key: 'header', level: 0, kind: 'header', text: header })
  for (const s of t['正文章节'] || []) {
    const sk = String(s.locator ?? s['章节编号'])
    out.push({ key: sk, level: 1, kind: 'section', num: s['章节编号'] || '', text: s['章节标题'] || '' })
    for (const c of s['条款列表'] || []) {
      const ck = String(c.locator ?? c['条款编号'])
      out.push({ key: ck, level: 2, kind: 'clause', num: c['条款编号'] || ck, text: c['条款大致说明'] || '' })
      for (const sc of c['子条款列表'] || []) {
        const k3 = String(sc.locator ?? sc['子条款编号'])
        out.push({ key: k3, level: 3, kind: 'clause', num: sc['子条款编号'] || k3, text: sc['子条款大致说明'] || '' })
        for (const ssc of sc['子子条款列表'] || []) {
          const k4 = String(ssc.locator ?? ssc['子子条款编号'])
          out.push({ key: k4, level: 4, kind: 'clause', num: ssc['子子条款编号'] || k4, text: ssc['子子条款大致说明'] || '' })
        }
      }
    }
  }
  const footer = String(t['合同尾部'] || '')
  if (footer) out.push({ key: 'footer', level: 0, kind: 'footer', text: footer })
  return out
}

// 基于内容的 LCS 对齐 diff：插入导致 locator 整体后移时，不会把后续条款误判为“全部修改”
export function diff(prevDoc, nextDoc) {
  const a = flatten(prevDoc)
  const b = flatten(nextDoc)
  if (!a.length) return b.map((n) => ({ ...n, status: 'same' }))
  const sig = (n) => `${n.level}|${n.kind}|${n.text}`
  const n = a.length
  const m = b.length
  // 大文档保护：O(n*m) 超过阈值时退化为按 key 对比
  if (n * m > 4_000_000) return diffByKey(a, b)
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = sig(a[i]) === sig(b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const ops = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (sig(a[i]) === sig(b[j])) { ops.push({ ...b[j], status: 'same' }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ ...a[i], status: 'removed' }); i++ }
    else { ops.push({ ...b[j], status: 'added' }); j++ }
  }
  while (i < n) ops.push({ ...a[i++], status: 'removed' })
  while (j < m) ops.push({ ...b[j++], status: 'added' })
  // 相邻的 删除+新增 且同层级 → 合并为“修改”（显示删除线旧文 + 新文）
  const merged = []
  for (let k = 0; k < ops.length; k++) {
    const cur = ops[k]
    const nxt = ops[k + 1]
    if (cur.status === 'removed' && nxt && nxt.status === 'added' && nxt.level === cur.level && nxt.kind === cur.kind) {
      merged.push({ ...nxt, status: 'modified', oldText: cur.text })
      k++
    } else merged.push(cur)
  }
  return merged
}

function diffByKey(a, b) {
  const am = new Map(a.map((x) => [x.key, x]))
  const bm = new Set(b.map((x) => x.key))
  const out = b.map((x) => {
    const old = am.get(x.key)
    if (!old) return { ...x, status: 'added' }
    if (old.text !== x.text) return { ...x, status: 'modified', oldText: old.text }
    return { ...x, status: 'same' }
  })
  for (const x of a) if (!bm.has(x.key)) out.push({ ...x, status: 'removed' })
  return out
}

export function stats(ops) {
  const s = { added: 0, removed: 0, modified: 0 }
  for (const o of ops) if (s[o.status] !== undefined) s[o.status]++
  return s
}
