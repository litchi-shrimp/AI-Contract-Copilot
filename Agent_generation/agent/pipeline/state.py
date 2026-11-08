"""流水线状态（可序列化 → 检查点）

检查点设计：
- 每个步骤成功完成后，把该步骤的“产物”写入 state.artifacts，并 state.step_done 追加步骤号，然后整体持久化（原子）
- 交互步骤（1/4/8）每轮对话完成后也持久化（对话历史 + 当前大纲/合同）
- 步骤执行中途被取消/崩溃：不会写入半成品；恢复时从最后一个完整检查点重放该步骤
- version 单调递增，持久化层用乐观锁（version 比较）防止旧状态覆盖新状态
"""
import copy
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

STEP_NAMES = {
    1: "需求提取",
    2: "模板匹配",
    3: "大纲生成",
    4: "大纲确认",
    5: "合同起草",
    6: "多维审查",
    7: "修订",
    8: "终稿确认",
}
INTERACTIVE_STEPS = {1, 4, 8}


@dataclass
class PipelineState:
    session_id: str
    status: str = "idle"          # idle | running | waiting_input | paused | completed | failed | closed
    current_step: int = 1
    step_done: List[int] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    interactive: Dict[str, Any] = field(default_factory=dict)   # 交互步骤的对话状态
    degraded: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    version: int = 0
    updated_at: float = field(default_factory=time.time)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return copy.deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, d: dict) -> "PipelineState":
        d = dict(d or {})
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})

    def mark_done(self, step: int):
        if step not in self.step_done:
            self.step_done.append(step)
        self.current_step = step + 1

    @property
    def contract(self) -> Optional[dict]:
        return self.artifacts.get("contract") or self.artifacts.get("outline")
