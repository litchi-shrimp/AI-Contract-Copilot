"""工具调用 Harness —— Agent 只能通过这里操作合同

职责：
1. 白名单：只允许执行当前 Agent 注册（JSON 声明）过的工具，拒绝任意代码/未知工具
2. 参数校验：按工具 JSON Schema 校验 + 规范化（单值→列表、长度一致性、locator 格式）
3. 前置校验：locator 必须存在（update/delete/insert-before/after），content 不能为空/超长
4. 事务：执行前快照，工具异常或产生非法结构时回滚，保证合同结构始终合法（原子性）
5. 超时：单次工具执行时长上限（防止工具卡死）
6. 审计：每次调用记录 tool/params/状态/错误码/耗时/影响的 locator/前后结构摘要，供评测与回放
7. 事件：tool.call / tool.result 推送到前端（工具调用过程可视化），并推送 contract.diff
"""
import copy
import json
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .context import current
from .errors import RunCancelled

LOCATOR_RE = re.compile(r"^\d+(\.\d+){0,3}$")
MAX_CONTENT_CHARS = 4000
EDIT_TOOLS = {"update_clause", "insert_clause", "delete_clause", "update_header_footer"}
READ_TOOLS = {"get_contract", "retrieve_template_reference", "web_search", "list_backups"}


@dataclass
class ToolCallRecord:
    seq: int
    agent: str
    tool: str
    params: Dict[str, Any]
    status: str                       # ok | rejected | error | partial | timeout
    error_code: Optional[str] = None  # unknown_tool | schema | locator_not_found | empty_content | ...
    message: str = ""
    duration_ms: int = 0
    batch_size: int = 1
    ok_items: int = 0
    locators: List[str] = field(default_factory=list)
    before_hash: str = ""
    after_hash: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["params"] = _truncate(d["params"])
        return d


def _truncate(obj, limit: int = 300):
    if isinstance(obj, str):
        return obj if len(obj) <= limit else obj[:limit] + "…"
    if isinstance(obj, list):
        return [_truncate(x, limit) for x in obj[:50]]
    if isinstance(obj, dict):
        return {k: _truncate(v, limit) for k, v in obj.items()}
    return obj


