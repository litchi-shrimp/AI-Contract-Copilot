"""生成评测报告 report.html（单文件、零外部依赖、可离线打开）

    python -m evals.report [--in evals/results/eval_results.json] [--out evals/report.html]
"""
import argparse
import html
import json
import math
from pathlib import Path

from .common import EVAL_DIR, load_json


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def pct(x, d=1) -> str:
    return "—" if x is None or x != x else f"{x * 100:.{d}f}%"


def num(x, d=2) -> str:
    return "—" if x is None or x != x else f"{x:.{d}f}"


def signed_pct(x) -> str:
    return "—" if x is None or x != x else (f"+{x * 100:.1f}%" if x >= 0 else f"{x * 100:.1f}%")


def bar_pair(label, a, b, a_name="本系统", b_name="单 LLM") -> str:
    def w(v):
        return 0 if v is None or v != v else max(0.0, min(1.0, v)) * 100
    return f"""<div class="bp"><div class="bp-l">{esc(label)}</div><div class="bp-bars">
<div class="bp-row"><span class="tag sys">{esc(a_name)}</span><div class="track"><div class="fill sys" style="width:{w(a):.1f}%"></div></div><b>{pct(a)}</b></div>
<div class="bp-row"><span class="tag base">{esc(b_name)}</span><div class="track"><div class="fill base" style="width:{w(b):.1f}%"></div></div><b>{pct(b)}</b></div>
</div></div>"""


def radar(dims, sys_vals, base_vals, size=320) -> str:
    n = len(dims)
    cx = cy = size / 2
    r = size / 2 - 52

    def pt(i, v):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        v = 0 if v is None or v != v else v
        return cx + r * v * math.cos(ang), cy + r * v * math.sin(ang)
    grid = ""
    for lv in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{pt(i, lv)[0]:.1f},{pt(i, lv)[1]:.1f}" for i in range(n))
        grid += f'<polygon points="{pts}" class="rg"/>'
    axes = ""
    for i, d in enumerate(dims):
        x, y = pt(i, 1.0)
        lx, ly = pt(i, 1.2)
        axes += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" class="ra"/>'
        anchor = "middle" if abs(lx - cx) < 8 else ("start" if lx > cx else "end")
        axes += f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="{anchor}" class="rl">{esc(d)}</text>'

    def poly(vals, cls):
        pts = " ".join(f"{pt(i, v)[0]:.1f},{pt(i, v)[1]:.1f}" for i, v in enumerate(vals))
        dots = "".join(f'<circle cx="{pt(i, v)[0]:.1f}" cy="{pt(i, v)[1]:.1f}" r="3" class="{cls}"/>'
                       for i, v in enumerate(vals))
        return f'<polygon points="{pts}" class="{cls}"/>{dots}'
    return (f'<svg viewBox="0 0 {size} {size}" class="radar" role="img" aria-label="六维雷达图">{grid}{axes}'
            f'{poly(base_vals, "pb")}{poly(sys_vals, "ps")}</svg>')


