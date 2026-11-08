"""运行上下文（RunContext）—— 会话级隔离的核心

每个会话 / 每次评测运行持有一个 RunContext：
- cancel:    取消令牌（停止按钮、会话关闭、步骤超时）
- workspace: 会话私有工作目录（所有中间产物只写这里，会话间互不影响）
- sink:      事件出口（SSE / 数据库事件流 / 评测 trace）
- budget:    调用预算（防失控）
- metrics:   指标
- agent/step/stream_event_map: 当前 Agent 作用域（替代原来的类级全局 _streaming_ctx，避免并行 Agent 串台）

通过 contextvars 传递；跨线程时用 spawn_thread / run_in_thread 复制上下文。
"""
import contextvars
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .budget import Budget, RunMetrics
from .cancel import CancelToken

EventSink = Callable[..., None]


@dataclass(frozen=True)
class RunContext:
    run_id: str
    cancel: CancelToken
    workspace: Optional[Path] = None
    sink: Optional[EventSink] = None
    budget: Optional[Budget] = None
    metrics: Optional[RunMetrics] = None
    agent: str = "unknown"
    step: Optional[int] = None
    stream_event_map: Optional[Dict[Tuple[str, ...], str]] = None
    tags: Optional[Dict[str, Any]] = None

    def emit(self, event_type: str, data: Optional[dict] = None, agent: Optional[str] = None,
             step: Optional[int] = None):
        """向事件出口推送事件；事件推送失败绝不影响主流程"""
        if self.sink is None:
            return
        try:
            self.sink(event_type, step=self.step if step is None else step,
                      agent=agent or self.agent, data=data or {})
        except Exception:
            pass

    def derive(self, **kwargs) -> "RunContext":
        return replace(self, **kwargs)


_current: contextvars.ContextVar[Optional[RunContext]] = contextvars.ContextVar("run_context", default=None)


def new_context(run_id: Optional[str] = None, workspace: Optional[Path] = None, sink: Optional[EventSink] = None,
                budget: Optional[Budget] = None, cancel: Optional[CancelToken] = None, **kwargs) -> RunContext:
    ws = Path(workspace) if workspace else None
    if ws is not None:
        ws.mkdir(parents=True, exist_ok=True)
    return RunContext(run_id=run_id or uuid.uuid4().hex[:12], cancel=cancel or CancelToken(), workspace=ws,
                      sink=sink, budget=budget, metrics=RunMetrics(), **kwargs)


def current() -> Optional[RunContext]:
    return _current.get()


@contextmanager
def use_context(ctx: Optional[RunContext]):
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


@contextmanager
def agent_scope(agent: Optional[str] = None, step: Optional[int] = None,
                stream_event_map: Optional[dict] = None, cancel: Optional[CancelToken] = None):
    """进入某个 Agent 的作用域（名称 / 步骤 / 流式字段路由 / 子取消令牌）"""
    base = current()
    if base is None:
        yield None
        return
    kwargs = {}
    if agent is not None:
        kwargs["agent"] = agent
    if step is not None:
        kwargs["step"] = step
    if stream_event_map is not None:
        kwargs["stream_event_map"] = stream_event_map
    if cancel is not None:
        kwargs["cancel"] = cancel
    with use_context(base.derive(**kwargs)) as ctx:
        yield ctx


def check_cancelled():
    ctx = current()
    if ctx is not None:
        ctx.cancel.raise_if_cancelled()


def is_cancelled() -> bool:
    ctx = current()
    return bool(ctx and ctx.cancel.cancelled)


def emit(event_type: str, data: Optional[dict] = None, **kwargs):
    ctx = current()
    if ctx is not None:
        ctx.emit(event_type, data, **kwargs)


def spawn_thread(target: Callable, args: tuple = (), kwargs: Optional[dict] = None, daemon: bool = True,
                 name: Optional[str] = None) -> threading.Thread:
    """创建携带当前 RunContext 的线程（threading.Thread 默认不继承 contextvars）"""
    ctx = contextvars.copy_context()
    thread = threading.Thread(target=ctx.run, args=(target, *args), kwargs=kwargs or {}, daemon=daemon, name=name)
    return thread


def bind(fn: Callable) -> Callable:
    """把函数与当前上下文绑定，可安全丢进线程池"""
    ctx = contextvars.copy_context()

    def _bound(*a, **kw):
        return ctx.run(fn, *a, **kw)
    return _bound