def outline_hash(outline: dict) -> str:
    import hashlib
    try:
        s = json.dumps(outline, ensure_ascii=False, sort_keys=True)
    except Exception:
        s = str(outline)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class ToolHarness:
    """包装 ToolManager，提供校验/事务/审计"""

    def __init__(self, tool_manager, outline_manager, agent_name: str = "unknown", timeout_s: float = 30.0):
        self.tm = tool_manager
        self.om = outline_manager
        self.agent_name = agent_name
        self.timeout_s = timeout_s
        self.records: List[ToolCallRecord] = []
        self._seq = 0
        # 可选：合同变更回调（如 chunk 并行起草时由编排层合并为完整合同再推送）；为 None 时直接推送当前文档
        self.on_change = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 规范化 + 校验
    def _normalize(self, tool: str, params: dict) -> dict:
        p = dict(params or {})
        if tool == "update_clause":
            if "locators" in p and "locator" not in p:
                p["locator"] = p.pop("locators")
            if "contents" in p and "content" not in p:
                p["content"] = p.pop("contents")
            p["locator"] = [str(x).strip() for x in _as_list(p.get("locator"))]
            p["content"] = [str(x) for x in _as_list(p.get("content"))]
        elif tool == "delete_clause":
            if "locators" in p and "locator" not in p:
                p["locator"] = p.pop("locators")
            p["locator"] = [str(x).strip() for x in _as_list(p.get("locator"))]
        elif tool == "insert_clause":
            if "locators" not in p:
                p["locators"] = _as_list(p.pop("locator", None))
                p["contents"] = _as_list(p.pop("content", None))
                p["positions"] = _as_list(p.pop("position", None))
            p["locators"] = [str(x).strip() for x in _as_list(p.get("locators"))]
            p["contents"] = [str(x) for x in _as_list(p.get("contents"))]
            pos = _as_list(p.get("positions"))
            if not pos:
                pos = ["after"] * len(p["locators"])
            elif len(pos) == 1 and len(p["locators"]) > 1:
                pos = pos * len(p["locators"])
            p["positions"] = [str(x).strip() for x in pos]
        return p

    def _validate(self, tool: str, p: dict) -> Optional[tuple]:
        """返回 (error_code, message) 或 None"""
        if tool in ("update_clause", "delete_clause"):
            locs = p["locator"]
            if not locs:
                return "schema", "locator 不能为空"
            if tool == "update_clause":
                if len(locs) != len(p["content"]):
                    return "length_mismatch", f"locator({len(locs)}) 与 content({len(p['content'])}) 数量不一致"
                for c in p["content"]:
                    if not c.strip():
                        return "empty_content", "content 不能为空字符串"
                    if len(c) > MAX_CONTENT_CHARS:
                        return "content_too_long", f"单条 content 超过 {MAX_CONTENT_CHARS} 字"
            if len(set(locs)) != len(locs):
                return "duplicate_locator", "同一批次 locator 重复"
            for loc in locs:
                if not LOCATOR_RE.match(loc):
                    return "bad_locator", f"locator 格式非法：{loc}"
                if self.om.find_node(loc) is None:
                    return "locator_not_found", f"locator 不存在：{loc}"
        elif tool == "insert_clause":
            locs, contents, pos = p["locators"], p["contents"], p["positions"]
            if not locs:
                return "schema", "locators 不能为空"
            if not (len(locs) == len(contents) == len(pos)):
                return "length_mismatch", "locators / contents / positions 数量不一致"
            for loc, c, ps in zip(locs, contents, pos):
                if ps not in ("before", "after", "child"):
                    return "bad_position", f"position 非法：{ps}"
                if not LOCATOR_RE.match(loc):
                    return "bad_locator", f"locator 格式非法：{loc}"
                if not c.strip():
                    return "empty_content", "content 不能为空字符串"
                if len(c) > MAX_CONTENT_CHARS:
                    return "content_too_long", f"单条 content 超过 {MAX_CONTENT_CHARS} 字"
                if ps == "child" and len(loc.split(".")) >= 4:
                    return "depth_exceeded", f"{loc} 已是最深层级，不能再插入子节点"
                exists = self.om.find_node(loc) is not None
                if not exists and not (ps == "after" and "." not in loc):
                    return "locator_not_found", f"参考 locator 不存在：{loc}"
        elif tool == "update_header_footer":
            if p.get("target") not in ("header", "footer"):
                return "schema", "target 必须为 header 或 footer"
            op = p.get("operation", "update")
            if op == "update" and not str(p.get("content", "")).strip():
                return "empty_content", "content 不能为空"
        return None

    # ------------------------------------------------------------ 结构不变量
    def _structure_ok(self) -> Optional[str]:
        try:
            body = self.om.get_outline()["标准化模板文本"]["正文章节"]
        except Exception:
            return "合同缺少 标准化模板文本/正文章节"
        if not isinstance(body, list):
            return "正文章节不是列表"
        return None

    # ------------------------------------------------------------ 执行
    def execute(self, tool: str, params: dict) -> str:
        ctx = current()
        if ctx is not None:
            ctx.cancel.raise_if_cancelled()
            if ctx.budget is not None:
                ctx.budget.charge_tool_call()
        with self._lock:
            self._seq += 1
            seq = self._seq
        t0 = time.monotonic()
        rec = ToolCallRecord(seq=seq, agent=self.agent_name, tool=tool or "", params=dict(params or {}),
                             status="ok")
        if ctx is not None:
            ctx.emit("tool.call", {"tool_name": tool, "params": _truncate(params or {}), "seq": seq},
                     agent=self.agent_name)
        result = self._execute_inner(tool, params, rec)
        rec.duration_ms = int((time.monotonic() - t0) * 1000)
        rec.message = str(result)[:500]
        self.records.append(rec)
        if ctx is not None:
            ctx.emit("tool.result", {"tool_name": tool, "seq": seq, "success": rec.status == "ok",
                                     "status": rec.status, "error_code": rec.error_code,
                                     "summary": str(result)[:300], "duration_ms": rec.duration_ms},
                     agent=self.agent_name)
            if tool in EDIT_TOOLS and rec.status in ("ok", "partial") and rec.before_hash != rec.after_hash:
                if self.on_change is not None:
                    try:
                        self.on_change(tool, rec.locators)
                    except Exception:
                        pass
                else:
                    ctx.emit("contract.update", {"tool_name": tool, "locators": rec.locators,
                                                 "outline": self.om.get_outline()}, agent=self.agent_name)
        return result

    def _execute_inner(self, tool: str, params: dict, rec: ToolCallRecord) -> str:
        if not tool or tool not in getattr(self.tm, "tools", {}):
            rec.status, rec.error_code = "rejected", "unknown_tool"
            allowed = ", ".join(getattr(self.tm, "tools", {}).keys())
            return f"错误：工具 {tool} 未注册或无权限。可用工具：{allowed}"
        if not isinstance(params, dict):
            rec.status, rec.error_code = "rejected", "schema"
            return "错误：tool_params 必须是 JSON 对象"
        try:
            p = self._normalize(tool, params)
        except Exception as e:  # noqa: BLE001
            rec.status, rec.error_code = "rejected", "schema"
            return f"错误：参数无法解析：{e}"
        rec.params = p
        locs = p.get("locator") or p.get("locators") or []
        rec.locators = list(locs) if isinstance(locs, list) else [str(locs)]
        rec.batch_size = max(1, len(rec.locators))
        err = self._validate(tool, p) if tool in EDIT_TOOLS else None
        if err:
            rec.status, rec.error_code = "rejected", err[0]
            return f"错误（{err[0]}）：{err[1]}。请先调用工具前确认 locator 与参数，或基于最新合同结构重试。"

        snapshot = copy.deepcopy(self.om.get_outline()) if tool in EDIT_TOOLS else None
        rec.before_hash = outline_hash(snapshot) if snapshot is not None else ""
        box: Dict[str, Any] = {}

        def _run():
            try:
                box["r"] = self.tm.execute_tool(tool, p)
            except BaseException as e:  # noqa: BLE001
                box["e"] = e

        if tool in EDIT_TOOLS:
            _run()
        else:
            th = threading.Thread(target=_run, daemon=True)
            th.start()
            deadline = time.monotonic() + self.timeout_s
            while th.is_alive():
                th.join(0.2)
                ctx = current()
                if ctx is not None and ctx.cancel.cancelled:
                    raise RunCancelled(ctx.cancel.reason or "cancelled")
                if time.monotonic() > deadline:
                    rec.status, rec.error_code = "timeout", "timeout"
                    return f"错误：工具 {tool} 执行超时（>{self.timeout_s}s），请换一种方式或稍后再试"
        if "e" in box:
            if isinstance(box["e"], RunCancelled):
                raise box["e"]
            self._rollback(snapshot)
            rec.status, rec.error_code = "error", "exception"
            return f"执行失败：{box['e']}"
        result = str(box.get("r", ""))
        if tool in EDIT_TOOLS:
            bad = self._structure_ok()
            if bad:
                self._rollback(snapshot)
                rec.status, rec.error_code = "error", "structure_broken"
                return f"错误：操作破坏了合同结构（{bad}），已自动回滚"
            # 按行“前缀”判定（工具对每个子操作输出一行），避免条款正文里的“失败/无法”等字样被误判
            fail_prefix = ("错误", "执行失败", "未找到", "无法", "插入节点失败", "插入条款内容时出错",
                           "更新条款内容时出错", "locator", "未提供", "操作合同首部")
            lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
            n_ok = sum(1 for ln in lines if ln.startswith("成功"))
            n_fail = sum(1 for ln in lines if ln.startswith(fail_prefix))
            rec.ok_items = n_ok
            if n_fail and n_ok:
                rec.status, rec.error_code = "partial", "partial_failure"
            elif n_fail or (lines and not n_ok and tool != "update_header_footer"):
                self._rollback(snapshot)
                rec.status, rec.error_code = "error", "tool_reported_failure"
            rec.after_hash = outline_hash(self.om.get_outline())
            if rec.status == "ok" and rec.after_hash == rec.before_hash and tool != "update_header_footer":
                rec.error_code = "noop"
        else:
            if result.startswith("错误") or result.startswith("执行失败"):
                rec.status, rec.error_code = "error", "tool_reported_failure"
        return result

    def _rollback(self, snapshot):
        if snapshot is None:
            return
        outline = self.om.get_outline()
        outline.clear()
        outline.update(snapshot)
        if hasattr(self.om, "_build_locator_index"):
            try:
                self.om._build_locator_index(outline)
            except Exception:
                pass

    def summary(self) -> dict:
        total = len(self.records)
        by_status: Dict[str, int] = {}
        for r in self.records:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        return {"total": total, "by_status": by_status}

    def export(self) -> List[dict]:
        return [r.to_dict() for r in self.records]
