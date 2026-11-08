#!/usr/bin/env python3
"""
配置管理模块

description: 管理智能体的配置参数（LLM + Agent），是配置的唯一入口。
所有 LLM 调用统一委托给 runtime.llm_gateway（重试 / 降级 / 熔断 / 超时 / 取消 / 预算 / 事件）。
"""
import json
import sys
from typing import TYPE_CHECKING, Optional

from ..runtime.errors import LLMError  # noqa: F401  兼容旧代码：from ..agents.config import LLMError
from ..runtime.llm_gateway import LLMSettings, call_llm, resolve_api_key

if TYPE_CHECKING:
    from ..utils.event_bus import EventBus

_FIELDS = ("api_key_name", "base_url", "model_id", "fallback_model_id", "temperature", "timeout", "max_retries",
           "base_delay", "stream", "max_loop", "inner_max_loop", "idle_timeout", "connect_timeout",
           "fallback_after", "max_tokens", "thinking", "seed", "step_timeout", "max_llm_calls")


class AgentConfig:
    def __init__(self, config_file=None):
        # LLM 配置（默认值；配置文件覆盖）
        self.api_key_name = "DeepSeekKey"
        self.base_url = "https://api.deepseek.com"
        self.model_id = "deepseek-flash"
        self.fallback_model_id = "deepseek-flash"
        self.temperature = 0.4
        self.timeout = 200
        self.max_retries = 3
        self.base_delay = 1
        self.stream = False
        self.idle_timeout = 90
        self.connect_timeout = 15
        self.fallback_after = 2
        self.max_tokens = None
        self.thinking = None
        self.seed = None

        # 智能体配置
        self.max_loop = 30
        self.inner_max_loop = 5
        self.step_timeout = None
        self.max_llm_calls = None

        if config_file:
            self._load_config(config_file)

    def _load_config(self, config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            for k in _FIELDS:
                if k in config:
                    setattr(self, k, config[k])
        except Exception as e:
            print(f"[警告] 加载配置文件失败 {config_file}: {e}", file=sys.stderr)

    @property
    def settings(self) -> LLMSettings:
        return LLMSettings.from_config(self)

    def get_api_key(self):
        return resolve_api_key(self.api_key_name)

    def llm_call(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        """同步 LLM 调用（经网关）。kwargs 透传：temperature / json_mode / max_tokens / agent / step"""
        return call_llm(system_prompt, user_prompt, self.settings, **kwargs)

    async def async_llm_call(self, system_prompt: str, user_prompt: str, event_bus: "EventBus" = None,
                             agent_name: str = "unknown", step: Optional[int] = None) -> str:
        """异步调用：在线程池中执行网关调用，事件循环不被阻塞"""
        import asyncio
        from ..runtime.context import bind
        loop = asyncio.get_running_loop()
        fn = bind(lambda: call_llm(system_prompt, user_prompt, self.settings, agent=agent_name, step=step))
        return await loop.run_in_executor(None, fn)
