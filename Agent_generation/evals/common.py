"""评测公共工具：路径、合同渲染、文本归一化"""
import hashlib
import json
import re
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ARTIFACTS = EVAL_DIR / "artifacts"
CACHE_DIR = EVAL_DIR / "cache"
DATASETS = EVAL_DIR / "datasets"
RUBRICS = EVAL_DIR / "rubrics"


def load_json(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p, data):
    from agent.runtime.paths import atomic_write_json
    atomic_write_json(Path(p), data)


def sha(s: str, n: int = 16) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def render(contract: dict, show_locator: bool = False) -> str:
    """结构化合同 → 文本（与系统内部渲染一致）"""
    from agent.utils.outline_manager import OutlineManager
    data = json.loads(json.dumps(contract, ensure_ascii=False))
    om = OutlineManager(data=data)
    if not show_locator:
        return om._format_outline(show_locator=False)
    lines = []
    tpl = data["标准化模板文本"]
    lines.append("【合同首部】\n" + str(tpl.get("合同首部", "")))
    for s in tpl.get("正文章节", []):
        lines.append(f"[{s.get('locator')}] {s.get('章节编号', '')} {s.get('章节标题', '')}")
        for c in s.get("条款列表", []) or []:
            lines.append(f"  [{c.get('locator')}] {c.get('条款大致说明', '')}")
            for sc in c.get("子条款列表", []) or []:
                lines.append(f"    [{sc.get('locator')}] {sc.get('子条款大致说明', '')}")
                for ssc in sc.get("子子条款列表", []) or []:
                    lines.append(f"      [{ssc.get('locator')}] {ssc.get('子子条款大致说明', '')}")
    lines.append("【合同尾部】\n" + str(tpl.get("合同尾部", "")))
    return "\n".join(lines)


_PUNCT = re.compile(r"[\s\u3000，,。．.；;：:、“”\"'‘’（）()【】\[\]《》<>!！?？…—\-·]")


def normalize(text: str) -> str:
    return _PUNCT.sub("", str(text or "")).lower()


def grounded(evidence: str, source_norm: str, min_len: int = 4) -> bool:
    """证据核验：引用（归一化后）必须是原文子串；过长引用允许取首尾片段核验（模型常截断/省略中间）"""
    ev = normalize(evidence)
    if len(ev) < min_len:
        return False
    if ev in source_norm:
        return True
    if "…" in str(evidence) or "..." in str(evidence):
        parts = [normalize(x) for x in re.split(r"…+|\.\.\.+", str(evidence)) if len(normalize(x)) >= min_len]
        return bool(parts) and all(p in source_norm for p in parts)
    if len(ev) >= 24:
        return ev[:12] in source_norm and ev[-12:] in source_norm
    return False
