import os
import sys
from pathlib import Path
from typing import Optional

CURRENT_FILE = Path(__file__).resolve()
CORE_DIR = CURRENT_FILE.parent          # agent/core/
AGENT_DIR = CORE_DIR.parent             # agent/
AGENTS_DIR = AGENT_DIR / "agents"       # agent/agents/
BASE_DIR = AGENT_DIR.parent             # 项目根目录


class BaseAgent:
    """所有Agent的基类，提供公共的配置初始化和工具组件"""

    def __init__(self, agent_name: str, config_path: Optional[Path] = None, event_bus=None):
        """
        Args:
            agent_name: Agent名称，也用于查找对应配置文件
            config_path: 配置文件路径，为None时自动在 configs/ 下查找
            event_bus: EventBus 实例（通过 property 访问，外部可通过 setter 注入）
        """
        self.agent_name = agent_name
        self._event_bus = event_bus

        # 自动推断配置文件路径
        if config_path is None:
            config_path = self._infer_config_path(agent_name)
        self.config_path = Path(config_path)

        # 延迟导入避免循环引用
        from ..agents.config import AgentConfig
        from ..utils.response_parser import ResponseParser
        from ..utils.history_manager import HistoryManager
        from ..utils.logger import get_logger

        self.config = AgentConfig(str(self.config_path))
        self.llm_call = self.config.llm_call
        self.response_parser = ResponseParser()
        self.history_manager = HistoryManager()
        self.logger = get_logger(agent_name)

    @property
    def event_bus(self):
        return self._event_bus

    @event_bus.setter
    def event_bus(self, value):
        self._event_bus = value

    async def async_llm_call(self, system_prompt: str, user_prompt: str, step: int = None) -> str:
        """异步流式 LLM 调用（自动绑定 event_bus 和 agent_name）"""
        return await self.config.async_llm_call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            event_bus=self.event_bus,
            agent_name=self.agent_name,
            step=step,
        )

    def patch_llm_call_for_events(self, step: int = None):
        """用同步流式包装器替换 self.llm_call

        替换后 Agent 内所有 self.llm_call() 调用会通过 async_llm_call
        逐 token 推送到 EventBus，无需改造 Agent 代码。

        同时自动读取 Agent 类上的 STREAM_EVENT_MAP（如已定义），
        用于在流式输出时将不同字段的 token 路由（映射）到不同事件类型。
        """
        eb = self.event_bus
        if eb is None:
            return
        from ..llm.async_llm import sync_llm_call_with_events
        from ..agents.config import AgentConfig
        cfg = self.config
        agent_name = self.agent_name
        stream_event_map = getattr(self.__class__, 'STREAM_EVENT_MAP', None)

        # 1. 在 AgentConfig 类级别存储流式上下文（跨线程生效）
        AgentConfig._streaming_ctx = {
            "event_bus": eb,
            "agent_name": agent_name,
            "step": step,
            "stream_event_map": stream_event_map,
        }

        # 2. 一次性 patch AgentConfig.llm_call（类级别，仅一次）
        if not getattr(AgentConfig, '_llm_call_patched', False):
            _original_llm = AgentConfig.llm_call
            def _patched_llm_call(self_cfg, system_prompt, user_prompt):
                ctx = getattr(AgentConfig, '_streaming_ctx', None)
                if ctx and ctx.get("event_bus") and not ctx["event_bus"]._closed:
                    return sync_llm_call_with_events(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        event_bus=ctx["event_bus"],
                        agent_name=ctx["agent_name"],
                        step=ctx["step"],
                        stream_event_map=ctx["stream_event_map"],
                        api_key_name=self_cfg.api_key_name,
                        model_id=self_cfg.model_id,
                        fallback_model_id=self_cfg.fallback_model_id,
                        base_url=self_cfg.base_url,
                        max_retries=self_cfg.max_retries,
                        temperature=self_cfg.temperature,
                        timeout=self_cfg.timeout,
                        base_delay=self_cfg.base_delay,
                    )
                return _original_llm(self_cfg, system_prompt, user_prompt)
            AgentConfig.llm_call = _patched_llm_call
            AgentConfig._llm_call_patched = True

        # 3. 仍然替换 self.llm_call（给直接通过 self.llm_call 调用为没有通过AgentConfig调用的 Agent 用）
        def _streaming_wrapper(system_prompt: str, user_prompt: str) -> str:
            return sync_llm_call_with_events(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                event_bus=eb,
                agent_name=agent_name,
                step=step,
                stream_event_map=stream_event_map,
                api_key_name=cfg.api_key_name,
                model_id=cfg.model_id,
                fallback_model_id=cfg.fallback_model_id,
                base_url=cfg.base_url,
                max_retries=cfg.max_retries,
                temperature=cfg.temperature,
                timeout=cfg.timeout,
                base_delay=cfg.base_delay,
            )

        self._original_llm_call = self.llm_call
        self.llm_call = _streaming_wrapper

    @staticmethod
    def _infer_config_path(agent_name: str) -> Path:
        """根据Agent名称推断配置文件路径

        命名规则: config_{snake_case_name}.json
        例如: UserFieldExtractionAgent → config_user_field_extraction.json

        若找不到精确匹配，尝试模糊查找
        """
        # 生成可能的配置文件名
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

        # 兜底：根据类名转换为蛇形命名查找
        snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in agent_name).lstrip("_")
        snake = snake.replace("agent", "")
        return BASE_DIR / "configs" / f"config_{snake}.json"
