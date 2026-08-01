#!/usr/bin/env python3
"""
异步 LLM 调用模块

基于 openai.AsyncClient 实现异步流式调用，每个 token 通过 EventBus 回调。
支持自动重试、模型降级、Token 用量统计。
"""
import asyncio
import json
import os
import sys
import time
import random
from typing import Optional, Dict, Tuple
from openai import AsyncClient, APITimeoutError, APIConnectionError, RateLimitError, InternalServerError
from openai import AuthenticationError, BadRequestError

from ..agents.config import LLMError
from ..utils.event_bus import EventBus, get_event_bus
from ..utils.stream_parser import KeyPathTracker


async def async_llm_call(
    system_prompt: str,
    user_prompt: str,
    event_bus: EventBus = None,
    agent_name: str = "unknown",
    step: int = None,
    stream_event_map: Dict[Tuple[str, ...], str] = None,
    api_key_name: str = "DeepSeekKey",
    model_id: str = "deepseek-v4-pro",
    fallback_model_id: str = "deepseek-v4-flash",
    base_url: str = "https://api.deepseek.com",
    max_retries: int = 3,
    temperature: float = 0.7,
    timeout: int = 180,
    base_delay: int = 1,
) -> str:
    """异步流式 LLM 调用

    通过 event_bus 实时推送每个 token（llm.token 事件），
    支持重试和自动降级到备用模型。

    Args:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        event_bus: EventBus 实例（若不传则自动从上下文获取）
        agent_name: 当前 Agent 名称（用于事件标记）
        step: 当前步骤编号
        api_key_name: 环境变量中的 API Key 名称
        model_id: 主模型 ID
        fallback_model_id: 备用模型 ID
        base_url: API 基础地址
        max_retries: 最大重试次数
        temperature: 温度参数
        timeout: 超时时间（秒）
        base_delay: 重试基础延迟（指数退避）

    Returns:
        完整响应文本

    Raises:
        LLMError: 所有重试均失败或遇到不可恢复的错误
    """
    api_key = os.getenv(api_key_name)
    if not api_key:
        raise LLMError(f"环境变量 '{api_key_name}' 未设置，请在运行前设置")

    eb = event_bus or get_event_bus()
    current_model = model_id
    current_timeout = timeout

    for attempt in range(1, max_retries + 1):
        try:
            client = AsyncClient(api_key=api_key, base_url=base_url)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            if eb:
                await eb.emit_llm_start(agent_name, current_model, step=step)

            start = time.time()
            response = await client.chat.completions.create(
                model=current_model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                temperature=temperature,
                timeout=current_timeout
            )

            full_text = ""
            usage_info = {}
            tracker = KeyPathTracker() if stream_event_map else None
            async for chunk in response:
                if eb and eb._closed:
                    return full_text.strip()

                if not chunk.choices and not chunk.usage:
                    continue

                if chunk.usage:
                    usage_info = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }

                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full_text += text
                    if eb:
                        await eb.emit_llm_token(agent_name, current_model, text, step=step)
                        if tracker and stream_event_map:
                            for key_path, ch in tracker.feed(text):
                                event_type = stream_event_map.get(key_path)
                                if event_type:
                                    await eb.put(event_type, step=step, agent=agent_name, data={"text": ch})

            duration = time.time() - start

            if eb:
                await eb.emit_llm_done(agent_name, current_model, full_text, usage_info, step=step)

            if eb and stream_event_map:
                outline_key_paths = [kp for kp, evt in stream_event_map.items() if evt == "agent.outline.stream"]
                if outline_key_paths and full_text.strip():
                    try:
                        parsed = json.loads(full_text.strip())
                        for kp in outline_key_paths:
                            val = parsed
                            for key in kp:
                                if isinstance(val, dict) and key in val:
                                    val = val[key]
                                else:
                                    val = None
                                    break
                            if val is not None:
                                wrapped = {kp[-1] if len(kp) == 1 else kp[0]: val}
                                outline_json = json.dumps(wrapped, ensure_ascii=False, indent=2)
                                await eb.put("agent.outline.stream", step=step, agent=agent_name, data={"text": outline_json})
                    except json.JSONDecodeError:
                        pass

            print(f"[LLM] {current_model} 耗时: {duration:.1f}s", file=sys.stderr)
            if usage_info:
                print(f"[Token] 输入: {usage_info.get('prompt_tokens')}, 输出: {usage_info.get('completion_tokens')}, 总计: {usage_info.get('total_tokens')}", file=sys.stderr)

            return full_text.strip()

        except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as e:
            if eb and eb._closed:
                raise LLMError("Session 已取消")
            delay = (base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
            error_msg = f"临时故障（{type(e).__name__}），第 {attempt} 次重试，等待 {delay:.0f}s..."
            print(f"⏳ {error_msg}", file=sys.stderr)
            if eb:
                await eb.emit_llm_error(agent_name, current_model, str(e), attempt - 1, step=step)
            await asyncio.sleep(delay)

            if isinstance(e, APITimeoutError):
                current_timeout = int(current_timeout * 1.5)
                print(f"  超时阈值已调整至 {current_timeout}s", file=sys.stderr)

            if attempt >= 2 and current_model == model_id:
                print(f"  降级至备用模型 {fallback_model_id}", file=sys.stderr)
                current_model = fallback_model_id

            continue

        except (AuthenticationError, BadRequestError) as e:
            error_msg = f"请求错误（{type(e).__name__}），无需重试"
            print(f"❌ {error_msg}", file=sys.stderr)
            if eb:
                await eb.emit_llm_error(agent_name, current_model, str(e), attempt - 1, step=step)
            raise LLMError(error_msg)

        except Exception as e:
            error_msg = f"请求错误（{type(e).__name__}）：{str(e)}"
            print(f"❌ {error_msg}", file=sys.stderr)
            if eb:
                await eb.emit_llm_error(agent_name, current_model, str(e), attempt - 1, step=step)
            raise LLMError(error_msg)

    raise LLMError("全部重试失败")


def sync_llm_call_with_events(
    system_prompt: str,
    user_prompt: str,
    event_bus: EventBus = None,
    agent_name: str = "unknown",
    step: int = None,
    stream_event_map: Dict[Tuple[str, ...], str] = None,
    api_key_name: str = "DeepSeekKey",
    model_id: str = "deepseek-v4-pro",
    fallback_model_id: str = "deepseek-v4-flash",
    base_url: str = "https://api.deepseek.com",
    max_retries: int = 3,
    temperature: float = 0.7,
    timeout: int = 180,
    base_delay: int = 1,
) -> str:
    """同步 LLM 调用 + 流式事件推送

    使用同步 OpenAI 客户端 + EventBus.put_sync() 推送到主事件循环。
    不创建新事件循环，避免跨循环 Queue/Lock 冲突。
    """
    from openai import OpenAI
    from openai import APITimeoutError as SyncAPITimeoutError
    from openai import APIConnectionError as SyncAPIConnectionError
    from openai import RateLimitError as SyncRateLimitError
    from openai import InternalServerError as SyncInternalServerError
    from openai import AuthenticationError as SyncAuthenticationError

    if event_bus is None:
        eb = get_event_bus()
    else:
        eb = event_bus
    if eb and eb._closed:
        return ""

    api_key = os.getenv(api_key_name)
    if not api_key:
        raise LLMError(f"环境变量 '{api_key_name}' 未设置，请在运行前设置")

    current_model = model_id
    current_timeout = timeout
    tracker = KeyPathTracker() if stream_event_map else None

    for attempt in range(1, max_retries + 1):
        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            if eb:
                eb.put_sync("llm.start", step=step, agent=agent_name, data={"model": current_model})

            start = time.time()
            response = client.chat.completions.create(
                model=current_model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                temperature=temperature,
                timeout=current_timeout,
            )

            full_text = ""
            usage_info = {}
            for chunk in response:
                if eb and eb._closed:
                    return full_text.strip()

                if not chunk.choices and not chunk.usage:
                    continue

                if chunk.usage:
                    usage_info = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }

                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full_text += text
                    if eb:
                        eb.put_sync("llm.token", step=step, agent=agent_name, data={"text": text, "model": current_model})
                        if tracker and stream_event_map:
                            for key_path, ch in tracker.feed(text):
                                event_type = stream_event_map.get(key_path)
                                if event_type:
                                    eb.put_sync(event_type, step=step, agent=agent_name, data={"text": ch})

            duration = time.time() - start

            if eb:
                eb.put_sync("llm.done", step=step, agent=agent_name, data={
                    "model": current_model, "usage": usage_info, "duration": duration
                })

            # 若 stream_event_map 中有 agent.outline.stream 条目，尝试从完整响应中提取并发射
            if eb and stream_event_map:
                outline_key_paths = [kp for kp, evt in stream_event_map.items() if evt == "agent.outline.stream"]
                if outline_key_paths and full_text.strip():
                    try:
                        parsed = json.loads(full_text.strip())
                        for kp in outline_key_paths:
                            val = parsed
                            for key in kp:
                                if isinstance(val, dict) and key in val:
                                    val = val[key]
                                else:
                                    val = None
                                    break
                            if val is not None:
                                # 包装回 {"标准化模板文本": val} 格式，让前端一致地通过 parsed['标准化模板文本'] 访问
                                wrapped = {kp[-1] if len(kp) == 1 else kp[0]: val}
                                outline_json = json.dumps(wrapped, ensure_ascii=False, indent=2)
                                eb.put_sync("agent.outline.stream", step=step, agent=agent_name, data={"text": outline_json})
                    except json.JSONDecodeError:
                        pass

            print(f"[LLM] {current_model} 耗时: {duration:.1f}s", file=sys.stderr)
            if usage_info:
                print(f"[Token] 输入: {usage_info.get('prompt_tokens')}, 输出: {usage_info.get('completion_tokens')}, 总计: {usage_info.get('total_tokens')}", file=sys.stderr)

            return full_text.strip()

        except (SyncAPITimeoutError, SyncAPIConnectionError, SyncRateLimitError, SyncInternalServerError) as e:
            if eb and eb._closed:
                raise LLMError("Session 已取消")
            delay = (base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
            error_msg = f"临时故障（{type(e).__name__}），第 {attempt} 次重试，等待 {delay:.0f}s..."
            print(f"⏳ {error_msg}", file=sys.stderr)
            if eb:
                eb.put_sync("llm.error", step=step, agent=agent_name, data={"error": str(e), "retry": attempt - 1})
            time.sleep(delay)

            if isinstance(e, SyncAPITimeoutError):
                current_timeout = int(current_timeout * 1.5)
                print(f"  超时阈值已调整至 {current_timeout}s", file=sys.stderr)

            if attempt >= 2 and current_model == model_id:
                print(f"  降级至备用模型 {fallback_model_id}", file=sys.stderr)
                current_model = fallback_model_id

            continue

        except (SyncAuthenticationError, BadRequestError) as e:
            error_msg = f"请求错误（{type(e).__name__}），无需重试"
            print(f"❌ {error_msg}", file=sys.stderr)
            if eb:
                eb.put_sync("llm.error", step=step, agent=agent_name, data={"error": str(e), "retry": attempt - 1})
            raise LLMError(error_msg)

        except Exception as e:
            error_msg = f"请求错误（{type(e).__name__}）：{str(e)}"
            print(f"❌ {error_msg}", file=sys.stderr)
            if eb:
                eb.put_sync("llm.error", step=step, agent=agent_name, data={"error": str(e), "retry": attempt - 1})
            raise LLMError(error_msg)

    raise LLMError("全部重试失败")
