#!/usr/bin/env python3
"""
事件总线模块 — 异步 SSE 驱动核心

每个 Session 一个 EventBus 实例，Agent 输出 → asyncio.Queue → SSE 推送。
支持断线重连（EventRingBuffer）、流式 Token、交互等待。
"""
import asyncio
import threading
import time
import contextvars
from typing import Optional


class EventRingBuffer:
    """环形缓冲区，缓存最近 N 条事件供重连使用 (Last-Event-ID)"""

    def __init__(self, maxlen: int = 200):
        self.buffer = []
        self.maxlen = maxlen

    def append(self, event: dict):
        if len(self.buffer) >= self.maxlen:
            self.buffer.pop(0)
        self.buffer.append(event)

    def get_since(self, last_event_id: str) -> list:
        for i, evt in enumerate(self.buffer):
            if evt.get("event_id") == last_event_id:
                return self.buffer[i + 1:]
        return list(self.buffer)


class EventBus:
    """每个 Session 一个的事件总线，Agent 输出 → SSE 推送

    所有 Agent 产生的输出（LLM token、思考过程、工具调用、系统消息等）
    都通过 EventBus 进入异步队列，然后由 SSE 端点消费推送到前端。
    """

    def __init__(self, session_id: str, maxsize: int = 10000):
        self.session_id = session_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.buffer = EventRingBuffer(maxlen=200)
        self._event_counter = 0
        self._event_id_lock = asyncio.Lock()
        self._user_input_event: Optional[asyncio.Event] = None
        self._user_input_sync_event: Optional[threading.Event] = None
        self._last_user_input: Optional[str] = None
        self._closed = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def _next_event_id(self) -> str:
        async with self._event_id_lock:
            self._event_counter += 1
            return f"evt_{self._event_counter:x}"

    async def put(self, event_type: str, step: int = None, agent: str = None, data: dict = None):
        """通用事件推送

        Args:
            event_type: 事件类型
            step: 当前步骤编号
            agent: 当前 Agent 名称
            data: 事件数据字典（直接作为 event.data 字段）
        """
        if self._closed:
            return
        event_id = await self._next_event_id()
        event = {
            "event_id": event_id,
            "session_id": self.session_id,
            "event_type": event_type,
            "step": step,
            "agent": agent,
            "timestamp": time.time(),
            "data": data or {},
        }
        self.buffer.append(event)
        await self.queue.put(event)

    def put_sync(self, event_type: str, step: int = None, agent: str = None, data: dict = None):
        """同步版本 put，供线程中运行的 Agent 调用

        通过 asyncio.run_coroutine_threadsafe 将事件投递到主事件循环。
        """
        if self._closed or self._loop is None:
            return
        self._event_counter += 1
        event_id = f"evt_{self._event_counter:x}"
        event = {
            "event_id": event_id,
            "session_id": self.session_id,
            "event_type": event_type,
            "step": step,
            "agent": agent,
            "timestamp": time.time(),
            "data": data or {},
        }
        self.buffer.append(event)
        asyncio.run_coroutine_threadsafe(self.queue.put(event), self._loop)

    # ==================== Workflow 生命周期 ====================

    async def emit_workflow_start(self, steps_total: int = 8):
        await self.put("workflow.start", data={"steps_total": steps_total})

    async def emit_step_start(self, step: int, name: str):
        await self.put("step.start", step=step, data={"step_name": name})

    async def emit_step_complete(self, step: int, name: str, duration: float, **extra):
        await self.put("step.complete", step=step, data={"step_name": name, "duration": duration, **extra})

    async def emit_step_error(self, step: int, name: str, error: str):
        await self.put("step.error", step=step, data={"step_name": name, "error": error})

    async def emit_workflow_complete(self, total_duration: float, output_paths: list = None):
        await self.put("workflow.complete", data={"total_duration": total_duration, "output_paths": output_paths or []})

    async def emit_agent_start(self, agent: str, step: int = None, input_summary: str = ""):
        await self.put("agent.start", step=step, agent=agent, data={"input_summary": input_summary})

    async def emit_agent_end(self, agent: str, duration: float, step: int = None):
        await self.put("agent.end", step=step, agent=agent, data={"duration": duration})

    # ==================== ReAct 循环事件 ====================

    async def emit_agent_think(self, agent: str, loop: int, content: str, step: int = None):
        await self.put("agent.think", step=step, agent=agent, data={"loop": loop, "content": content})

    async def emit_agent_act(self, agent: str, loop: int, act_type: str, params: dict, completed: bool, step: int = None):
        await self.put("agent.act", step=step, agent=agent, data={"loop": loop, "act_type": act_type, "params": params, "completed": completed})

    async def emit_agent_observation(self, agent: str, loop: int, content: str, step: int = None):
        await self.put("agent.observation", step=step, agent=agent, data={"loop": loop, "content": content})

    async def emit_agent_complete(self, agent: str, loop: int, completed: bool, step: int = None):
        await self.put("agent.complete", step=step, agent=agent, data={"loop": loop, "completed": completed})

    # ==================== LLM 流式事件 ====================

    async def emit_llm_start(self, agent: str, model: str, step: int = None):
        await self.put("llm.start", step=step, agent=agent, data={"model": model})

    async def emit_llm_token(self, agent: str, model: str, text: str, step: int = None):
        await self.put("llm.token", step=step, agent=agent, data={"model": model, "text": text})

    async def emit_llm_done(self, agent: str, model: str, full_text: str, usage: dict = None, step: int = None):
        await self.put("llm.done", step=step, agent=agent, data={"model": model, "full_text": full_text, "usage": usage or {}})

    async def emit_llm_error(self, agent: str, model: str, error: str, retry_count: int = 0, step: int = None):
        await self.put("llm.error", step=step, agent=agent, data={"model": model, "error": error, "retry_count": retry_count})

    # ==================== 工具调用事件 ====================

    async def emit_tool_call(self, agent: str, tool_name: str, params: dict, step: int = None):
        await self.put("tool.call", step=step, agent=agent, data={"tool_name": tool_name, "params": params})

    async def emit_tool_result(self, agent: str, tool_name: str, success: bool, summary: str, step: int = None):
        await self.put("tool.result", step=step, agent=agent, data={"tool_name": tool_name, "success": success, "summary": summary})

    # ==================== 交互事件 ====================

    async def emit_agent_ask(self, agent: str, question: str, step: int = None):
        await self.put("agent.ask", step=step, agent=agent, data={"question": question, "expects_input": True})

    async def emit_user_input(self, text: str, step: int = None):
        await self.put("user.input", step=step, data={"text": text})

    # ==================== 并行事件 ====================

    async def emit_parallel_start(self, group: str, agents: list, step: int = None):
        await self.put("parallel.start", step=step, data={"group": group, "agents": agents})

    async def emit_parallel_agent_start(self, group: str, agent: str, progress: str, step: int = None):
        await self.put("parallel.agent_start", step=step, agent=agent, data={"group": group, "progress": progress})

    async def emit_parallel_agent_complete(self, group: str, agent: str, duration: float, step: int = None):
        await self.put("parallel.agent_complete", step=step, agent=agent, data={"group": group, "duration": duration})

    async def emit_parallel_complete(self, group: str, agents_count: int, step: int = None):
        await self.put("parallel.complete", step=step, data={"group": group, "agents_count": agents_count})

    # ==================== Session 事件 ====================

    async def emit_ping(self):
        await self.put("session.ping", data={"message": "keepalive"})

    async def emit_timeout(self, timeout_seconds: int, last_active: float):
        await self.put("session.timeout", data={"timeout_seconds": timeout_seconds, "last_active": last_active})

    async def emit_session_end(self, reason: str):
        await self.put("session.end", data={"reason": reason})

    async def emit_system(self, agent: str, text: str, step: int = None):
        await self.put("system", step=step, agent=agent, data={"message": text})

    async def emit_error(self, source: str, error_type: str, message: str):
        await self.put("error", data={"source": source, "type": error_type, "message": message})

    # ==================== 交互等待 ====================

    async def wait_for_user_input(self, timeout: float = 1800) -> Optional[str]:
        """异步等待用户输入（协程中使用）"""
        self._user_input_event = asyncio.Event()
        try:
            await asyncio.wait_for(self._user_input_event.wait(), timeout=timeout)
            return self._last_user_input
        except asyncio.TimeoutError:
            return None
        finally:
            self._user_input_event = None

    def wait_for_user_input_sync(self, timeout: float = 1800) -> Optional[str]:
        """同步等待用户输入（线程中使用）

        供在线程中运行的 Agent 调用，底层使用 threading.Event，
        与 async wait_for_user_input 共享同一个 _last_user_input。
        """
        self._user_input_sync_event = threading.Event()
        if not self._user_input_sync_event.wait(timeout=timeout):
            self._user_input_sync_event = None
            return None
        self._user_input_sync_event = None
        return self._last_user_input

    def set_user_input(self, text: str):
        """设置用户输入（由 /input 端点调用）"""
        self._last_user_input = text
        if self._user_input_event:
            self._user_input_event.set()
        if self._user_input_sync_event:
            self._user_input_sync_event.set()

    # ==================== 队列操作 ====================

    async def drain(self) -> list:
        """取出当前所有积压事件（非阻塞）"""
        events = []
        while not self.queue.empty():
            try:
                events.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    def close(self):
        self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed


_current_bus: contextvars.ContextVar[Optional[EventBus]] = contextvars.ContextVar("event_bus", default=None)


def get_event_bus() -> Optional[EventBus]:
    return _current_bus.get()


def set_event_bus(bus: EventBus):
    _current_bus.set(bus)


def release_event_bus():
    _current_bus.set(None)
