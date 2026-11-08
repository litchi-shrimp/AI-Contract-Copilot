"""补全阶段（Drafter / Leader）工具调用评测 —— 轨迹级指标（确定性计算，不依赖 LLM）

输入：ToolHarness 审计记录（每次调用的 tool / params / status / error_code / locators / 前后结构哈希）

指标定义（与业界 Agent 评测一致：τ-bench 的终态评估 + 轨迹效率评估）：
- 终态正确率 final_state_accuracy：对“应修改目标”（用例要素 + Leader 计划中的 locator）的终态核验，
  由 e2e 规则检查（要素是否在终稿出现）给出；这里提供“编辑落地率” = 被终稿保留的编辑 / 成功编辑
- 非法操作率 invalid_rate：被 Harness 拒绝（未注册工具 / 参数非法 / locator 不存在 / 越层）的调用占比
- 执行错误率 error_rate：通过校验但执行失败并回滚的调用占比
- 批量利用率 batch_utilization：编辑类调用中，单次调用操作 ≥2 个 locator 的比例（工具支持批量，鼓励合并）
- 平均批量大小 avg_batch
- 绕路率 detour_rate：无效调用占比 = (失败/被拒 + 无变化 noop + 对同一 locator 的重复覆写 + 重复相同调用) / 总调用
- 重复失败率 repeat_failure_rate：与上一次失败调用参数完全相同的再次调用（模型没有吸收 Observation）
- 自纠正率 self_correction_rate：一次失败后，下一次调用成功的比例（错误恢复能力）
"""
import json
from collections import Counter, defaultdict
from typing import Dict, List

EDIT = {"update_clause", "insert_clause", "delete_clause", "update_header_footer"}


def _sig(r: dict) -> str:
    return r.get("tool", "") + "|" + json.dumps(r.get("params", {}), ensure_ascii=False, sort_keys=True)


def trajectory_metrics(records: List[dict]) -> Dict:
    n = len(records)
    if n == 0:
        return {"calls": 0}
    invalid = sum(1 for r in records if r["status"] == "rejected")
    errors = sum(1 for r in records if r["status"] in ("error", "timeout"))
    ok = [r for r in records if r["status"] in ("ok", "partial")]
    edits = [r for r in records if r.get("tool") in EDIT]
    ok_edits = [r for r in edits if r["status"] in ("ok", "partial")]
    batch = [r for r in ok_edits if r.get("batch_size", 1) >= 2]
    noop = sum(1 for r in ok_edits if r.get("error_code") == "noop")
    # 对同一 locator 的重复覆写（update 同一位置两次以上，第二次起视为绕路）
    seen_upd = Counter()
    rewrite = 0
    for r in ok_edits:
        if r["tool"] == "update_clause":
            for loc in r.get("locators", []):
                seen_upd[loc] += 1
                if seen_upd[loc] > 1:
                    rewrite += 1
    dup_calls = 0
    sigs = Counter()
    for r in records:
        s = _sig(r)
        sigs[s] += 1
        if sigs[s] > 1:
            dup_calls += 1
    repeat_fail, after_fail, recovered = 0, 0, 0
    prev = None
    by_agent = defaultdict(list)
    for r in records:
        by_agent[r.get("agent", "")].append(r)
    for seq in by_agent.values():
        prev = None
        for r in seq:
            if prev is not None and prev["status"] in ("rejected", "error", "timeout"):
                after_fail += 1
                if _sig(r) == _sig(prev):
                    repeat_fail += 1
                if r["status"] in ("ok", "partial"):
                    recovered += 1
            prev = r
    detour = invalid + errors + noop + rewrite + dup_calls
    codes = Counter(r.get("error_code") for r in records if r["status"] not in ("ok",) and r.get("error_code"))
    return {
        "calls": n,
        "edit_calls": len(edits),
        "success_rate": len(ok) / n,
        "invalid_rate": invalid / n,
        "error_rate": errors / n,
        "batch_utilization": (len(batch) / len(ok_edits)) if ok_edits else float("nan"),
        "avg_batch": (sum(r.get("batch_size", 1) for r in ok_edits) / len(ok_edits)) if ok_edits else float("nan"),
        "detour_rate": min(1.0, detour / n),
        "detour_breakdown": {"invalid": invalid, "error": errors, "noop": noop, "rewrite_same_locator": rewrite,
                             "duplicate_call": dup_calls},
        "repeat_failure_rate": (repeat_fail / after_fail) if after_fail else 0.0,
        "self_correction_rate": (recovered / after_fail) if after_fail else float("nan"),
        "error_codes": dict(codes),
        "tools": dict(Counter(r.get("tool") for r in records)),
    }


def edit_landing(records: List[dict], final_contract: dict) -> Dict:
    """编辑落地率：成功的 update/insert 写入的内容，有多少在终稿中仍然存在（未被后续步骤覆盖/丢失）"""
    from .common import normalize, render
    final = normalize(render(final_contract))
    total, kept = 0, 0
    for r in records:
        if r.get("tool") not in ("update_clause", "insert_clause") or r["status"] not in ("ok", "partial"):
            continue
        p = r.get("params", {})
        contents = p.get("content") or p.get("contents") or []
        for c in contents if isinstance(contents, list) else [contents]:
            c = normalize(str(c))
            if len(c) < 8:
                continue
            total += 1
            probe = c[:30]
            if probe in final:
                kept += 1
    return {"edits": total, "kept": kept, "landing_rate": (kept / total) if total else float("nan")}


def leader_plan_metrics(report: dict) -> Dict:
    """Leader 计划执行：决策落地率、问题覆盖率"""
    if not report:
        return {}
    ex, fa = report.get("executed_actions", []) or [], report.get("failed_actions", []) or []
    deferred = report.get("deferred_items", []) or []
    n = len(ex) + len(fa)
    return {"decisions": n, "executed": len(ex), "failed": len(fa), "deferred": len(deferred),
            "plan_execution_rate": (len(ex) / n) if n else float("nan")}
