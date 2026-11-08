"""步骤级兜底执行器

run_with_fallback(primary, fallbacks=[...]) 依次尝试：主策略 → 重试 → 备选策略 → 最终兜底。
每次尝试有独立子取消令牌与超时；超时只取消该子任务，不影响整个会话（会话级取消仍然向下传播）。
所有降级都会留痕（metrics + 事件），前端可提示“已启用降级策略”。
"""
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from .context import agent_scope, bind, current
from .errors import BudgetExceeded, RunCancelled


@dataclass
class Strategy:
    name: str
    fn: Callable[[], Any]
    timeout: Optional[float] = None
    retries: int = 0
    accept: Optional[Callable[[Any], bool]] = None


class StepFailed(Exception):
    def __init__(self, step: str, errors: List[Tuple[str, str]]):
        super().__init__(f"{step} 全部策略失败：" + "; ".join(f"{n}: {e}" for n, e in errors))
        self.errors = errors


def run_with_timeout(fn: Callable[[], Any], timeout: Optional[float], label: str = "task") -> Any:
    """在子线程运行 fn，带超时与取消；超时时取消子令牌让 fn 内部尽快退出"""
    ctx = current()
    if timeout is None and ctx is None:
        return fn()
    import threading
    child = ctx.cancel.child() if ctx is not None else None
    box = {}

    def _target():
        try:
            if ctx is not None:
                with agent_scope(cancel=child):
                    box["r"] = fn()
            else:
                box["r"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    th = threading.Thread(target=bind(_target), daemon=True, name=f"run:{label}")
    th.start()
    deadline = time.monotonic() + timeout if timeout else None
    while th.is_alive():
        th.join(0.2)
        if ctx is not None and ctx.cancel.cancelled:
            th.join(5)
            raise RunCancelled(ctx.cancel.reason or "cancelled")
        if deadline is not None and time.monotonic() > deadline:
            if child is not None:
                child.cancel(f"{label} 超时")
            th.join(10)
            raise TimeoutError(f"{label} 超过 {timeout}s")
    if "e" in box:
        err = box["e"]
        if isinstance(err, RunCancelled) and ctx is not None and not ctx.cancel.cancelled:
            raise TimeoutError(f"{label} 超过 {timeout}s")
        raise err
    return box.get("r")


def run_with_fallback(step_name: str, strategies: List[Strategy]) -> Tuple[Any, str]:
    """返回 (结果, 实际生效的策略名)。会话被取消时直接抛 RunCancelled。"""
    ctx = current()
    errors: List[Tuple[str, str]] = []
    for idx, st in enumerate(strategies):
        for attempt in range(st.retries + 1):
            if ctx is not None:
                ctx.cancel.raise_if_cancelled()
            try:
                result = run_with_timeout(st.fn, st.timeout, label=f"{step_name}/{st.name}")
                if st.accept is not None and not st.accept(result):
                    raise ValueError("结果未通过验收校验")
                if idx > 0 and ctx is not None:
                    ctx.emit("step.degraded", {"step_name": step_name, "strategy": st.name,
                                               "errors": [e for _, e in errors][-3:]})
                    if ctx.metrics is not None:
                        ctx.metrics.note("degraded", step=step_name, strategy=st.name)
                return result, st.name
            except (RunCancelled, BudgetExceeded):
                raise
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {str(e)[:300]}"
                errors.append((f"{st.name}#{attempt + 1}", msg))
                if ctx is not None:
                    ctx.emit("step.retry", {"step_name": step_name, "strategy": st.name,
                                            "attempt": attempt + 1, "error": msg})
                    if ctx.metrics is not None:
                        ctx.metrics.note("step_retry", step=step_name, strategy=st.name, error=msg)
    raise StepFailed(step_name, errors)
