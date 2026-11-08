"""多厂商 LLM 协议适配层

支持的 API 格式（format）：
- openai_chat       OpenAI Chat Completions（/v1/chat/completions）——DeepSeek、通义、Kimi、智谱、豆包、硅基流动、OpenRouter、Ollama 等兼容
- openai_responses  OpenAI Responses API（/v1/responses）
- anthropic         Anthropic Messages（/v1/messages）
- gemini            Google Gemini generateContent（流式 streamGenerateContent?alt=sse）

统一接口：stream_chat(provider, messages, ...) → 逐块产出 ("reasoning"|"content"|"usage", payload)
所有实现基于 httpx 原生 SSE，不依赖厂商 SDK；错误统一映射为 LLMRetryableError / LLMFatalError，供网关重试、熔断。
"""
import json
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import httpx

from .errors import LLMFatalError, LLMRetryableError

FORMATS = ("openai_chat", "openai_responses", "anthropic", "gemini")

# 预置厂商（类 TRAE：选择厂商后自动填充 Base URL 与 API 格式，用户只需填 Key 与模型）
PRESETS: List[Dict] = [
    {"id": "deepseek", "name": "DeepSeek", "format": "openai_chat", "base_url": "https://api.deepseek.com",
     "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "openai", "name": "OpenAI", "format": "openai_responses", "base_url": "https://api.openai.com/v1",
     "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o4-mini"]},
    {"id": "anthropic", "name": "Anthropic", "format": "anthropic", "base_url": "https://api.anthropic.com",
     "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-3-5-haiku-latest"]},
    {"id": "gemini", "name": "Google Gemini", "format": "gemini", "base_url": "https://generativelanguage.googleapis.com",
     "models": ["gemini-2.5-pro", "gemini-2.5-flash"]},
    {"id": "qwen", "name": "阿里云百炼（通义千问）", "format": "openai_chat",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": ["qwen-max", "qwen-plus", "qwen-turbo"]},
    {"id": "moonshot", "name": "Moonshot（Kimi）", "format": "openai_chat", "base_url": "https://api.moonshot.cn/v1",
     "models": ["kimi-k2-0905-preview", "moonshot-v1-32k"]},
    {"id": "zhipu", "name": "智谱 GLM", "format": "openai_chat", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4.6", "glm-4-plus", "glm-4-flash"]},
    {"id": "doubao", "name": "火山方舟（豆包）", "format": "openai_chat", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "models": []},
    {"id": "siliconflow", "name": "硅基流动", "format": "openai_chat", "base_url": "https://api.siliconflow.cn/v1",
     "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"]},
    {"id": "openrouter", "name": "OpenRouter", "format": "openai_chat", "base_url": "https://openrouter.ai/api/v1",
     "models": ["anthropic/claude-sonnet-4.5", "openai/gpt-4.1", "google/gemini-2.5-pro"]},
    {"id": "ollama", "name": "Ollama（本地）", "format": "openai_chat", "base_url": "http://localhost:11434/v1",
     "models": ["qwen2.5:14b", "llama3.1:8b"]},
    {"id": "custom", "name": "自定义", "format": "openai_chat", "base_url": "", "models": []},
]


@dataclass
class Provider:
    format: str
    base_url: str
    api_key: str
    model: str
    extra_headers: Dict[str, str] = field(default_factory=dict)
    max_output_tokens: int = 8192
    label: str = ""

    def safe_dict(self) -> dict:
        return {"format": self.format, "base_url": self.base_url, "model": self.model, "label": self.label}


def _join(base: str, path: str) -> str:
    base = (base or "").rstrip("/")
    if path.startswith("/v1/") and base.endswith("/v1"):
        path = path[3:]
    return base + path


