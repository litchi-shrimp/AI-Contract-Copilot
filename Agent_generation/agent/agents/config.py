#!/usr/bin/env python3
"""
配置管理模块

description: 管理智能体的配置参数，同时包含LLM和Agent配置，是配置的唯一入口
"""
import os
import sys
import time
import random
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError, InternalServerError, AuthenticationError, BadRequestError
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..utils.event_bus import EventBus


class LLMError(Exception):
    """LLM 调用失败异常"""
    pass

class AgentConfig:
    def __init__(self, config_file=None):
        """初始化配置
        
        Args:
            config_file: 配置文件路径
        """
        # LLM 配置
        self.api_key_name = "baiLianKey"
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model_id = "deepseek-v3.2"
        self.fallback_model_id = "deepseek-v4-flash"
        self.temperature = 0.4
        self.timeout = 200
        self.max_retries = 2
        self.base_delay = 1
        self.stream = False
        
        # 智能体配置
        self.max_loop = 30
        self.inner_max_loop = 5
        
        # 加载配置文件（如果存在）
        if config_file:
            self._load_config(config_file)
    
    def _load_config(self, config_file):
        """加载配置文件
        
        Args:
            config_file: 配置文件路径
        """
        try:
            import json
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 更新配置（字段名与JSON配置文件保持一致）
            if 'api_key_name' in config:
                self.api_key_name = config['api_key_name']
            if 'base_url' in config:
                self.base_url = config['base_url']
            if 'model_id' in config:
                self.model_id = config['model_id']
            if 'fallback_model_id' in config:
                self.fallback_model_id = config['fallback_model_id']
            if 'temperature' in config:
                self.temperature = config['temperature']
            if 'timeout' in config:
                self.timeout = config['timeout']
            if 'max_retries' in config:
                self.max_retries = config['max_retries']
            if 'base_delay' in config:
                self.base_delay = config['base_delay']
            if 'stream' in config:
                self.stream = config['stream']
            if 'max_loop' in config:
                self.max_loop = config['max_loop']
            if 'inner_max_loop' in config:
                self.inner_max_loop = config['inner_max_loop']
        except Exception as e:
            print(f"[警告] 加载配置文件失败 {config_file}: {e}", file=sys.stderr)

    def get_api_key(self):
        """获取API密钥，优先从环境变量读取"""
        api_key = os.getenv(self.api_key_name)
        if not api_key:
            raise ValueError(f"环境变量 '{self.api_key_name}' 未设置，请在运行前设置")
        return api_key

    def llm_call(self, system_prompt: str, user_prompt: str) -> str:
        current_model = self.model_id
        current_timeout = self.timeout

        for attempt in range(1, self.max_retries + 1):
            try:
                client = OpenAI(api_key=self.get_api_key(), base_url=self.base_url)
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
                start = time.time()
                response = client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    stream=self.stream,
                    temperature=self.temperature,
                    timeout=current_timeout
                )
                full = ""
                if self.stream:
                    for chunk in response:
                        if c := chunk.choices[0].delta.content:
                            print(c, end="", flush=True)
                            full += c
                    print()
                else:
                    full = response.choices[0].message.content
                    usage = response.usage
                    print(f"[Token] 输入: {usage.prompt_tokens}, 输出: {usage.completion_tokens}, 总计: {usage.total_tokens}")
                print(f"[LLM] {current_model} 耗时: {time.time() - start:.1f}s", file=sys.stderr)
                return full.strip()

            except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as e:
                delay = (self.base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
                print(f"⏳ 临时故障（{type(e).__name__}），第 {attempt} 次重试，等待 {delay:.0f}s...", file=sys.stderr)
                time.sleep(delay)

                if isinstance(e, APITimeoutError):
                    current_timeout = int(current_timeout * 1.5)
                    print(f"  超时阈值已调整至 {current_timeout}s", file=sys.stderr)

                if attempt >= 2 and current_model == self.model_id:
                    print(f"  降级至备用模型 {self.fallback_model_id}", file=sys.stderr)
                    current_model = self.fallback_model_id

                continue

            except (AuthenticationError, BadRequestError) as e:
                raise LLMError(f"请求错误（{type(e).__name__}），无需重试")

            except Exception as e:
                raise LLMError(f"请求错误（{type(e).__name__}），无需重试")

        raise LLMError("全部重试失败")

    async def async_llm_call(
        self,
        system_prompt: str,
        user_prompt: str,
        event_bus: "EventBus" = None,
        agent_name: str = "unknown",
        step: int = None,
    ) -> str:
        """异步流式 LLM 调用（通过 EventBus 推送 Token）

        与同步 llm_call 保持相同的参数语义，底层使用 openai.AsyncClient 流式调用。
        """
        from ..llm.async_llm import async_llm_call as _async_call
        return await _async_call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            event_bus=event_bus,
            agent_name=agent_name,
            step=step,
            api_key_name=self.api_key_name,
            model_id=self.model_id,
            fallback_model_id=self.fallback_model_id,
            base_url=self.base_url,
            max_retries=self.max_retries,
            temperature=self.temperature,
            timeout=self.timeout,
            base_delay=self.base_delay,
        )