def spark(values, w=160, h=40, cls="sl") -> str:
    vals = [v for v in values if v is not None]
    if len(vals) < 1:
        return ""
    mx = max(vals) or 1
    step = w / max(1, len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{h - 4 - (v / mx) * (h - 10):.1f}" for i, v in enumerate(vals))
    dots = "".join(f'<circle cx="{i * step:.1f}" cy="{h - 4 - (v / mx) * (h - 10):.1f}" r="2.6"/>'
                   for i, v in enumerate(vals))
    return f'<svg viewBox="-4 0 {w + 8} {h}" class="{cls}"><polyline points="{pts}"/>{dots}</svg>'


def kpi(label, value, sub="", tone="") -> str:
    return f'<div class="kpi {tone}"><div class="kpi-l">{esc(label)}</div><div class="kpi-v">{value}</div><div class="kpi-s">{sub}</div></div>'


def ci(stat) -> str:
    lo, hi = (stat or {}).get("ci95", [None, None])
    return "" if lo is None or lo != lo else f"95% CI [{pct(lo)}, {pct(hi)}]"


def build(data: dict) -> str:
    meta, agg, cases = data["meta"], data["aggregate"], data["cases"]
    e, o, c, hl, jr = agg["e2e"], agg["outline"], agg["completion"], agg["headline"], agg["judge_reliability"]
    rub_e, rub_o = data["rubrics"]["e2e"], data["rubrics"]["outline"]
    dim_names = [rub_e["dimensions"][d]["name"] for d in ("D1", "D2", "D3", "D4", "D5", "D6")]
    sys_d = [e[f"system_{d}"]["mean"] for d in ("D1", "D2", "D3", "D4", "D5", "D6")]
    base_d = [e[f"baseline_{d}"]["mean"] for d in ("D1", "D2", "D3", "D4", "D5", "D6")]
    pw = agg["pairwise"]["overall"]
    n_pw = pw["A"] + pw["B"] + pw["TIE"]

    # ---------------- 用例明细
    case_rows = ""
    for cs in cases:
        s = cs.get("e2e", {}).get("system", {})
        b = cs.get("e2e", {}).get("baseline", {})
        pwc = cs.get("e2e", {}).get("pairwise", {}).get("dims", {}).get("__overall__", {})
        win = {"A": '<span class="pill ok">本系统</span>', "B": '<span class="pill bad">单 LLM</span>',
               "TIE": '<span class="pill">平局</span>'}.get(pwc.get("final"), "—")
        case_rows += (f"<tr><td><b>{esc(cs['title'])}</b><div class='muted'>{esc(cs['id'])} · 大纲策略 "
                      f"{esc(s.get('outline_strategy', '—'))} · 命中模板 {esc(s.get('match_count', '—'))}</div></td>"
                      f"<td>{pct(s.get('rules', {}).get('element', {}).get('score'))}</td>"
                      f"<td>{pct(b.get('rules', {}).get('element', {}).get('score'))}</td>"
                      f"<td>{pct(s.get('rules', {}).get('clause', {}).get('score'))}</td>"
                      f"<td>{pct(b.get('rules', {}).get('clause', {}).get('score'))}</td>"
                      f"<td><b>{pct(s.get('rubric', {}).get('total'))}</b></td>"
                      f"<td>{pct(b.get('rubric', {}).get('total'))}</td><td>{win}</td>"
                      f"<td>{num((s.get('duration_s') or 0) / 60, 1)} min<div class='muted'>"
                      f"{(s.get('tokens') or 0) / 1000:.0f}k tok</div></td></tr>")

    # ---------------- 要素明细（可定位）
    element_blocks = ""
    for cs in cases:
        s = cs.get("e2e", {}).get("system", {})
        b = cs.get("e2e", {}).get("baseline", {})
        srows = {r["id"]: r for r in s.get("rules", {}).get("element", {}).get("rows", [])}
        brows = {r["id"]: r for r in b.get("rules", {}).get("element", {}).get("rows", [])}
        trs = ""
        for eid, r in srows.items():
            br = brows.get(eid, {})
            trs += (f"<tr><td>{esc(r['desc'])}</td><td>{'✅' if r['hit'] else '❌'} "
                    f"<code>{esc(r.get('locator') or '')}</code><div class='ev'>{esc(r.get('evidence', ''))}</div></td>"
                    f"<td>{'✅' if br.get('hit') else '❌'}<div class='ev'>{esc(br.get('evidence', ''))}</div></td></tr>")
        if trs:
            element_blocks += (f"<details><summary>{esc(cs['title'])} — 本系统 {s['rules']['element']['hits']}/"
                               f"{s['rules']['element']['n']} · 单 LLM {b.get('rules', {}).get('element', {}).get('hits', '—')}"
                               f"/{b.get('rules', {}).get('element', {}).get('n', '—')}</summary><table class='t sm'>"
                               f"<tr><th>要素</th><th>本系统（定位 · 证据）</th><th>单 LLM</th></tr>{trs}</table></details>")

    # ---------------- Rubric 审计（检查项 → 投票 → 证据）
    audit_blocks = ""
    for cs in cases:
        for who, label in (("system", "本系统"), ("baseline", "单 LLM")):
            rub = cs.get("e2e", {}).get(who, {}).get("rubric")
            if not rub:
                continue
            trs = ""
            for it in rub["items"]:
                v = "".join("●" if x else "○" for x in it["votes"])
                fin = "通过" if it["final"] == 1 else ("未通过" if it["final"] == 0 else "N/A")
                evs = [r for r in it["raw"] if r.get("verdict") == "yes" and r.get("grounded")]
                ev = evs[0] if evs else (it["raw"][0] if it["raw"] else {})
                ung = f"<span class='pill warn'>证据未核验 ×{it['ungrounded']}</span>" if it["ungrounded"] else ""
                trs += (f"<tr><td><code>{esc(it['id'])}</code></td><td>{esc(it['text'])}</td>"
                        f"<td class='votes'>{v}</td><td><span class='pill {'ok' if it['final'] == 1 else 'bad'}'>"
                        f"{fin}</span>{ung}</td><td><code>{esc(ev.get('locator', ''))}</code><div class='ev'>"
                        f"{esc(ev.get('evidence', '') or ev.get('reason', ''))}</div></td></tr>")
            keys = ", ".join(a["cache_key"][:10] for a in rub.get("audits", []))
            audit_blocks += (f"<details><summary>{esc(cs['title'])} · {label} · 综合 {pct(rub['total'])}"
                             f"</summary><div class='muted'>审计键（evals/cache/judge.jsonl）：{esc(keys)}</div>"
                             f"<table class='t sm'><tr><th>ID</th><th>检查项</th><th>投票</th><th>结论</th>"
                             f"<th>证据（locator · 原文）</th></tr>{trs}</table></details>")

    # ---------------- 成对盲评理由
    pair_blocks = ""
    for cs in cases:
        pwd = cs.get("e2e", {}).get("pairwise", {}).get("dims", {})
        if not pwd:
            continue
        trs = ""
        for d in rub_e["pairwise_dims"] + [{"id": "__overall__", "name": "总体"}]:
            r = pwd.get(d["id"], {})
            name = {"A": "本系统", "B": "单 LLM", "TIE": "平局"}
            trs += (f"<tr><td>{esc(d['name'])}</td><td>{name.get(r.get('ab'), '—')}</td>"
                    f"<td>{name.get(r.get('ba_swapped'), '—')}</td><td><b>{name.get(r.get('final'), '—')}</b>"
                    f"{'' if r.get('consistent') else ' <span class=pill>位置不一致→平局</span>'}</td>"
                    f"<td class='ev'>{esc(r.get('reason_ab', ''))}</td></tr>")
        pair_blocks += (f"<details><summary>{esc(cs['title'])}</summary><table class='t sm'><tr><th>维度</th>"
                        f"<th>A/B 顺序</th><th>B/A 顺序（换回）</th><th>最终</th><th>理由（A/B 顺序）</th></tr>"
                        f"{trs}</table></details>")

    # ---------------- 大纲 & WebSearch
    od = ("O1", "O2", "O3")
    outline_rows = ""
    for d in od:
        outline_rows += (f"<tr><td>{esc(rub_o['dimensions'][d]['name'])}<div class='muted'>"
                         f"{esc(rub_o['dimensions'][d]['desc'])}</div></td>"
                         f"<td>{pct(o[f'system_{d}']['mean'])}</td><td>{pct(o[f'adversarial_initial_{d}']['mean'])}</td>"
                         f"<td>{pct(o[f'adversarial_{d}']['mean'])}</td><td>{pct(o[f'baseline_{d}']['mean'])}</td></tr>")
    ws_rows = ""
    for cs in cases:
        ws = cs.get("outline", {}).get("websearch")
        if not ws:
            continue
        ws_rows += (f"<tr><td>{esc(cs['title'])}</td><td>{ws['results']}</td><td>{ws['relevant']}</td>"
                    f"<td>{pct(ws['noise_rate'])}</td><td>{pct(ws['utilization'])}</td>"
                    f"<td>{ws['rounds_to_converge']}{'' if ws['converged'] else ' <span class=pill warn>未收敛</span>'}</td>"
                    f"<td>{spark(ws['issues_per_round'])}<div class='muted'>漏洞数/轮："
                    f"{esc(' → '.join(str(x) for x in ws['issues_per_round']))}</div></td></tr>")
    ws_detail = ""
    for cs in cases:
        ws = cs.get("outline", {}).get("websearch")
        if not ws or not ws["rows"]:
            continue
        trs = "".join(f"<tr><td>{esc(r['title'][:40])}<div class='muted'>{esc(r['url'][:70])}</div></td>"
                      f"<td class='votes'>{''.join('●' if v else '○' for v in r['votes'])}</td>"
                      f"<td>{'相关' if r['relevant'] else '<span class=pill bad>噪声</span>'}</td>"
                      f"<td>{'✅ ' + esc('、'.join(r['absorbed_topics'][:5])) if r['absorbed'] else '—'}</td>"
                      f"<td class='ev'>{esc(r['reason'])}</td></tr>" for r in ws["rows"])
        ws_detail += (f"<details><summary>{esc(cs['title'])} 检索结果逐条判定</summary><table class='t sm'>"
                      f"<tr><th>结果</th><th>相关性投票</th><th>判定</th><th>被大纲吸收的主题</th><th>理由</th></tr>"
                      f"{trs}</table></details>")

    # ---------------- 工具调用
    tool_rows = ""
    for cs in cases:
        dr = cs.get("completion", {}).get("drafter", {})
        ld = cs.get("completion", {}).get("leader", {})
        fs = cs.get("completion", {}).get("final_state", {})
        if not dr:
            continue
        tool_rows += (f"<tr><td>{esc(cs['title'])}</td><td>{dr.get('calls', 0)}</td>"
                      f"<td>{pct(dr.get('invalid_rate'))}</td><td>{pct(dr.get('batch_utilization'))}"
                      f"<div class='muted'>均 {num(dr.get('avg_batch'), 1)} 个/次</div></td>"
                      f"<td>{pct(dr.get('detour_rate'))}</td><td>{pct(dr.get('landing', {}).get('landing_rate'))}</td>"
                      f"<td>{ld.get('plan', {}).get('executed', '—')}/{ld.get('plan', {}).get('decisions', '—')}</td>"
                      f"<td>{pct(fs.get('outline'))} → {pct(fs.get('after_draft'))} → <b>{pct(fs.get('after_revision'))}</b></td></tr>")
    codes = {}
    for cs in cases:
        for layer in ("drafter", "leader"):
            for k, v in (cs.get("completion", {}).get(layer, {}).get("error_codes", {}) or {}).items():
                codes[k] = codes.get(k, 0) + v
    code_list = "".join(f"<li><code>{esc(k)}</code> × {v}</li>" for k, v in sorted(codes.items(), key=lambda x: -x[1])) or "<li>无</li>"

    # ---------------- Rubric 定义
    def rubric_table(rub):
        out = ""
        for did, d in rub["dimensions"].items():
            items = "".join(f"<li><code>{esc(it['id'])}</code> {esc(it['text'])} <span class='muted'>w={it.get('weight', 1)}</span></li>"
                            for it in d["items"])
            out += (f"<div class='rub'><div class='rub-h'><b>{esc(did)} {esc(d['name'])}</b><span>权重 {d['weight']}</span></div>"
                    f"<div class='muted'>{esc(d['desc'])}</div><ul>{items}</ul></div>")
        return out

    # ---------------- 迭代对比（评测驱动修复）
    hist = data.get("history") or []
    hist_html = ""
    if hist:
        rows = ""
        for h in hist:
            rows += (f"<tr><td><b>{esc(h['label'])}</b><div class='muted'>{esc(h.get('note', ''))}</div></td>"
                     f"<td>{pct(h.get('element_accuracy'))}</td><td>{pct(h.get('clause_recall'))}</td>"
                     f"<td>{pct(h.get('rubric_system'))}</td><td>{pct(h.get('outline_system'))}</td>"
                     f"<td>{pct(h.get('rubric_baseline'))}</td></tr>")
        hist_html = (f"<section id='iter'><h2>评测驱动迭代 <span class='sub'>历史版本（模型配置 / Rubric 版本不同，分数仅作过程参考）</span></h2>"
                     f"<div class='card'><table class='t'><tr><th>版本</th><th>要素准确率</th><th>必备条款召回</th>"
                     f"<th>端到端 Rubric</th><th>大纲 Rubric</th><th>单 LLM 端到端</th></tr>{rows}</table>"
                     f"<ul class='muted small'>"
                     f"<li><b>v1 → v2（修复系统）</b>：审计明细显示大纲阶段“脱敏规则”把用户已给定的金额/期限/比例替换成占位符"
                     f"（如 {{月租金金额}}），O3.2 在 5/5 用例失败；WebSearch 检索词拼入分类路径导致检索结果全部为噪声；"
                     f"extra_need 含空值导致模板匹配与对抗大纲失败。修复后重新生成系统产物。</li>"
                     f"<li><b>v2 → v3（校准 Rubric + 统一模型）</b>：v2 审计发现 D2.2/D5.1/D5.3/D2.8 把“用户未提供的值使用占位符”判为不通过，"
                     f"而单 LLM 因编造具体数值得分——检查项混淆了“缺失”与“待填”（见 <code>evals/rubrics/CALIBRATION.md</code>）。"
                     f"v3 将 Rubric 升级为 e2e-v1.3，并把系统、基线、Judge 全部统一为 deepseek-flash 后重新生成与评测（v1/v2 中部分 Agent、基线与 Judge 使用 v4-pro）。"
                     f"<b>不同 Rubric 版本 / 模型配置的分数不可直接比较</b>。</li>"
                     f"<li><b>v3 → v4（修复系统）</b>：v3 审计发现设备采购合同的单价 28 万 / 总价 280 万被写成占位符——用户原话中有，"
                     f"但需求摘要改写时丢失，下游 Agent 只看到摘要。修复：将用户原话注入大纲/起草/修订提示词。"
                     f"大纲阶段要素准确率 97.2% → 100%。同时修正了“交货期”要素的检测正则（规则误判）。本报告正文为 v4 结果。</li></ul>"
                     f"</div></section>")

    rel = jr
    kappa_tone = "ok" if rel["fleiss_kappa"] == rel["fleiss_kappa"] and rel["fleiss_kappa"] >= 0.6 else "warn"
    css = CSS
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Contract Copilot · 三层评测报告</title><style>{css}</style></head><body>
<header class="hero"><div class="wrap">
<div class="eyebrow">EVALUATION REPORT · {esc(meta['generated_at'])}</div>
<h1>AI Contract Copilot 三层评测报告</h1>
<p class="lead">针对法务合同生成“不可量化、出错难定位”的问题，按链路拆分为 <b>端到端</b>、<b>大纲阶段</b>、<b>补全阶段（工具调用）</b> 三层独立评测。
规则硬指标零方差可复算；主观质量采用 <b>分析型 Rubric + 证据绑定 + 多次采样投票 + 双向成对盲评</b> 的 LLM-as-Judge 协议，所有判定可追溯到原文与缓存审计键。</p>
<div class="metaline"><span>用例 {len(cases)} 个</span><span>Judge {esc(meta['judge_model'])} · T={meta['judge_temperature']} · k={meta['samples']}</span>
<span>Rubric {esc(meta['rubrics']['e2e']['version'])} <code>{esc(meta['rubrics']['e2e']['hash'])}</code> / {esc(meta['rubrics']['outline']['version'])} <code>{esc(meta['rubrics']['outline']['hash'])}</code></span>
<span>基线：{esc(meta['baseline'])}</span></div>
</div></header>
<main class="wrap">
<nav class="toc"><a href="#sum">总览</a><a href="#l1">① 端到端</a><a href="#l2">② 大纲阶段</a><a href="#l3">③ 补全阶段</a><a href="#rel">Judge 可靠性</a><a href="#iter">迭代对比</a><a href="#method">方法与 Rubric</a><a href="#audit">审计明细</a></nav>

<section id="sum"><h2>总览</h2><div class="kpis">
{kpi("要素准确率", pct(hl['element_accuracy']), ci(e['system_element_acc']) + f"<br>单 LLM {pct(e['baseline_element_acc']['mean'])}", "ok")}
{kpi("必备条款召回", pct(hl['clause_recall']), ci(e['system_clause_recall']) + f"<br>单 LLM {pct(e['baseline_clause_recall']['mean'])}", "ok")}
{kpi("6 维 Rubric 综合", pct(hl['rubric_system']), f"单 LLM {pct(hl['rubric_baseline'])}<br>相对提升 <b>{signed_pct(hl['rubric_lift'])}</b>", "brand")}
{kpi("成对盲评（双向一致）", f"{pw['A']}<small> 胜</small> {pw['TIE']}<small> 平</small> {pw['B']}<small> 负</small>", f"{n_pw} 组 · 位置不一致 {pw['inconsistent']} 组按平局计")}
{kpi("大纲 3 维 Rubric", pct(o['system']['mean']), f"单 LLM {pct(o['baseline']['mean'])}<br>相对提升 <b>{signed_pct(hl['outline_lift'])}</b>")}
{kpi("WebSearch 噪声 / 利用", f"{pct(o['noise_rate']['mean'], 0)} / {pct(o['utilization']['mean'], 0)}", f"平均收敛轮数 {num(o['rounds']['mean'], 1)}")}
{kpi("工具非法操作率", pct(c['invalid_rate']['mean']), f"批量利用率 {pct(c['batch_utilization']['mean'])} · 绕路率 {pct(c['detour_rate']['mean'])}")}
{kpi("Judge 一致性 κ", num(rel['fleiss_kappa']), f"k 次全一致 {pct(rel['unanimity'])} · 证据核验失败 {pct(rel['ungrounded_rate'])}", kappa_tone)}
</div>
<div class="note">说明：所有数值由 <code>python -m evals.run_eval</code> 实测得到（非预设目标）。样本量为 {len(cases)} 个用例，置信区间较宽，结论用于指导迭代而非统计显著性声明。</div>
</section>

<section id="l1"><h2>① 端到端评测 <span class="sub">完整合同 · 本系统（8 步多 Agent）vs 单 LLM 单次生成</span></h2>
<div class="grid2"><div class="card"><h3>规则硬指标</h3>
{bar_pair("要素准确率", e['system_element_acc']['mean'], e['baseline_element_acc']['mean'])}
{bar_pair("必备条款召回率", e['system_clause_recall']['mean'], e['baseline_clause_recall']['mean'])}
<p class="muted">要素：用户需求中的金额/期限/比例/条件，中文数字归一化后正则匹配，并定位到 locator；条款：按合同类型必备清单在“章节标题 + 条款概要”中匹配。</p></div>
<div class="card center"><h3>6 维 Rubric</h3>{radar(dim_names, sys_d, base_d)}
<div class="legend"><span><i class="sw ps"></i>本系统</span><span><i class="sw pb"></i>单 LLM</span></div></div></div>
<div class="card"><h3>分维度得分</h3><table class="t"><tr><th>维度</th><th>权重</th><th>本系统</th><th>单 LLM</th><th>差值</th></tr>
{''.join(f"<tr><td>{esc(rub_e['dimensions'][d]['name'])}<div class='muted'>{esc(rub_e['dimensions'][d]['desc'])}</div></td><td>{rub_e['dimensions'][d]['weight']}</td><td><b>{pct(e[f'system_{d}']['mean'])}</b></td><td>{pct(e[f'baseline_{d}']['mean'])}</td><td>{signed_pct((e[f'system_{d}']['mean'] - e[f'baseline_{d}']['mean']) if e[f'system_{d}']['mean'] == e[f'system_{d}']['mean'] and e[f'baseline_{d}']['mean'] == e[f'baseline_{d}']['mean'] else float('nan'))}</td></tr>" for d in ("D1", "D2", "D3", "D4", "D5", "D6"))}
</table></div>
<div class="card"><h3>用例明细</h3><div class="scroll"><table class="t"><tr><th>用例</th><th>要素·系统</th><th>要素·基线</th><th>条款·系统</th><th>条款·基线</th><th>Rubric·系统</th><th>Rubric·基线</th><th>盲评</th><th>成本</th></tr>{case_rows}</table></div></div>
<div class="card"><h3>要素逐项定位（出错可定位到条款）</h3>{element_blocks}</div>
<div class="card"><h3>成对盲评理由</h3>{pair_blocks or '<p class="muted">无</p>'}</div>
</section>

<section id="l2"><h2>② 大纲阶段评测 <span class="sub">结构质量 3 维 Rubric · 多轮 WebSearch</span></h2>
<div class="kpis three">
{kpi("系统大纲（实际路径）", pct(o['system']['mean']), f"单 LLM 大纲 {pct(o['baseline']['mean'])} · 提升 <b>{signed_pct(hl['outline_lift'])}</b>", "brand")}
{kpi("对抗式：初稿 → 收敛", f"{pct(o['adversarial_initial']['mean'])} → {pct(o['adversarial']['mean'])}", f"ModelB 审查 + ModelA 工具修复带来 <b>{signed_pct(hl['adversarial_lift_vs_initial'])}</b>")}
{kpi("收敛轮数", num(o['rounds']['mean'], 1), f"噪声率 {pct(o['noise_rate']['mean'])} · 利用率 {pct(o['utilization']['mean'])}")}
</div>
<div class="card"><table class="t"><tr><th>维度</th><th>系统大纲</th><th>对抗·初稿</th><th>对抗·收敛</th><th>单 LLM</th></tr>{outline_rows}</table></div>
<div class="card"><h3>多轮 WebSearch（零模板对抗路径）</h3><div class="scroll"><table class="t"><tr><th>用例</th><th>检索结果</th><th>相关</th><th>噪声率</th><th>利用率</th><th>收敛轮数</th><th>漏洞收敛曲线</th></tr>{ws_rows}</table></div>
<p class="muted">噪声率 = Judge 判为无关的结果 / 全部结果（k 次投票多数决）；利用率 = 相关结果中，其条款主题词（≥2 个）出现在 ModelA 初稿大纲中的比例；收敛轮数 = ModelB 首次判“完备”的轮次（未收敛记上限）。</p>
{ws_detail}</div>
</section>

<section id="l3"><h2>③ 补全阶段评测 <span class="sub">Drafter / Leader 的 update · insert · delete 工具调用</span></h2>
<div class="kpis">
{kpi("终态要素准确率", pct(c['element_after_revision']['mean']), f"大纲 {pct(c['element_outline']['mean'])} → 起草 {pct(c['element_after_draft']['mean'])} → 修订", "ok")}
{kpi("非法操作率", pct(c['invalid_rate']['mean']), "被 Harness 拒绝 / 全部调用")}
{kpi("批量利用率", pct(c['batch_utilization']['mean']), f"平均每次 {num(c['avg_batch']['mean'], 1)} 个 locator")}
{kpi("绕路率", pct(c['detour_rate']['mean']), "失败/拒绝 + 空操作 + 同位重写 + 重复调用")}
{kpi("编辑落地率", pct(c['landing_rate']['mean']), "成功编辑在终稿中保留的比例")}
{kpi("自纠正率", pct(c['self_correction_rate']['mean']), f"失败后下一次成功 · 重复失败 {pct(c['repeat_failure_rate']['mean'])}")}
{kpi("Leader 计划执行率", pct(c['leader_plan_execution']['mean']), f"Leader 非法操作率 {pct(c['leader_invalid_rate']['mean'])}")}
</div>
<div class="card"><div class="scroll"><table class="t"><tr><th>用例</th><th>Drafter 调用</th><th>非法率</th><th>批量利用</th><th>绕路率</th><th>落地率</th><th>Leader 执行</th><th>要素准确率演进</th></tr>{tool_rows}</table></div>
<div class="grid2"><div><h4>拒绝 / 失败原因分布（Harness error_code）</h4><ul class="codes">{code_list}</ul></div>
<div><h4>指标口径</h4><ul class="muted small"><li>终态正确率借鉴 τ-bench：不比对轨迹，只核验终稿状态（要素是否落地）</li><li>绕路率衡量轨迹效率：对完成任务无贡献的调用占比</li><li>所有工具调用经 ToolHarness，失败操作已自动回滚，不会污染合同</li></ul></div></div></div>
</section>

<section id="rel"><h2>Judge 可靠性与可复现性</h2><div class="kpis">
{kpi("Fleiss κ（检查项）", num(rel['fleiss_kappa']), f"{rel['items']} 个检查项 × k={meta['samples']}", kappa_tone)}
{kpi("k 次全一致率", pct(rel['unanimity']), "同一检查项多次采样结论完全相同")}
{kpi("证据核验失败", f"{rel['ungrounded_yes']} / {rel['yes_total']}", "“通过”但引用不在原文 → 自动判不通过")}
{kpi("检索相关性 κ", num(rel['relevance_kappa']), "WebSearch 相关性二值判定一致性")}
{kpi("Judge 调用 / 缓存命中", f"{meta['judge_stats']['calls']} / {meta['judge_stats']['cache_hits']}", f"解析失败 {meta['judge_stats']['parse_failures']}")}
</div>
<div class="card"><ul class="method">
<li><b>可复现</b>：Judge 模型、温度、采样数、Rubric 内容哈希写入报告；每次调用以 (rubric 版本, 模型, prompt 哈希, 采样序号) 为键缓存原始输出，重跑得到完全一致的结果。</li>
<li><b>可审计</b>：每个检查项保留 k 次投票、逐次证据（locator + 原文）、理由与缓存键；成对比较保留 A/B 与 B/A 两次结论。</li>
<li><b>抗偏差</b>：检查项每次随机打乱顺序（位置偏差）；成对比较双向交换，不一致计平局（位置偏差）；规则“不因篇幅给分”（长度偏差）；“通过”必须给出可核验原文引用（幻觉式通过）。</li>
<li><b>κ 解读</b>：κ ≥ 0.8 几乎完全一致、0.6–0.8 高度一致、0.4–0.6 中等；低于 0.6 的检查项需改写措辞并升级 Rubric 版本。</li>
</ul></div></section>

{hist_html}
<section id="method"><h2>方法与 Rubric 定义</h2>
<div class="grid2"><div class="card"><h3>端到端 6 维 Rubric <code>{esc(rub_e['version'])}</code></h3>{rubric_table(rub_e)}
<p class="muted">另：每条用户特殊需求自动生成检查项 D1.S*（权重 2）。</p></div>
<div class="card"><h3>大纲 3 维 Rubric <code>{esc(rub_o['version'])}</code></h3>{rubric_table(rub_o)}
<p class="muted">另：每条用户特殊需求自动生成检查项 O1.S*（权重 1.5）。</p>
<h3>评分公式</h3><p class="formula">维度分 = Σ w<sub>i</sub>·pass<sub>i</sub> / Σ w<sub>i</sub>（pass 为 k 次投票多数决，N/A 不计）<br>综合分 = Σ W<sub>d</sub>·维度分<sub>d</sub> / Σ W<sub>d</sub></p>
<h3>对比对象</h3><p class="muted">{esc(meta['baseline'])}。本系统：{esc(meta['system_models'])}。</p></div></div>
</section>

<section id="audit"><h2>审计明细 <span class="sub">逐检查项 · 投票 · 证据</span></h2><div class="card">{audit_blocks}</div></section>
<footer>Generated by <code>evals/report.py</code> · 数据：<code>evals/results/eval_results.json</code> · 缓存：<code>evals/cache/judge.jsonl</code></footer>
</main></body></html>"""


CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#0f172a;--muted:#64748b;--line:#e5e7ef;--brand:#4f46e5;--brand2:#7c3aed;--ok:#059669;--bad:#dc2626;--warn:#d97706;--base:#94a3b8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
.hero{background:linear-gradient(135deg,#1e1b4b 0%,#312e81 45%,#4f46e5 100%);color:#fff;padding:44px 0 36px}
.eyebrow{letter-spacing:.18em;font-size:12px;opacity:.75}.hero h1{margin:8px 0 10px;font-size:30px}
.lead{max-width:880px;opacity:.92;margin:0 0 16px}.metaline{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:12px;opacity:.85}
.metaline code{background:rgba(255,255,255,.15);color:#fff}
.toc{position:sticky;top:0;z-index:5;display:flex;gap:4px;flex-wrap:wrap;background:rgba(246,247,251,.92);backdrop-filter:blur(8px);padding:10px 0;border-bottom:1px solid var(--line);margin-bottom:8px}
.toc a{color:var(--ink);text-decoration:none;padding:5px 12px;border-radius:999px;font-size:13px}.toc a:hover{background:#e0e7ff;color:var(--brand)}
section{padding:18px 0}h2{font-size:21px;margin:10px 0 14px}h2 .sub{font-size:13px;color:var(--muted);font-weight:400;margin-left:8px}
h3{font-size:15px;margin:0 0 12px}h4{font-size:13px;margin:12px 0 6px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.kpis.three{grid-template-columns:repeat(3,1fr)}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.kpi-l{font-size:12px;color:var(--muted)}.kpi-v{font-size:26px;font-weight:700;margin:4px 0 2px;letter-spacing:-.01em}.kpi-v small{font-size:13px;font-weight:500;color:var(--muted)}
.kpi-s{font-size:12px;color:var(--muted)}.kpi.ok .kpi-v{color:var(--ok)}.kpi.brand .kpi-v{color:var(--brand)}.kpi.warn .kpi-v{color:var(--warn)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:12px 0;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.card.center{text-align:center}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.grid2 .card{margin:0}
.note{margin-top:12px;font-size:12px;color:var(--muted);background:#eef2ff;border:1px solid #e0e7ff;border-radius:10px;padding:8px 12px}
.bp{display:grid;grid-template-columns:120px 1fr;gap:10px;align-items:center;margin:10px 0}.bp-l{font-weight:600;font-size:13px}
.bp-row{display:grid;grid-template-columns:62px 1fr 56px;align-items:center;gap:8px;margin:3px 0;font-size:12px}
.track{height:10px;background:#eef0f6;border-radius:999px;overflow:hidden}.fill{height:100%;border-radius:999px}
.fill.sys{background:linear-gradient(90deg,var(--brand),var(--brand2))}.fill.base{background:var(--base)}
.tag{font-size:11px;padding:1px 6px;border-radius:6px;text-align:center}.tag.sys{background:#e0e7ff;color:var(--brand)}.tag.base{background:#f1f5f9;color:#475569}
.radar{width:100%;max-width:360px}.rg{fill:none;stroke:#e5e7ef}.ra{stroke:#e5e7ef}.rl{font-size:12px;fill:#334155}
.ps{fill:rgba(79,70,229,.18);stroke:var(--brand);stroke-width:2}circle.ps{fill:var(--brand)}
.pb{fill:rgba(148,163,184,.18);stroke:var(--base);stroke-width:2;stroke-dasharray:4 3}circle.pb{fill:var(--base)}
.legend{display:flex;gap:16px;justify-content:center;font-size:12px;color:var(--muted)}.sw{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-2px}
.sw.ps{background:var(--brand)}.sw.pb{background:var(--base)}
.t{width:100%;border-collapse:collapse;font-size:13px}.t th{text-align:left;font-weight:600;color:#475569;background:#f8fafc;border-bottom:1px solid var(--line);padding:8px 10px;white-space:nowrap}
.t td{border-bottom:1px solid #f1f3f8;padding:8px 10px;vertical-align:top}.t.sm{font-size:12px}.scroll{overflow-x:auto}
.muted{color:var(--muted);font-size:12px}.small{font-size:12px}.ev{color:#475569;font-size:12px;max-width:520px}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;background:#f1f5f9;padding:1px 5px;border-radius:5px}
.pill{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;background:#f1f5f9;color:#475569;margin-left:4px}
.pill.ok{background:#d1fae5;color:#065f46}.pill.bad{background:#fee2e2;color:#991b1b}.pill.warn{background:#fef3c7;color:#92400e}
.votes{font-family:ui-monospace,monospace;letter-spacing:2px;color:var(--brand)}
details{border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:0 12px;background:#fcfcfe}details[open]{background:#fff}
summary{cursor:pointer;padding:10px 0;font-weight:600}summary::marker{color:var(--brand)}
.sl{width:160px;height:40px}.sl polyline{fill:none;stroke:var(--brand);stroke-width:2}.sl circle{fill:var(--brand)}
.rub{border-top:1px dashed var(--line);padding:10px 0}.rub:first-of-type{border-top:0}.rub-h{display:flex;justify-content:space-between}.rub-h span{font-size:12px;color:var(--muted)}
.rub ul{margin:6px 0 0;padding-left:18px;font-size:12.5px}.rub li{margin:3px 0}
.formula{background:#f8fafc;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:13px}
.method li{margin:6px 0}.codes{columns:2;font-size:12px}
footer{color:var(--muted);font-size:12px;padding:28px 0 40px;border-top:1px solid var(--line);margin-top:20px}
@media (max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}}
@media print{.toc{display:none}.hero{-webkit-print-color-adjust:exact;print-color-adjust:exact}details{break-inside:avoid}}
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(EVAL_DIR / "results" / "eval_results.json"))
    ap.add_argument("--out", default=str(EVAL_DIR / "report.html"))
    ap.add_argument("--history", default=str(EVAL_DIR / "results" / "history.json"))
    a = ap.parse_args()
    data = load_json(a.inp)
    data["history"] = load_json(a.history, []) or []
    Path(a.out).write_text(build(data), encoding="utf-8")
    print("report →", a.out)
