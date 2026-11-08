"""评测数据生成（每个用例一个进程，失败隔离；产物落盘，可断点续跑）

    python -m evals.generate --case lease_residential --what system,baseline,adversarial
    python -m evals.generate --all            # 依次启动所有用例（后台并行进程）

产物：evals/artifacts/<case>/{system.json, baseline_contract.json, baseline_outline.json, adversarial.json}
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from .common import ARTIFACTS, DATASETS, load_json, save_json

KEEP_EVENTS = {"retrieval.web", "tool.result", "step.degraded", "llm.fallback", "llm.retry", "step.complete"}


def _ctx(case_dir: Path, name: str, events: list):
    from agent.runtime import new_context

    def sink(t, step=None, agent=None, data=None):
        if t in KEEP_EVENTS:
            events.append({"type": t, "step": step, "agent": agent, "data": data, "ts": time.time()})
    return new_context(run_id=f"{case_dir.name}-{name}", workspace=case_dir / f"ws_{name}", sink=sink)


def gen_system(case: dict, case_dir: Path):
    from agent.pipeline import ContractPipeline, PipelineState
    from agent.runtime import use_context
    out = case_dir / "system.json"
    ckpt = case_dir / "system_checkpoint.json"
    if out.exists():
        return
    events = []
    ctx = _ctx(case_dir, "system", events)
    state = PipelineState.from_dict(load_json(ckpt)) if ckpt.exists() else PipelineState(case["id"])
    p = ContractPipeline(state, checkpoint=lambda s: save_json(ckpt, s.to_dict()),
                         initial_requirement=case["requirement"], auto_confirm=True)
    t0 = time.time()
    with use_context(ctx):
        p.advance()
    st = p.state
    save_json(out, {"case": case["id"], "duration_s": round(time.time() - t0, 1), "status": st.status,
                    "contract": st.artifacts.get("contract"), "outline": st.artifacts.get("outline"),
                    "draft_contract": st.artifacts.get("draft_contract"),
                    "outline_strategy": st.artifacts.get("outline_strategy"),
                    "user_need": st.artifacts.get("user_need"),
                    "match_count": len(st.artifacts.get("match_result") or []),
                    "adversarial_trace": st.artifacts.get("adversarial_trace"),
                    "drafter_traces": st.artifacts.get("drafter_traces"),
                    "leader_report": st.artifacts.get("leader_report"),
                    "reviews": st.artifacts.get("reviews"), "degraded": st.degraded,
                    "metrics": ctx.metrics.to_dict(), "events": events})


def gen_baseline(case: dict, case_dir: Path):
    from agent.runtime import use_context
    from .baseline import single_llm_contract, single_llm_outline
    for name, fn in (("baseline_contract", single_llm_contract), ("baseline_outline", single_llm_outline)):
        out = case_dir / f"{name}.json"
        if out.exists():
            continue
        events = []
        ctx = _ctx(case_dir, name, events)
        t0 = time.time()
        with use_context(ctx):
            doc = fn(case["requirement"])
        save_json(out, {"case": case["id"], "duration_s": round(time.time() - t0, 1), "contract": doc,
                        "metrics": ctx.metrics.to_dict()})


def gen_adversarial(case: dict, case_dir: Path):
    """强制走零模板对抗式大纲（WebSearch + ModelA/ModelB），评测检索与收敛"""
    from agent.agents.OutlineGenerationWOTem import AdversarialOutlineAgent
    from agent.runtime import agent_scope, use_context
    out = case_dir / "adversarial.json"
    if out.exists():
        return
    sysres = load_json(case_dir / "system.json") or {}
    need = sysres.get("user_need") or {"collected": {"contract_type": case["contract_family"]}, "extra_need": [],
                                       "summary": case["requirement"]}
    events = []
    ctx = _ctx(case_dir, "adversarial", events)
    t0 = time.time()
    with use_context(ctx):
        agent = AdversarialOutlineAgent(agent_name="AdversarialOutlineAgent", user_need=need)
        agent.patch_llm_call_for_events(step=3)
        with agent_scope(agent="AdversarialOutlineAgent", step=3, stream_event_map=agent.STREAM_EVENT_MAP):
            outline = agent.generate()
    save_json(out, {"case": case["id"], "duration_s": round(time.time() - t0, 1), "outline": outline,
                    "trace": agent.trace, "converged": agent.converged, "metrics": ctx.metrics.to_dict(),
                    "retrievals": [e["data"] for e in events if e["type"] == "retrieval.web"]})


def run_case(case_id: str, what: list):
    cases = {c["id"]: c for c in load_json(DATASETS / "e2e_cases.json")}
    case = cases[case_id]
    case_dir = ARTIFACTS / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    status = load_json(case_dir / "status.json", {}) or {}
    for w in what:
        fn = {"system": gen_system, "baseline": gen_baseline, "adversarial": gen_adversarial}[w]
        t0 = time.time()
        try:
            fn(case, case_dir)
            status[w] = {"ok": True, "s": round(time.time() - t0, 1)}
        except BaseException as e:  # noqa: BLE001
            status[w] = {"ok": False, "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]}
        save_json(case_dir / "status.json", status)
        print(case_id, w, status[w].get("ok"), status[w].get("error", ""), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--what", default="system,baseline,adversarial")
    a = ap.parse_args()
    what = a.what.split(",")
    if a.all:
        log_dir = ARTIFACTS / "_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        procs = []
        for c in load_json(DATASETS / "e2e_cases.json"):
            f = open(log_dir / f"{c['id']}.log", "w", encoding="utf-8")
            procs.append(subprocess.Popen([sys.executable, "-m", "evals.generate", "--case", c["id"], "--what", a.what],
                                          stdout=f, stderr=subprocess.STDOUT, cwd=str(Path(__file__).parent.parent),
                                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}))
        print("started", [p.pid for p in procs])
    else:
        run_case(a.case, what)