def _raise_for_status(resp: httpx.Response):
    ctype = resp.headers.get("content-type", "")
    if resp.status_code < 400:
        if "html" in ctype and "event-stream" not in ctype:
            # 常见误配置：Base URL 指向了网页而非 API（如漏了 /v1）
            raise LLMFatalError(f"返回了网页而非 API 响应（HTTP {resp.status_code}），请检查 Base URL 是否正确")
        return
    try:
        body = resp.read().decode("utf-8", "ignore")[:400]
    except Exception:
        body = ""
    msg = f"HTTP {resp.status_code}: {body}"
    if resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500:
        raise LLMRetryableError(msg)
    raise LLMFatalError(msg)


def _sse_lines(resp: httpx.Response) -> Iterator[Tuple[str, str]]:
    """解析 SSE：产出 (event, data)"""
    event = ""
    for line in resp.iter_lines():
        if not line:
            event = ""
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            yield event, line[5:].strip()


def _split_system(messages: List[dict]) -> Tuple[str, List[dict]]:
    sys_parts = [m["content"] for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    return "\n\n".join(sys_parts), rest


# ------------------------------------------------------------ OpenAI Chat Completions
def _openai_chat(p: Provider, messages, temperature, max_tokens, json_mode, client) -> Iterator[Tuple[str, object]]:
    body = {"model": p.model, "messages": messages, "stream": True, "temperature": temperature,
            "stream_options": {"include_usage": True}}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {p.api_key}", "Content-Type": "application/json", **p.extra_headers}
    with client.stream("POST", _join(p.base_url, "/chat/completions"), json=body, headers=headers) as resp:
        if resp.status_code == 400 and json_mode:
            # 部分兼容厂商不支持 response_format：去掉后重试一次
            resp.read()
            body.pop("response_format", None)
            yield from _openai_chat(p, messages, temperature, max_tokens, False, client)
            return
        _raise_for_status(resp)
        for _, data in _sse_lines(resp):
            if data == "[DONE]":
                break
            try:
                ch = json.loads(data)
            except json.JSONDecodeError:
                continue
            if ch.get("usage"):
                u = ch["usage"]
                yield "usage", {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
                                "total_tokens": u.get("total_tokens", 0)}
            for c in ch.get("choices") or []:
                d = c.get("delta") or {}
                if d.get("reasoning_content"):
                    yield "reasoning", d["reasoning_content"]
                if d.get("content"):
                    yield "content", d["content"]


# ------------------------------------------------------------ OpenAI Responses API
def _openai_responses(p: Provider, messages, temperature, max_tokens, json_mode, client):
    system, rest = _split_system(messages)
    body = {"model": p.model, "stream": True,
            "input": [{"role": m["role"], "content": m["content"]} for m in rest]}
    if system:
        body["instructions"] = system
    if max_tokens:
        body["max_output_tokens"] = max_tokens
    if not p.model.startswith(("o1", "o3", "o4", "gpt-5")):
        body["temperature"] = temperature
    if json_mode:
        body["text"] = {"format": {"type": "json_object"}}
    headers = {"Authorization": f"Bearer {p.api_key}", "Content-Type": "application/json", **p.extra_headers}
    with client.stream("POST", _join(p.base_url, "/responses"), json=body, headers=headers) as resp:
        _raise_for_status(resp)
        for event, data in _sse_lines(resp):
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            t = ev.get("type") or event
            if t == "response.output_text.delta":
                yield "content", ev.get("delta", "")
            elif t in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                yield "reasoning", ev.get("delta", "")
            elif t == "response.completed":
                u = (ev.get("response") or {}).get("usage") or {}
                yield "usage", {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                                "total_tokens": u.get("total_tokens", 0)}
            elif t in ("response.failed", "error"):
                err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
                raise LLMRetryableError(f"Responses API 错误：{str(err)[:300]}")


# ------------------------------------------------------------ Anthropic Messages
def _anthropic(p: Provider, messages, temperature, max_tokens, json_mode, client):
    system, rest = _split_system(messages)
    if json_mode:
        system = (system + "\n\n只输出合法 JSON，不要输出任何其他内容。").strip()
    body = {"model": p.model, "stream": True, "max_tokens": max_tokens or p.max_output_tokens, "temperature": temperature,
            "messages": [{"role": "assistant" if m["role"] == "assistant" else "user", "content": m["content"]} for m in rest]}
    if system:
        body["system"] = system
    headers = {"x-api-key": p.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json",
               **p.extra_headers}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    with client.stream("POST", _join(p.base_url, "/v1/messages"), json=body, headers=headers) as resp:
        _raise_for_status(resp)
        for event, data in _sse_lines(resp):
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            t = ev.get("type") or event
            if t == "message_start":
                usage["prompt_tokens"] = ((ev.get("message") or {}).get("usage") or {}).get("input_tokens", 0)
            elif t == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta":
                    yield "content", d.get("text", "")
                elif d.get("type") == "thinking_delta":
                    yield "reasoning", d.get("thinking", "")
            elif t == "message_delta":
                usage["completion_tokens"] = (ev.get("usage") or {}).get("output_tokens", usage["completion_tokens"])
            elif t == "error":
                err = ev.get("error") or {}
                if err.get("type") in ("overloaded_error", "rate_limit_error", "api_error"):
                    raise LLMRetryableError(f"Anthropic：{err}")
                raise LLMFatalError(f"Anthropic：{err}")
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    yield "usage", usage


# ------------------------------------------------------------ Gemini
def _gemini(p: Provider, messages, temperature, max_tokens, json_mode, client):
    system, rest = _split_system(messages)
    body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                         for m in rest],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens or p.max_output_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    url = _join(p.base_url, f"/v1beta/models/{p.model}:streamGenerateContent") + "?alt=sse"
    headers = {"x-goog-api-key": p.api_key, "Content-Type": "application/json", **p.extra_headers}
    usage = {}
    with client.stream("POST", url, json=body, headers=headers) as resp:
        _raise_for_status(resp)
        for _, data in _sse_lines(resp):
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            for cand in ev.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if part.get("text"):
                        yield ("reasoning" if part.get("thought") else "content"), part["text"]
            um = ev.get("usageMetadata")
            if um:
                usage = {"prompt_tokens": um.get("promptTokenCount", 0),
                         "completion_tokens": um.get("candidatesTokenCount", 0),
                         "total_tokens": um.get("totalTokenCount", 0)}
    if usage:
        yield "usage", usage


