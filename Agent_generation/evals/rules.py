"""规则硬指标（确定性、零方差、可复算）

- 要素准确率 element_accuracy：用户需求中的关键要素（金额/期限/比例/条件）是否以正确值写入合同
  · 中文数字先归一化为阿拉伯数字（cn2an），再按用例中的正则模式匹配；命中即“准确写入”
  · 每个要素记录命中片段（证据）与位置，便于定位
- 必备条款召回率 clause_recall：按合同类型的必备条款清单，在“章节标题+条款概要”中匹配
- 占位符残留率 placeholder_leak：用户已给定的要素，合同中仍用 {占位符} 代替（如 {月租金金额}）
- 结构合法性 structure_valid：locator 唯一、编号连续、首尾部存在
"""
import re
import warnings
from typing import Dict, List

from .common import render

warnings.filterwarnings("ignore", module="cn2an")


def to_arabic(text: str) -> str:
    try:
        import cn2an
        out = []
        for line in text.splitlines():
            try:
                out.append(cn2an.transform(line, "cn2an"))
            except Exception:
                out.append(line)
        return "\n".join(out)
    except Exception:
        return text


def _locate(contract: dict, snippet: str) -> str:
    tpl = contract.get("标准化模板文本", {})
    if snippet and snippet in to_arabic(str(tpl.get("合同首部", ""))):
        return "header"
    for s in tpl.get("正文章节", []):
        stack = [(s.get("locator"), s.get("章节标题", ""))]
        for c in s.get("条款列表", []) or []:
            stack.append((c.get("locator"), c.get("条款大致说明", "")))
            for sc in c.get("子条款列表", []) or []:
                stack.append((sc.get("locator"), sc.get("子条款大致说明", "")))
                for ssc in sc.get("子子条款列表", []) or []:
                    stack.append((ssc.get("locator"), ssc.get("子子条款大致说明", "")))
        for loc, txt in stack:
            if snippet and snippet[:20] in to_arabic(str(txt)):
                return str(loc)
    return "footer" if snippet and snippet in to_arabic(str(tpl.get("合同尾部", ""))) else "?"


def element_accuracy(contract: dict, elements: List[dict]) -> Dict:
    text = to_arabic(render(contract))
    flat = text.replace("\n", "。")
    rows = []
    for el in elements:
        hit = None
        for pat in el["patterns"]:
            m = re.search(pat, flat)
            if m:
                hit = m.group(0)
                break
        rows.append({"id": el["id"], "desc": el["desc"], "hit": bool(hit), "evidence": (hit or "")[:120],
                     "locator": _locate(contract, hit) if hit else ""})
    n = len(rows)
    return {"score": sum(r["hit"] for r in rows) / n if n else float("nan"), "n": n,
            "hits": sum(r["hit"] for r in rows), "rows": rows}


def _headings(contract: dict) -> str:
    tpl = contract.get("标准化模板文本", {})
    parts = [str(tpl.get("合同首部", ""))[:200]]
    for s in tpl.get("正文章节", []):
        parts.append(str(s.get("章节标题", "")))
        for c in s.get("条款列表", []) or []:
            parts.append(str(c.get("条款大致说明", ""))[:80])
            for sc in c.get("子条款列表", []) or []:
                parts.append(str(sc.get("子条款大致说明", ""))[:40])
    parts.append(str(tpl.get("合同尾部", "")))
    return "\n".join(parts)


def clause_recall(contract: dict, required: List[dict]) -> Dict:
    """必备条款召回：在章节标题 + 条款概要前缀中匹配（避免正文偶然出现关键词导致虚高）"""
    text = _headings(contract)
    rows = []
    for rc in required:
        hit = None
        for pat in rc["patterns"]:
            m = re.search(pat, text)
            if m:
                hit = m.group(0)
                break
        rows.append({"id": rc["id"], "name": rc["name"], "hit": bool(hit), "evidence": hit or ""})
    n = len(rows)
    return {"score": sum(r["hit"] for r in rows) / n if n else float("nan"), "n": n,
            "hits": sum(r["hit"] for r in rows), "rows": rows}


PLACEHOLDER_RE = re.compile(r"\{([^{}]{1,30})\}")
GIVEN_HINTS = ("租金", "押金", "违约金", "服务费", "单价", "总价", "价款", "期限", "宽限", "比例", "费率", "补偿",
               "数量", "可用率", "质保期", "交货期", "通知期")


def placeholder_leak(contract: dict) -> Dict:
    """统计“本应由用户需求给定”的占位符（名字含金额/期限类关键词）。仅作诊断指标"""
    text = render(contract)
    phs = PLACEHOLDER_RE.findall(text)
    leaked = sorted({p for p in phs if any(h in p for h in GIVEN_HINTS)})
    return {"placeholders": len(phs), "unique": len(set(phs)), "value_like": leaked[:40],
            "value_like_count": len(leaked)}


def structure_valid(contract: dict) -> Dict:
    from agent.utils.outline_manager import OutlineManager
    import json
    om = OutlineManager(data=json.loads(json.dumps(contract, ensure_ascii=False)))
    problems = om.validate_structure()
    secs = contract.get("标准化模板文本", {}).get("正文章节", [])
    locs = [str(s.get("locator", "")) for s in secs]
    if locs and locs != [str(i) for i in range(1, len(locs) + 1)]:
        problems.append("章节 locator 不连续")
    n_clauses = sum(len(s.get("条款列表", []) or []) for s in secs)
    chars = len(render(contract))
    return {"valid": not problems, "problems": problems, "sections": len(secs), "clauses": n_clauses,
            "chars": chars}
