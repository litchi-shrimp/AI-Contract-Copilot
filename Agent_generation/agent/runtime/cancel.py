"""可层级传播的取消令牌

- 父令牌取消 → 所有子令牌视为已取消（用户停止整个会话）
- 子令牌取消 → 仅影响该子任务（单个 Agent 超时，编排层可降级继续）
- 支持注册回调：取消时立即关闭正在读取的 HTTP 流、唤醒等待用户输入的线程
"""
import threading
from typing import Callable, List, Optional

from .errors import RunCancelled


class CancelToken:
    def __init__(self, parent: Optional["CancelToken"] = None):
        self._event = threading.Event()
        self._parent = parent
        self._reason: Optional[str] = None
        self._callbacks: List[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._unlink = parent.add_callback(self._on_parent_cancel) if parent is not None else None

    def _on_parent_cancel(self):
        self._fire(self._parent.reason if self._parent else "cancelled")

    @property
    def reason(self) -> Optional[str]:
        if self._event.is_set():
            return self._reason
        if self._parent is not None and self._parent.cancelled:
            return self._parent.reason
        return None

    @property
    def cancelled(self) -> bool:
        if self._event.is_set():
            return True
        return self._parent is not None and self._parent.cancelled

    def cancel(self, reason: str = "cancelled"):
        self._fire(reason)

    def _fire(self, reason: str):
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

    def add_callback(self, cb: Callable[[], None]) -> Callable[[], None]:
        """注册取消回调；若已取消则立即执行。返回反注册函数"""
        run_now = False
        with self._lock:
            if self._event.is_set():
                run_now = True
            else:
                self._callbacks.append(cb)
        if run_now:
            try:
                cb()
            except Exception:
                pass

        def _remove():
            with self._lock:
                if cb in self._callbacks:
                    self._callbacks.remove(cb)
        return _remove

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RunCancelled(self.reason or "cancelled")

    def wait(self, seconds: float) -> bool:
        """阻塞等待至多 seconds 秒，被取消则提前返回 True"""
        if self.cancelled:
            return True
        return self._event.wait(seconds) or self.cancelled

    def sleep(self, seconds: float):
        """可被取消打断的 sleep，取消时抛 RunCancelled"""
        if seconds > 0:
            self._event.wait(seconds)
        self.raise_if_cancelled()

    def child(self) -> "CancelToken":
        return CancelToken(parent=self)

    def detach(self):
        """子任务结束后从父令牌摘除回调，避免长会话中父令牌累积成千上万个失效回调（内存泄漏）"""
        if self._unlink is not None:
            self._unlink()
            self._unlink = None
