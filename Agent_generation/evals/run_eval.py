"""三层评测主程序

    python -m evals.run_eval                 # 评测 evals/artifacts 下所有已生成用例 → evals/results/eval_results.json
    python -m evals.run_eval --samples 3     # Judge 采样次数（默认 3）
    python -m evals.report                   # 由结果生成 report.html

Layer 1 端到端：规则硬指标（要素准确率 / 必备条款召回 / 结构合法）+ 6 维 Rubric（系统 vs 单 LLM）+ 成对盲评（双向交换）
Layer 2 大纲：3 维 Rubric（系统大纲 vs 单 LLM 大纲）+ 多轮 WebSearch（噪声率 / 利用率 / 收敛轮数）
Layer 3 补全：Drafter / Leader 工具调用轨迹指标（非法率 / 批量利用率 / 绕路率 / 自纠正 / 编辑落地率）+ 起草前后终态对比
所有 Judge 调用缓存在 evals/cache/*.jsonl，重复运行结果完全一致（可复现）；结果中保留缓存键用于审计追溯。
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from .common import ARTIFACTS, DATASETS, EVAL_DIR, RUBRICS, load_json, render, save_json, sha
from .judge import CheckItem, Judge, items_to_json, score_items
from .retrieval_metrics import retrieval_metrics
from .rules import clause_recall, element_accuracy, placeholder_leak, structure_valid
from .stats import bootstrap_ci, fleiss_kappa_binary, mean, unanimity
from .tool_metrics import edit_landing, leader_plan_metrics, trajectory_metrics

RESULTS = EVAL_DIR / "results"


def load_rubric(name: str):
    path = RUBRICS / name
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text), sha(text, 12)


def build_items(rubric: dict, specials: list, special_dim: str, special_prefix: str, special_weight: float,
                special_tmpl: str):
    items = []
    for did, d in rubric["dimensions"].items():
        for it in d["items"]:
            items.append(CheckItem(it["id"], did, it["text"], float(it.get("weight", 1))))
    for i, s in enumerate(specials, 1):
        items.append(CheckItem(f"{special_prefix}{i}", special_dim, special_tmpl.format(s=s), special_weight))
    return items


def weighted_total(rubric: dict, dims: dict) -> float:
    num = den = 0.0
    for did, d in rubric["dimensions"].items():
        sc = dims.get(did, {}).get("score")
        if sc is None or sc != sc:
            continue
        num += d["weight"] * sc
        den += d["weight"]
    return num / den if den else float("nan")


def eval_document(judge: Judge, tag: str, doc: dict, need: str, rubric: dict, items, kind: str):
    text = render(doc, show_locator=True)
    ctx = (f"【评测对象】{kind}\n【用户需求】\n{need}\n\n说明：被评文本中方括号为 locator。" +
           ("大纲阶段只判断“是否涉及该方面”，不评判措辞细节。" if kind == "合同大纲" else ""))
    res = judge.checklist(tag, text, items, context=ctx)
    dims = score_items(res["items"])
    return {"dims": {k: {kk: v[kk] for kk in ("score", "n", "pass", "ungrounded")} for k, v in dims.items()},
            "total": weighted_total(rubric, dims), "items": items_to_json(res["items"]), "audits": res["audits"],
            "chars": len(text)}


def rule_metrics(doc: dict, case: dict):
    return {"element": element_accuracy(doc, case["elements"]), "clause": clause_recall(doc, case["required_clauses"]),
            "placeholder": placeholder_leak(doc), "structure": structure_valid(doc)}


def eval_case(judge: Judge, case: dict, e2e_rubric, outline_rubric) -> dict:
    d = ARTIFACTS / case["id"]
    sysr = load_json(d / "system.json")
    base_c = load_json(d / "baseline_contract.json")
    base_o = load_json(d / "baseline_outline.json")
    adv = load_json(d / "adversarial.json")
    out = {"id": case["id"], "title": case["title"], "family": case["contract_family"], "available": {
        "system": bool(sysr and sysr.get("contract")), "baseline_contract": bool(base_c),
        "baseline_outline": bool(base_o), "adversarial": bool(adv)}}
    need = case["requirement"]
    e2e_items = build_items(e2e_rubric, case["special_requirements"], "D1", "D1.S", 2.0,
                            "合同中明确约定了：{s}（数值与条件须与需求一致）")
    out_items = build_items(outline_rubric, case["special_requirements"], "O1", "O1.S", 1.5,
                            "大纲中有对应条款覆盖用户需求：{s}")
    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        if out["available"]["system"]:
            jobs["sys_e2e"] = pool.submit(eval_document, judge, f"{case['id']}:e2e:system", sysr["contract"], need,
                                          e2e_rubric, e2e_items, "完整合同")
            jobs["sys_outline"] = pool.submit(eval_document, judge, f"{case['id']}:outline:system", sysr["outline"],
                                              need, outline_rubric, out_items, "合同大纲")
        if base_c:
            jobs["base_e2e"] = pool.submit(eval_document, judge, f"{case['id']}:e2e:baseline", base_c["contract"],
                                           need, e2e_rubric, e2e_items, "完整合同")
        if base_o:
            jobs["base_outline"] = pool.submit(eval_document, judge, f"{case['id']}:outline:baseline",
                                               base_o["contract"], need, outline_rubric, out_items, "合同大纲")
        if adv:
            jobs["adv_outline"] = pool.submit(eval_document, judge, f"{case['id']}:outline:adversarial",
                                              adv["outline"], need, outline_rubric, out_items, "合同大纲")
            if adv.get("trace", {}).get("initial_outline"):
                jobs["adv_initial"] = pool.submit(eval_document, judge, f"{case['id']}:outline:adv_initial",
                                                  adv["trace"]["initial_outline"], need, outline_rubric, out_items,
                                                  "合同大纲")
        if out["available"]["system"] and base_c:
            jobs["pairwise"] = pool.submit(judge.pairwise, f"{case['id']}:pair", need, render(sysr["contract"]),
                                           render(base_c["contract"]), e2e_rubric["pairwise_dims"])
        res = {k: f.result() for k, f in jobs.items()}

    # ---------------- Layer 1 端到端
    l1 = {}
    if out["available"]["system"]:
        l1["system"] = {"rules": rule_metrics(sysr["contract"], case), "rubric": res["sys_e2e"],
                        "duration_s": sysr.get("duration_s"), "degraded": sysr.get("degraded"),
                        "tokens": sum(v.get("total_tokens", 0) for v in sysr["metrics"]["by_model"].values()),
                        "llm_calls": sum(v.get("calls", 0) for v in sysr["metrics"]["by_model"].values()),
                        "outline_strategy": sysr.get("outline_strategy"), "match_count": sysr.get("match_count")}
    if base_c:
        l1["baseline"] = {"rules": rule_metrics(base_c["contract"], case), "rubric": res["base_e2e"],
                          "duration_s": base_c.get("duration_s"),
                          "tokens": sum(v.get("total_tokens", 0) for v in base_c["metrics"]["by_model"].values())}
    if "pairwise" in res:
        l1["pairwise"] = res["pairwise"]
    out["e2e"] = l1

    # ---------------- Layer 2 大纲
    l2 = {}
    for k, key in (("system", "sys_outline"), ("baseline", "base_outline"), ("adversarial", "adv_outline"),
                   ("adversarial_initial", "adv_initial")):
        if key in res:
            l2[k] = res[key]
    if adv:
        ctype = (sysr or {}).get("user_need", {}).get("collected", {}).get("contract_type", case["contract_family"])
        l2["websearch"] = retrieval_metrics(judge, case["id"], ctype, adv.get("retrievals", []),
                                            adv.get("trace", {}).get("initial_outline"), adv.get("trace", {}))
        l2["adversarial_meta"] = {"converged": adv.get("converged"), "duration_s": adv.get("duration_s"),
                                  "rounds": adv.get("trace", {}).get("rounds", [])}
    out["outline"] = l2

    # ---------------- Layer 3 补全（工具调用）
    l3 = {}
    if out["available"]["system"]:
        drafter_recs = [r for t in (sysr.get("drafter_traces") or {}).values() for r in t.get("tool_records", [])]
        leader_recs = (sysr.get("leader_report") or {}).get("tool_records", []) or []
        l3["drafter"] = trajectory_metrics(drafter_recs)
        l3["drafter"]["landing"] = edit_landing(drafter_recs, sysr["contract"])
        l3["drafter"]["stop_reasons"] = {k: v.get("stop_reason") for k, v in (sysr.get("drafter_traces") or {}).items()}
        l3["leader"] = trajectory_metrics(leader_recs)
        l3["leader"]["landing"] = edit_landing(leader_recs, sysr["contract"])
        l3["leader"]["plan"] = leader_plan_metrics(sysr.get("leader_report"))
        if sysr.get("draft_contract"):
            l3["final_state"] = {
                "outline": rule_metrics(sysr["outline"], case)["element"]["score"],
                "after_draft": rule_metrics(sysr["draft_contract"], case)["element"]["score"],
                "after_revision": rule_metrics(sysr["contract"], case)["element"]["score"],
            }
        if adv:
            l3["adversarial"] = trajectory_metrics(adv.get("trace", {}).get("tool_records", []) or [])
    out["completion"] = l3
    return out


def aggregate(cases: list, judge: Judge) -> dict:
    def collect(path):
        vals = []
        for c in cases:
            cur = c
            for p in path:
                cur = cur.get(p) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, (int, float)) and cur == cur:
                vals.append(float(cur))
        return vals

    def stat(path):
        v = collect(path)
        lo, hi = bootstrap_ci(v) if len(v) >= 2 else (float("nan"), float("nan"))
        return {"mean": mean(v), "n": len(v), "ci95": [lo, hi], "values": v}

    agg = {
        "e2e": {
            "system_element_acc": stat(["e2e", "system", "rules", "element", "score"]),
            "baseline_element_acc": stat(["e2e", "baseline", "rules", "element", "score"]),
            "system_clause_recall": stat(["e2e", "system", "rules", "clause", "score"]),
            "baseline_clause_recall": stat(["e2e", "baseline", "rules", "clause", "score"]),
            "system_rubric": stat(["e2e", "system", "rubric", "total"]),
            "baseline_rubric": stat(["e2e", "baseline", "rubric", "total"]),
        },
        "outline": {
            "system": stat(["outline", "system", "total"]),
            "baseline": stat(["outline", "baseline", "total"]),
            "adversarial": stat(["outline", "adversarial", "total"]),
            "adversarial_initial": stat(["outline", "adversarial_initial", "total"]),
            "noise_rate": stat(["outline", "websearch", "noise_rate"]),
            "utilization": stat(["outline", "websearch", "utilization"]),
            "rounds": stat(["outline", "websearch", "rounds_to_converge"]),
        },
        "completion": {k: stat(["completion", "drafter", k]) for k in
                       ("invalid_rate", "error_rate", "batch_utilization", "avg_batch", "detour_rate",
                        "repeat_failure_rate", "self_correction_rate", "success_rate")},
    }
    agg["completion"]["landing_rate"] = stat(["completion", "drafter", "landing", "landing_rate"])
    agg["completion"]["leader_plan_execution"] = stat(["completion", "leader", "plan", "plan_execution_rate"])
    agg["completion"]["leader_invalid_rate"] = stat(["completion", "leader", "invalid_rate"])
    for k in ("outline", "after_draft", "after_revision"):
        agg["completion"][f"element_{k}"] = stat(["completion", "final_state", k])
    for dim in ("D1", "D2", "D3", "D4", "D5", "D6"):
        agg["e2e"][f"system_{dim}"] = stat(["e2e", "system", "rubric", "dims", dim, "score"])
        agg["e2e"][f"baseline_{dim}"] = stat(["e2e", "baseline", "rubric", "dims", dim, "score"])
    for dim in ("O1", "O2", "O3"):
        for who in ("system", "baseline", "adversarial", "adversarial_initial"):
            agg["outline"][f"{who}_{dim}"] = stat(["outline", who, "dims", dim, "score"])

    def lift(a, b):
        return (a / b - 1) if (a == a and b == b and b) else float("nan")
    e = agg["e2e"]
    agg["headline"] = {
        "element_accuracy": e["system_element_acc"]["mean"],
        "clause_recall": e["system_clause_recall"]["mean"],
        "rubric_system": e["system_rubric"]["mean"], "rubric_baseline": e["baseline_rubric"]["mean"],
        "rubric_lift": lift(e["system_rubric"]["mean"], e["baseline_rubric"]["mean"]),
        "outline_lift": lift(agg["outline"]["system"]["mean"], agg["outline"]["baseline"]["mean"]),
        "adversarial_lift_vs_initial": lift(agg["outline"]["adversarial"]["mean"],
                                            agg["outline"]["adversarial_initial"]["mean"]),
    }
    # 成对盲评胜率（双向一致才计）
    wins = {"A": 0, "B": 0, "TIE": 0, "inconsistent": 0}
    per_dim = {}
    for c in cases:
        pw = c.get("e2e", {}).get("pairwise")
        if not pw:
            continue
        for did, r in pw["dims"].items():
            dd = per_dim.setdefault(did, {"A": 0, "B": 0, "TIE": 0, "inconsistent": 0})
            dd[r["final"]] += 1
            if not r["consistent"]:
                dd["inconsistent"] += 1
        ov = pw["dims"]["__overall__"]
        wins[ov["final"]] += 1
        if not ov["consistent"]:
            wins["inconsistent"] += 1
    agg["pairwise"] = {"overall": wins, "per_dim": per_dim}
    # Judge 可靠性：逐检查项 k 次投票的 Fleiss κ 与一致率；证据核验失败率
    votes, ungrounded, yes_total = [], 0, 0
    for c in cases:
        for layer, keys in (("e2e", ("system", "baseline")), ("outline", ("system", "baseline", "adversarial"))):
            for k in keys:
                node = c.get(layer, {}).get(k, {})
                rub = node.get("rubric", node) if layer == "e2e" else node
                for it in (rub or {}).get("items", []) or []:
                    if len(it["votes"]) == judge.samples:
                        votes.append(it["votes"])
                    ungrounded += it["ungrounded"]
                    yes_total += sum(1 for r in it["raw"] if r.get("verdict") == "yes")
    rel_votes = [v for c in cases for v in c.get("outline", {}).get("websearch", {}).get("relevance_votes", [])
                 if len(v) == judge.samples]
    agg["judge_reliability"] = {
        "items": len(votes), "fleiss_kappa": fleiss_kappa_binary(votes), "unanimity": unanimity(votes),
        "ungrounded_yes": ungrounded, "yes_total": yes_total,
        "ungrounded_rate": (ungrounded / yes_total) if yes_total else float("nan"),
        "relevance_kappa": fleiss_kappa_binary(rel_votes) if rel_votes else float("nan"),
    }
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--cases", default="")
    ap.add_argument("--parallel", action="store_true", help="每个用例一个子进程并行评测（各自独立缓存文件），完成后汇总")
    ap.add_argument("--only-case", default="", help="内部使用：仅评测单个用例并写出 partial 结果")
    ap.add_argument("--aggregate-only", action="store_true", help="仅汇总 results/partial/*.json")
    a = ap.parse_args()
    if a.parallel:
        import subprocess, sys, os
        ids = [c["id"] for c in load_json(DATASETS / "e2e_cases.json") if (ARTIFACTS / c["id"]).exists()]
        procs = [subprocess.Popen([sys.executable, "-m", "evals.run_eval", "--samples", str(a.samples), "--model", a.model,
                                   "--only-case", cid], cwd=str(EVAL_DIR.parent), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                 for cid in ids]
        codes = [p.wait() for p in procs]
        print("sub-evals exit codes:", dict(zip(ids, codes)))
        a.aggregate_only = True
    e2e_rubric, e2e_hash = load_rubric("e2e_rubric.yaml")
    outline_rubric, outline_hash = load_rubric("outline_rubric.yaml")
    judge = Judge(model=a.model, samples=a.samples, rubric_version=f"{e2e_rubric['version']}+{outline_rubric['version']}",
                  cache_name=f"judge_{a.only_case}" if a.only_case else "judge")
    partial_dir = RESULTS / "partial"
    if a.only_case:
        case = next(c for c in load_json(DATASETS / "e2e_cases.json") if c["id"] == a.only_case)
        t0 = time.time()
        res = eval_case(judge, case, e2e_rubric, outline_rubric)
        if judge.stats.get("provider_errors"):
            # 供应商错误（如余额不足）导致部分判定缺失 → 结果无效，不写出，避免污染报告
            print(a.only_case, "INVALID: provider errors", judge.stats)
            raise SystemExit(2)
        save_json(partial_dir / f"{a.only_case}.json", {"case": res, "judge_stats": judge.stats,
                                                       "duration_s": round(time.time() - t0, 1)})
        print(a.only_case, "done", judge.stats)
        return
    if a.aggregate_only:
        parts = [load_json(p) for p in sorted(partial_dir.glob("*.json"))]
        order = [c["id"] for c in load_json(DATASETS / "e2e_cases.json")]
        results = sorted([p["case"] for p in parts], key=lambda c: order.index(c["id"]))
        for k in judge.stats:
            judge.stats[k] = sum(p["judge_stats"].get(k, 0) for p in parts)
        t0 = time.time() - sum(p["duration_s"] for p in parts) / max(1, len(parts))
        agg = aggregate(results, judge)
        _write(a, results, agg, judge, e2e_rubric, outline_rubric, e2e_hash, outline_hash, t0)
        return
    all_cases = load_json(DATASETS / "e2e_cases.json")
    want = set(a.cases.split(",")) if a.cases else None
    t0 = time.time()
    results = []
    for case in all_cases:
        if want and case["id"] not in want:
            continue
        if not (ARTIFACTS / case["id"]).exists():
            continue
        print(f"[eval] {case['id']} ...", flush=True)
        results.append(eval_case(judge, case, e2e_rubric, outline_rubric))
    agg = aggregate(results, judge)
    _write(a, results, agg, judge, e2e_rubric, outline_rubric, e2e_hash, outline_hash, t0)


def _write(a, results, agg, judge, e2e_rubric, outline_rubric, e2e_hash, outline_hash, t0):
    payload = {
        "meta": {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "duration_s": round(time.time() - t0, 1),
                 "judge_model": a.model, "judge_temperature": 0.0, "samples": a.samples,
                 "rubrics": {"e2e": {"version": e2e_rubric["version"], "hash": e2e_hash},
                             "outline": {"version": outline_rubric["version"], "hash": outline_hash}},
                 "baseline": "deepseek-flash 单次生成（同模型、同 Schema、同需求，无模板/检索/审查）",
                 "system_models": "全部 Agent 均为 deepseek-flash",
                 "judge_stats": judge.stats},
        "rubrics": {"e2e": e2e_rubric, "outline": outline_rubric},
        "aggregate": agg, "cases": results,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    save_json(RESULTS / "eval_results.json", payload)
    print(json.dumps(agg["headline"], ensure_ascii=False, indent=2))
    print("judge:", judge.stats, "κ=", agg["judge_reliability"]["fleiss_kappa"])


if __name__ == "__main__":
    main()
