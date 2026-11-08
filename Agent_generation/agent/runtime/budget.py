"""单次运行的资源预算与指标统计（线程安全）

预算用于 Harness 约束：防止 Agent 死循环/失控导致 Token 与费用爆炸。
指标用于可观测性与评测（调用次数、重试、降级、耗时、Token）。
"""
import threading
import time
from collections import defaultdict
from typing import Optional

from .errors import BudgetExceeded


class Budget:
    def __init__(self, max_llm_calls: Optional[int] = None, max_total_tokens: Optional[int] = None,
                 max_tool_calls: Optional[int] = None, wall_clock_seconds: Optional[float] = None):
        self.max_llm_calls = max_llm_calls
        self.max_total_tokens = max_total_tokens
        self.max_tool_calls = max_tool_calls
        self.deadline = time.monotonic() + wall_clock_seconds if wall_clock_seconds else None
        self.llm_calls = 0
        self.total_tokens = 0
        self.tool_calls = 0
        self._lock = threading.Lock()

    def check(self):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise BudgetExceeded("超出单次运行最大时长")
        if self.max_total_tokens is not None and self.total_tokens >= self.max_total_tokens:
            raise BudgetExceeded(f"超出单次运行 Token 预算（{self.max_total_tokens}）")

    def charge_llm_call(self):
        with self._lock:
            self.check()
            if self.max_llm_calls is not None and self.llm_calls >= self.max_llm_calls:
                raise BudgetExceeded(f"超出单次运行 LLM 调用次数上限（{self.max_llm_calls}）")
            self.llm_calls += 1

    def charge_tokens(self, n: int):
        with self._lock:
            self.total_tokens += int(n or 0)

    def charge_tool_call(self):
        with self._lock:
            if self.max_tool_calls is not None and self.tool_calls >= self.max_tool_calls:
                raise BudgetExceeded(f"超出单次运行工具调用次数上限（{self.max_tool_calls}）")
            self.tool_calls += 1

    def to_dict(self) -> dict:
        return {"llm_calls": self.llm_calls, "total_tokens": self.total_tokens, "tool_calls": self.tool_calls,
                "max_llm_calls": self.max_llm_calls, "max_total_tokens": self.max_total_tokens,
                "max_tool_calls": self.max_tool_calls}


class RunMetrics:
    """按 agent / model 聚合的调用指标，以及关键事件（重试、降级、熔断、兜底）留痕"""

    def __init__(self):
        self._lock = threading.Lock()
        self.by_model = defaultdict(lambda: defaultdict(float))
        self.by_agent = defaultdict(lambda: defaultdict(float))
        self.events = []

    def record(self, agent: str, model: str, **values):
        with self._lock:
            for k, v in values.items():
                self.by_model[model][k] += v
                self.by_agent[agent][k] += v

    def note(self, kind: str, **data):
        with self._lock:
            self.events.append({"kind": kind, "ts": time.time(), **data})
            if len(self.events) > 2000:
                del self.events[:1000]

    def count(self, kind: str) -> int:
        with self._lock:
            return sum(1 for e in self.events if e["kind"] == kind)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "by_model": {k: dict(v) for k, v in self.by_model.items()},
                "by_agent": {k: dict(v) for k, v in self.by_agent.items()},
                "events": list(self.events[-500:]),
            }
