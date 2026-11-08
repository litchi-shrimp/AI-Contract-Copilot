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
    """在当前线程运行 fn，带超时与取消（不再为每次尝试额外创建看门狗线程）。

    超时由共享的定时器触发子取消令牌：fn 内部所有阻塞点（LLM 流、信号量、退避 sleep、工具调用）
    都监听取消令牌，会立即抛出 RunCancelled；这里再把“子令牌超时”转换为 TimeoutError 交给降级逻辑。
    会话级取消（用户停止）沿父令牌传播，原样抛出 RunCancelled。
    """
    ctx = current()
    if ctx is None:
        return fn()
    child = ctx.cancel.child()
    timed_out = {"v": False}

    def _expire():
        timed_out["v"] = True
        child.cancel(f"{label} 超时")

    handle = _deadlines.schedule(timeout, _expire) if timeout else None
    try:
        with agent_scope(cancel=child):
            return fn()
    except RunCancelled:
        if ctx.cancel.cancelled:
            raise RunCancelled(ctx.cancel.reason or "cancelled")
        if timed_out["v"]:
            raise TimeoutError(f"{label} 超过 {timeout}s")
        raise
    finally:
        if handle is not None:
            _deadlines.cancel(handle)
        child.detach()


class _DeadlineScheduler:
    """所有超时共用一个守护线程（小根堆），替代“每个任务一个 threading.Timer / 看门狗线程”"""

    def __init__(self):
        import heapq
        import itertools
        import threading
        self._heap, self._heapq, self._seq = [], heapq, itertools.count()
        self._cancelled = set()
        self._cv = threading.Condition()
        self._thread = None
        self._threading = threading

    def schedule(self, delay: float, cb: Callable[[], None]) -> int:
        hid = next(self._seq)
        with self._cv:
            self._heapq.heappush(self._heap, (time.monotonic() + delay, hid, cb))
            if self._thread is None or not self._thread.is_alive():
                self._thread = self._threading.Thread(target=self._run, daemon=True, name="deadlines")
                self._thread.start()
            self._cv.notify()
        return hid

    def cancel(self, hid: int):
        with self._cv:
            self._cancelled.add(hid)

    def _run(self):
        while True:
            with self._cv:
                while not self._heap:
                    self._cv.wait()
                when, hid, cb = self._heap[0]
                now = time.monotonic()
                if hid in self._cancelled:
                    self._heapq.heappop(self._heap)
                    self._cancelled.discard(hid)
                    continue
                if when > now:
                    self._cv.wait(when - now)
                    continue
                self._heapq.heappop(self._heap)
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass


_deadlines = _DeadlineScheduler()


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
