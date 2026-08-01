#!/usr/bin/env python3
"""
Session 管理器

管理所有合同生成 Session 的生命周期：
- Session 创建与销毁
- EventBus 实例管理
- 超时检测与清理
- 断线重连支持
"""
import asyncio
import time
import uuid
import logging
from typing import Optional, Dict, Any

from agent.utils.event_bus import EventBus
from agent.utils.Session import Session

logger = logging.getLogger("session_manager")


class SessionState:
    """单个 Session 的状态"""

    def __init__(self, session_id: str, timeout_seconds: int = 1800):
        self.session_id = session_id
        self.event_bus = EventBus(session_id)
        try:
            self.event_bus.attach_loop(asyncio.get_event_loop())
        except RuntimeError:
            pass
        self.session = Session(session_id, timeout_seconds)
        self.workflow_task: Optional[asyncio.Task] = None
        self.created_at = time.time()
        self.current_step: int = 0
        self.current_agent: str = ""
        self.status: str = "idle"  # idle | running | waiting_input | completed | error
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.sse_connections: int = 0
        self._cancelled = False

    def refresh(self):
        self.session.refresh()

    @property
    def is_expired(self) -> bool:
        if self._cancelled:
            return True
        return self.session.is_expired()

    def cancel(self):
        """标记取消，并立即解阻塞 EventBus 上的 wait_for_user_input 等待"""
        self._cancelled = True
        self.event_bus.set_user_input("")  # 解阻塞任何 wait_for_user_input
        self.event_bus.close()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "current_step": self.current_step,
            "current_agent": self.current_agent,
            "created_at": self.created_at,
            "sse_connections": self.sse_connections,
            "is_expired": self.is_expired,
        }


class SessionManager:
    """全局 Session 管理器，单例"""

    def __init__(self, cleanup_interval: int = 60):
        self._sessions: Dict[str, SessionState] = {}
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                expired_ids = [
                    sid for sid, state in self._sessions.items()
                    if state.is_expired
                ]
                for sid in expired_ids:
                    await self.destroy_session(sid)
                if expired_ids:
                    logger.info(f"清理了 {len(expired_ids)} 个过期 Session")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理任务异常: {e}")

    def create_session(self, timeout_seconds: int = 1800) -> str:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        state = SessionState(session_id, timeout_seconds)
        self._sessions[session_id] = state
        logger.info(f"创建 Session: {session_id}")
        return session_id

    def get_session(self, session_id: str) -> Optional[SessionState]:
        state = self._sessions.get(session_id)
        if not state:
            return None
        if state._cancelled or state.is_expired:
            logger.info(f"Session {session_id} 已过期或已取消")
            asyncio.ensure_future(self.destroy_session(session_id))
            return None
        return state

    async def destroy_session(self, session_id: str):
        state = self._sessions.pop(session_id, None)
        if state:
            state.cancel()
            if state.workflow_task and not state.workflow_task.done():
                state.workflow_task.cancel()
                try:
                    await asyncio.wait_for(state.workflow_task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            state.event_bus.close()
            logger.info(f"销毁 Session: {session_id}")

    def list_sessions(self) -> list:
        return [state.to_dict() for state in self._sessions.values() if not state._cancelled and not state.is_expired]


_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
