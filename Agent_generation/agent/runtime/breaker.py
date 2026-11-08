"""熔断器（Circuit Breaker）

按 “base_url|model” 维度隔离。连续失败达到阈值 → OPEN（快速失败，网关直接切备用模型），
冷却时间后 → HALF_OPEN（放行少量探测请求），探测成功 → CLOSED。
只统计基础设施类失败（超时/连接/429/5xx/空响应），参数错误不计入。
"""
import threading
import time
from typing import Dict


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0,
                 half_open_max_calls: int = 1):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self):
        if self._state == self.OPEN and time.monotonic() - self._opened_at >= self.recovery_timeout:
            self._state = self.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> bool:
        with self._lock:
            self._maybe_half_open()
            if self._state == self.CLOSED:
                return True
            if self._state == self.HALF_OPEN and self._half_open_inflight < self.half_open_max_calls:
                self._half_open_inflight += 1
                return True
            return False

    def record_success(self):
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0
            self._half_open_inflight = 0

    def record_failure(self):
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = time.monotonic()

    def reset(self):
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0
            self._half_open_inflight = 0


_registry: Dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    with _registry_lock:
        br = _registry.get(name)
        if br is None:
            br = CircuitBreaker(name, **kwargs)
            _registry[name] = br
        return br


def reset_all_breakers():
    with _registry_lock:
        for br in _registry.values():
            br.reset()


def breaker_states() -> Dict[str, str]:
    with _registry_lock:
        items = list(_registry.items())
    return {k: v.state for k, v in items}