_IMPL = {"openai_chat": _openai_chat, "openai_responses": _openai_responses, "anthropic": _anthropic, "gemini": _gemini}


def stream_chat(p: Provider, messages: List[dict], temperature: float = 0.4, max_tokens: Optional[int] = None,
                json_mode: bool = False, connect_timeout: float = 15, idle_timeout: float = 120,
                cancel_cb=None) -> Iterator[Tuple[str, object]]:
    """统一流式接口。cancel_cb(close_fn) 用于注册取消回调（取消时关闭连接）"""
    if p.format not in _IMPL:
        raise LLMFatalError(f"不支持的 API 格式：{p.format}")
    if not p.base_url or not p.model:
        raise LLMFatalError("Base URL 或模型未配置")
    client = httpx.Client(timeout=httpx.Timeout(connect=connect_timeout, read=idle_timeout, write=30, pool=30),
                          follow_redirects=True)
    unregister = cancel_cb(client.close) if cancel_cb else (lambda: None)
    try:
        yield from _IMPL[p.format](p, messages, temperature, max_tokens, json_mode, client)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise LLMRetryableError(f"{type(e).__name__}: {e}") from e
    finally:
        unregister()
        client.close()


def test_connection(p: Provider, timeout: float = 30) -> dict:
    """连通性测试：发送极短请求，返回 {ok, latency_ms, reply, error}"""
    import time
    t0 = time.time()
    try:
        out = []
        for kind, val in stream_chat(p, [{"role": "user", "content": "请只回复：OK"}], temperature=0, max_tokens=64,
                                     connect_timeout=10, idle_timeout=timeout):
            if kind == "content":
                out.append(val)
        reply = "".join(out).strip()
        if not reply:
            return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                    "error": "连接成功但未解析到任何模型输出：请检查 API 格式、Base URL（是否需要 /v1）与模型名称"}
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "reply": reply[:60]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "error": str(e)[:400]}
