"""合同大纲结构校验与规范化（多处复用：大纲生成、对抗生成、起草合并、评测）"""
from typing import Optional


def as_text(v) -> str:
    """模型偶尔把首部/尾部输出成对象或数组 → 统一转为多行文本"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return "\n".join(f"{k}：{as_text(x)}" if not isinstance(x, (dict, list)) else as_text(x) for k, x in v.items())
    if isinstance(v, list):
        return "\n".join(as_text(x) for x in v)
    return str(v)


def unwrap(data: dict) -> dict:
    """兼容 {"标准化模板文本": {...}} 与 直接 {...} 两种格式，返回带包裹的结构"""
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("标准化模板文本"), dict):
        return data
    if "正文章节" in data:
        return {"标准化模板文本": data}
    return data


def validate_outline(data, min_sections: int = 3) -> Optional[str]:
    """返回 None 表示合法，否则返回错误描述（供 LLM 自修复）"""
    data = unwrap(data)
    tpl = data.get("标准化模板文本") if isinstance(data, dict) else None
    if not isinstance(tpl, dict):
        return "缺少 标准化模板文本 对象"
    for k in ("合同首部", "正文章节", "合同尾部"):
        if k not in tpl:
            return f"标准化模板文本 缺少 {k}"
    secs = tpl["正文章节"]
    if not isinstance(secs, list) or len(secs) < min_sections:
        return f"正文章节 至少需要 {min_sections} 个章节"
    for i, s in enumerate(secs):
        if not isinstance(s, dict):
            return f"正文章节[{i}] 必须是对象"
        if not str(s.get("章节标题", "")).strip():
            return f"正文章节[{i}] 缺少 章节标题"
        if not isinstance(s.get("条款列表", []), list):
            return f"正文章节[{i}].条款列表 必须是数组"
        for j, c in enumerate(s.get("条款列表", [])):
            if not isinstance(c, dict):
                return f"正文章节[{i}].条款列表[{j}] 必须是对象"
            text = c.get("条款大致说明") or c.get("条款内容")
            if not str(text or "").strip():
                return f"正文章节[{i}].条款列表[{j}] 缺少 条款大致说明"
    return None


def normalize_outline(data: dict) -> dict:
    """规范化：补齐缺失字段、统一使用 *大致说明 字段、去掉 locator 交由 OutlineManager 重建"""
    data = unwrap(data)
    tpl = data.setdefault("标准化模板文本", {})
    for k in ("合同首部", "合同尾部"):
        tpl[k] = as_text(tpl.get(k, ""))
    secs = tpl.setdefault("正文章节", [])
    for s in secs:
        s.setdefault("章节编号", "")
        s.setdefault("章节标题", "")
        s.pop("locator", None)
        for c in s.setdefault("条款列表", []) or []:
            if "条款大致说明" not in c and "条款内容" in c:
                c["条款大致说明"] = c.pop("条款内容")
            c.setdefault("条款大致说明", "")
            c.pop("locator", None)
            for sc in c.setdefault("子条款列表", []) or []:
                if "子条款大致说明" not in sc and "子条款内容" in sc:
                    sc["子条款大致说明"] = sc.pop("子条款内容")
                sc.setdefault("子条款大致说明", "")
                sc.pop("locator", None)
                for ssc in sc.setdefault("子子条款列表", []) or []:
                    if "子子条款大致说明" not in ssc and "子子条款内容" in ssc:
                        ssc["子子条款大致说明"] = ssc.pop("子子条款内容")
                    ssc.setdefault("子子条款大致说明", "")
                    ssc.pop("locator", None)
    return data
