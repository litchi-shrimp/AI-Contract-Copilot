#!/usr/bin/env python3
"""
LLM 调用模块（测试/调试入口）

所有 Agent 的 LLM 调用都封装在 agents/config.py AgentConfig.llm_call → runtime.llm_gateway。
本文件只保留旧签名，便于单独测试模型连通性：python -m agent.llm.llm
"""
from ..runtime.errors import LLMError  # noqa: F401
from ..runtime.llm_gateway import LLMSettings, call_llm


def llm_call(system_prompt: str, user_prompt: str, api_key_name: str = "DeepSeekKey",
             model_id: str = "deepseek-flash", fallback_model_id: str = "deepseek-flash",
             base_url: str = "https://api.deepseek.com", max_retries: int = 3, stream: bool = False,
             temperature: float = 0.7, timeout: int = 180, base_delay: int = 1) -> str:
    settings = LLMSettings(api_key_name=api_key_name, model_id=model_id, fallback_model_id=fallback_model_id,
                           base_url=base_url, max_retries=max_retries, temperature=temperature, timeout=timeout,
                           base_delay=base_delay)
    return call_llm(system_prompt, user_prompt, settings)


if __name__ == "__main__":
    print(llm_call("你是一个助手", "只回复：OK"))
