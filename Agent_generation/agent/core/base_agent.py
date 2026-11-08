import sys
from pathlib import Path
from typing import Optional

CURRENT_FILE = Path(__file__).resolve()
CORE_DIR = CURRENT_FILE.parent          # agent/core/
AGENT_DIR = CORE_DIR.parent             # agent/
AGENTS_DIR = AGENT_DIR / "agents"       # agent/agents/
BASE_DIR = AGENT_DIR.parent             # 项目根目录（Agent_generation）


class BaseAgent:
    """所有Agent的基类，提供公共的配置初始化和工具组件

    LLM 调用：self.llm_call(system, user) → AgentConfig.llm_call → runtime.llm_gateway
    - 自动带上 agent 名称 / step / STREAM_EVENT_MAP（按 Agent 实例绑定，不再写类级全局变量，避免并行 Agent 串台）
    - 每次调用前检查取消令牌；取消时抛 RunCancelled（BaseException），不会被 Agent 内部 except Exception 吞掉
    """

    def __init__(self, agent_name: str, config_path: Optional[Path] = None, event_bus=None):
        self.agent_name = agent_name
        self._event_bus = event_bus
        self._step: Optional[int] = None

        if config_path is None:
            config_path = self._infer_config_path(agent_name)
        self.config_path = Path(config_path)

        from ..agents.config import AgentConfig
        from ..utils.response_parser import ResponseParser
        from ..utils.history_manager import HistoryManager
        from ..utils.logger import get_logger

        self.config = AgentConfig(str(self.config_path))
        self.response_parser = ResponseParser()
        self.history_manager = HistoryManager()
        self.logger = get_logger(agent_name)

    # ------------------------------------------------------------ LLM
    def llm_call(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        from ..runtime.context import check_cancelled
        check_cancelled()
        kwargs.setdefault("agent", self.agent_name)
        if self._step is not None:
            kwargs.setdefault("step", self._step)
        sem = getattr(self.__class__, "STREAM_EVENT_MAP", None)
        if sem is not None:
            kwargs.setdefault("stream_event_map", sem)
        return self.config.llm_call(system_prompt, user_prompt, **kwargs)

    async def async_llm_call(self, system_prompt: str, user_prompt: str, step: int = None) -> str:
        return await self.config.async_llm_call(system_prompt=system_prompt, user_prompt=user_prompt,
                                                agent_name=self.agent_name, step=step or self._step)

    # ------------------------------------------------------------ 事件
    @property
    def event_bus(self):
        return self._event_bus

    @event_bus.setter
    def event_bus(self, value):
        self._event_bus = value

    def emit(self, event_type: str, data: Optional[dict] = None):
        """向当前会话推送事件（优先 RunContext，兼容旧 EventBus）"""
        from ..runtime.context import current
        ctx = current()
        if ctx is not None and ctx.sink is not None:
            ctx.emit(event_type, data or {}, agent=self.agent_name, step=self._step)
        elif self._event_bus is not None:
            try:
                self._event_bus.put_sync(event_type, step=self._step, agent=self.agent_name, data=data or {})
            except Exception:
                pass

    def patch_llm_call_for_events(self, step: int = None):
        """兼容旧编排代码：记录 step；事件流由 RunContext 统一处理，无需再 monkey-patch"""
        self._step = step

    # ------------------------------------------------------------ 取消
    @staticmethod
    def check_cancelled():
        from ..runtime.context import check_cancelled
        check_cancelled()

    @staticmethod
    def is_cancelled() -> bool:
        from ..runtime.context import is_cancelled
        return is_cancelled()

    @staticmethod
    def _infer_config_path(agent_name: str) -> Path:
        name_mapping = {
            "UserFieldExtractionAgent": "config_user_need_extraction.json",
            "OutlineGenerationAgent": "config_outline_generation.json",
            "OutlineModificationAgent": "config_outline_modification.json",
            "DrafterAgent": "config_initial_contract.json",
            "LeaderAgent": "config_leader.json",
            "ReviewCompletenessAgent": "config_review.json",
            "ReviewConsistencyAgent": "config_review.json",
            "ReviewLegalAgent": "config_review.json",
            "ReviewUsageAgent": "config_review.json",
        }
        config_name = name_mapping.get(agent_name)
        if config_name:
            return BASE_DIR / "configs" / config_name
        snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in agent_name).lstrip("_")
        snake = snake.replace("agent", "")
        path = BASE_DIR / "configs" / f"config_{snake}.json"
        if not path.exists():
            print(f"[警告] 未找到 {agent_name} 的配置文件，使用默认 LLM 配置", file=sys.stderr)
        return path
