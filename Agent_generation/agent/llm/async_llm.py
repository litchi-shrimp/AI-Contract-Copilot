#!/usr/bin/env python3
"""
LLM 调用兼容层

历史上这里各自实现了一套 同步/异步 + 重试/降级 逻辑（三份重复代码，取消不生效、无熔断）。
现在统一委托给 runtime.llm_gateway，本文件只保留旧函数签名以兼容调用方。
事件推送通过 RunContext.sink 完成（会话级隔离），不再依赖全局 EventBus。
"""
import asyncio
from typing import Dict, Optional, Tuple

from ..runtime.context import bind, current, new_context, use_context
from ..runtime.errors import LLMError  # noqa: F401
from ..runtime.llm_gateway import LLMSettings, call_llm


def _settings(api_key_name, model_id, fallback_model_id, base_url, max_retries, temperature, timeout,
              base_delay) -> LLMSettings:
    return LLMSettings(api_key_name=api_key_name, model_id=model_id, fallback_model_id=fallback_model_id,
                       base_url=base_url, max_retries=max_retries, temperature=temperature, timeout=timeout,
                       base_delay=base_delay)


def sync_llm_call_with_events(
    system_prompt: str,
    user_prompt: str,
    event_bus=None,
    agent_name: str = "unknown",
    step: int = None,
    stream_event_map: Dict[Tuple[str, ...], str] = None,
    api_key_name: str = "DeepSeekKey",
    model_id: str = "deepseek-flash",
    fallback_model_id: str = "deepseek-flash",
    base_url: str = "https://api.deepseek.com",
    max_retries: int = 3,
    temperature: float = 0.7,
    timeout: int = 180,
    base_delay: int = 1,
) -> str:
    """同步 LLM 调用（流式事件经当前 RunContext 推送）"""
    settings = _settings(api_key_name, model_id, fallback_model_id, base_url, max_retries, temperature, timeout,
                         base_delay)
    if current() is None and event_bus is not None:
        ctx = new_context(sink=event_bus.put_sync)
        with use_context(ctx):
            return call_llm(system_prompt, user_prompt, settings, agent=agent_name, step=step,
                            stream_event_map=stream_event_map)
    return call_llm(system_prompt, user_prompt, settings, agent=agent_name, step=step,
                    stream_event_map=stream_event_map)


async def async_llm_call(
    system_prompt: str,
    user_prompt: str,
    event_bus=None,
    agent_name: str = "unknown",
    step: int = None,
    stream_event_map: Dict[Tuple[str, ...], str] = None,
    api_key_name: str = "DeepSeekKey",
    model_id: str = "deepseek-flash",
    fallback_model_id: str = "deepseek-flash",
    base_url: str = "https://api.deepseek.com",
    max_retries: int = 3,
    temperature: float = 0.7,
    timeout: int = 180,
    base_delay: int = 1,
) -> str:
    """异步版本：放进线程池执行，不阻塞事件循环"""
    loop = asyncio.get_running_loop()
    fn = bind(lambda: sync_llm_call_with_events(system_prompt, user_prompt, event_bus, agent_name, step,
                                                stream_event_map, api_key_name, model_id, fallback_model_id,
                                                base_url, max_retries, temperature, timeout, base_delay))
    return await loop.run_in_executor(None, fn)
