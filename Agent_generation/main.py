#!/usr/bin/env python3
"""
主入口

    python main.py                     # 命令行交互式生成合同
    python main.py --need "需求描述"    # 一次给出需求，自动确认（无人值守）
    python main.py --resume <run_id>   # 从检查点继续（停止 / 崩溃后恢复）
    python main.py server              # 启动 Web 后端（FastAPI）

CLI 模式下 Ctrl+C = 停止：当前步骤立即取消（不写入半成品），可用 --resume 从检查点继续。
"""
import argparse
import json
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"


def _printer(event_type, step=None, agent=None, data=None):
    data = data or {}
    if event_type == "step.start":
        print(f"\n━━ Step {step} {data.get('step_name')} {'(恢复)' if data.get('resumed') else ''}", flush=True)
    elif event_type == "step.complete":
        print(f"   ✓ {data.get('step_name')} 完成 ({data.get('duration')}s)", flush=True)
    elif event_type == "step.degraded":
        print(f"   ⚠ 降级：{data.get('step_name')} → {data.get('strategy')}", flush=True)
    elif event_type == "tool.result":
        print(f"   [{agent}] {data.get('tool_name')} → {data.get('status')}", flush=True)
    elif event_type == "llm.fallback":
        print(f"   ⚠ 模型降级 {data.get('from')} → {data.get('to')}", flush=True)
    elif event_type == "system":
        print(f"   ℹ {data.get('message')}", flush=True)


def run_cli(need: str = None, resume: str = None):
    from agent.pipeline import ContractPipeline, PipelineFinished, PipelineState
    from agent.runtime import RunCancelled, new_context, use_context
    from agent.runtime.paths import atomic_write_json

    run_id = resume or uuid.uuid4().hex[:10]
    run_dir = RUNS_DIR / run_id
    ckpt = run_dir / "checkpoint.json"
    state = PipelineState(run_id)
    if resume:
        if not ckpt.exists():
            sys.exit(f"找不到检查点：{ckpt}")
        state = PipelineState.from_dict(json.loads(ckpt.read_text(encoding="utf-8")))
        print(f"从检查点恢复：Step {state.current_step}，已完成 {state.step_done}")
    ctx = new_context(run_id=run_id, workspace=run_dir / "workspace", sink=_printer)
    pipeline = ContractPipeline(state, checkpoint=lambda s: atomic_write_json(ckpt, s.to_dict()),
                                initial_requirement=need, auto_confirm=bool(need))

    def _on_sigint(*_):
        print("\n⏹ 正在停止…（当前步骤将被取消，可用 --resume 继续）", flush=True)
        ctx.cancel.cancel("user_stop")
    signal.signal(signal.SIGINT, _on_sigint)

    result_box = {}
    pending = [None]
    t0 = time.time()
    while True:
        def _work():
            with use_context(ctx):
                try:
                    result_box["r"] = pipeline.advance(pending[0])
                except RunCancelled as e:
                    result_box["cancelled"] = e.reason
                except BaseException as e:  # noqa: BLE001
                    result_box["error"] = e
        th = threading.Thread(target=_work, daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.2)
        if "cancelled" in result_box:
            print(f"已停止。继续：python main.py --resume {run_id}")
            return
        if "error" in result_box:
            print(f"❌ 流程异常：{result_box['error']}（检查点已保存，可 --resume {run_id} 重试）")
            return
        r = result_box.pop("r")
        if isinstance(r, PipelineFinished):
            out = run_dir / "final_contract.json"
            atomic_write_json(out, r.state.artifacts.get("contract"))
            print(f"\n✅ 完成，用时 {time.time() - t0:.0f}s，降级 {len(r.state.degraded)} 次，输出：{out}")
            return
        print(f"\n🤖 {r.prompt}")
        pending[0] = input("👤 ").strip()


def start_server():
    """平台后端（认证 / 会话 / 停止-继续 / SSE / 导出）。旧版 sse_server 仍保留供 test_frontend.html 使用"""
    import os
    import uvicorn
    uvicorn.run("server.app:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")),
                workers=1, log_level="info")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        start_server()
    else:
        ap = argparse.ArgumentParser(description="AI Contract Copilot CLI")
        ap.add_argument("--need", help="一次性给出完整需求（自动确认，无人值守）")
        ap.add_argument("--resume", help="从检查点恢复的 run_id")
        args = ap.parse_args()
        run_cli(args.need, args.resume)
