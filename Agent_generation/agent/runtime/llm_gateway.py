"""LLM 网关 —— 全系统唯一的大模型出口

所有 Agent / 评测 Judge 的模型调用都经过这里，统一实现工程级鲁棒性：

1. Key 解析：配置名 → 别名（DeepSeekKey / DEEPSEEK_API_KEY），缺失时给出明确错误
2. 舱壁（Bulkhead）：按 base_url 限制并发，防止并行 Agent 打爆供应商限流
3. 熔断（Circuit Breaker）：按 model 维度，连续失败快速失败并切换备用模型
4. 重试：指数退避 + 抖动，识别 429 的 Retry-After；参数/鉴权错误不重试
5. 降级：主模型失败 N 次或熔断打开 → 备用模型（fallback_model_id）
6. 超时：连接超时 + 流式空闲超时（每个 chunk 之间）+ 单次尝试总时长上限
7. 取消：取消令牌触发时立即关闭 HTTP 流，正在阻塞的线程马上退出（不留僵尸请求）
8. 预算：单次运行的调用次数 / Token 上限
9. 可观测：llm.start / reasoning / token / retry / fallback / done / error 事件 + 指标
10. 故障注入：测试可注入 mock handler，模拟超时、限流、空响应、坏 JSON 等
"""
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .breaker import get_breaker
from .context import current
from .errors import (BudgetExceeded, CircuitOpenError, LLMError, LLMFatalError, LLMRetryableError,
                     RunCancelled, StructuredOutputError)

KEY_ALIASES = ["DeepSeekKey", "DEEPSEEK_API_KEY", "DEEPSEEK_KEY"]


@dataclass
class LLMSettings:
    api_key_name: str = "DeepSeekKey"
    base_url: str = "https://api.deepseek.com"
    model_id: str = "deepseek-flash"
    fallback_model_id: Optional[str] = "deepseek-flash"
    temperature: float = 0.4
    timeout: float = 200.0
    max_retries: int = 3
    base_delay: float = 1.0
    idle_timeout: float = 90.0
    connect_timeout: float = 15.0
    fallback_after: int = 2
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    seed: Optional[int] = None

    @classmethod
    def from_config(cls, cfg) -> "LLMSettings":
        s = cls()
        for f in ("api_key_name", "base_url", "model_id", "fallback_model_id", "temperature", "timeout",
                  "max_retries", "base_delay", "idle_timeout", "connect_timeout", "fallback_after",
                  "max_tokens", "thinking", "seed"):
            if hasattr(cfg, f) and getattr(cfg, f) is not None:
                setattr(s, f, getattr(cfg, f))
        if not s.fallback_model_id:
            s.fallback_model_id = s.model_id
        return s

    def __post_init__(self):
        enforce_model_policy(self)


@dataclass
class LLMResult:
    content: str
    reasoning: str = ""
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    attempts: int = 1
    fallback_used: bool = False
    duration: float = 0.0


# 模型白名单：所有调用（Agent 与评测 Judge / 基线）只允许使用 ALLOWED_MODELS；
# 配置或代码中出现其它模型名一律改写为 DEFAULT_MODEL（防止误用高价模型）。可用环境变量 LLM_ALLOWED_MODELS 覆盖
DEFAULT_MODEL = os.getenv("LLM_DEFAULT_MODEL", "deepseek-flash")
ALLOWED_MODELS = {m.strip() for m in os.getenv("LLM_ALLOWED_MODELS", DEFAULT_MODEL).split(",") if m.strip()}


def enforce_model_policy(s: "LLMSettings") -> "LLMSettings":
    if s.model_id not in ALLOWED_MODELS:
        s.model_id = DEFAULT_MODEL
    if s.fallback_model_id and s.fallback_model_id not in ALLOWED_MODELS:
        s.fallback_model_id = DEFAULT_MODEL
    return s


def resolve_api_key(name: Optional[str]) -> str:
    for key in [name] + KEY_ALIASES:
        if key and os.getenv(key):
            return os.getenv(key)
    raise LLMFatalError(f"未找到 LLM API Key：请设置环境变量 {name or KEY_ALIASES[0]}（或 {' / '.join(KEY_ALIASES)}）")


# ---------------------------------------------------------------- 舱壁
_bulkheads: Dict[str, threading.BoundedSemaphore] = {}
_bulkhead_lock = threading.Lock()
MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "8"))


def _bulkhead(base_url: str) -> threading.BoundedSemaphore:
    with _bulkhead_lock:
        sem = _bulkheads.get(base_url)
        if sem is None:
            sem = threading.BoundedSemaphore(MAX_CONCURRENCY)
            _bulkheads[base_url] = sem
        return sem


def _acquire(sem: threading.BoundedSemaphore, cancel):
    while not sem.acquire(timeout=0.5):
        if cancel is not None:
            cancel.raise_if_cancelled()


# ---------------------------------------------------------------- 客户端复用
_clients: Dict[Tuple[str, str, float, float], object] = {}
_client_lock = threading.Lock()


def _client(api_key: str, base_url: str, connect_timeout: float, idle_timeout: float):
    import httpx
    from openai import OpenAI
    k = (api_key[-6:], base_url, connect_timeout, idle_timeout)
    with _client_lock:
        c = _clients.get(k)
        if c is None:
            c = OpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                       timeout=httpx.Timeout(connect=connect_timeout, read=idle_timeout, write=30.0, pool=30.0))
            _clients[k] = c
        return c


# ---------------------------------------------------------------- 故障注入 / Mock
MockHandler = Callable[[dict], object]
_mock_handler: Optional[MockHandler] = None


def set_mock_handler(handler: Optional[MockHandler]):
    """测试用：handler(request) → str | LLMResult | 抛异常。request 含 model/system/user/attempt"""
    global _mock_handler
    _mock_handler = handler


def get_mock_handler() -> Optional[MockHandler]:
    return _mock_handler


# ---------------------------------------------------------------- 异常分类
def _classify(exc: BaseException) -> Tuple[bool, Optional[float]]:
    """返回 (是否可重试, 建议等待秒数)"""
    try:
        import httpx
        import openai
    except Exception:  # pragma: no cover
        return True, None
    if isinstance(exc, (LLMRetryableError,)):
        return True, None
    if isinstance(exc, LLMFatalError):
        return False, None
    if isinstance(exc, openai.RateLimitError):
        retry_after = None
        try:
            retry_after = float(exc.response.headers.get("retry-after"))
        except Exception:
            pass
        return True, retry_after
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)):
        return True, None
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError, openai.BadRequestError,
                        openai.NotFoundError, openai.UnprocessableEntityError)):
        return False, None
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500 or exc.status_code in (408, 409, 429), None
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, ConnectionError, TimeoutError)):
        return True, None
    return True, None


# ---------------------------------------------------------------- 主入口
def _emit(ctx, event_type: str, agent: str, step, data: dict):
    if ctx is not None:
        ctx.emit(event_type, data, agent=agent, step=step)


def _do_stream(request: dict, settings: LLMSettings, model: str, ctx, agent: str, step,
               stream_event_map, on_token) -> LLMResult:
    """执行一次流式请求（单次尝试）"""
    from ..utils.stream_parser import KeyPathTracker

    cancel = ctx.cancel if ctx else None
    start = time.monotonic()

    if _mock_handler is not None:
        out = _mock_handler({**request, "model": model})
        if isinstance(out, LLMResult):
            out.model = out.model or model
            res = out
        else:
            res = LLMResult(content=str(out), model=model)
        tracker = KeyPathTracker() if stream_event_map else None
        for piece in [res.content[i:i + 16] for i in range(0, len(res.content), 16)]:
            if cancel is not None:
                cancel.raise_if_cancelled()
            _route_token(ctx, agent, step, piece, model, tracker, stream_event_map, on_token)
        res.duration = time.monotonic() - start
        if not res.content.strip():
            raise LLMRetryableError("模型返回空内容")
        return res

    user_provider = (ctx.tags or {}).get("provider") if ctx is not None else None
    if user_provider is not None:
        return _do_stream_provider(request, settings, user_provider, ctx, agent, step, stream_event_map, on_token)

    api_key = resolve_api_key(settings.api_key_name)
    client = _client(api_key, settings.base_url, settings.connect_timeout, settings.idle_timeout)
    kwargs = dict(model=model, messages=request["messages"], stream=True,
                  stream_options={"include_usage": True},
                  temperature=request.get("temperature", settings.temperature))
    if request.get("max_tokens") or settings.max_tokens:
        kwargs["max_tokens"] = request.get("max_tokens") or settings.max_tokens
    if settings.seed is not None:
        kwargs["seed"] = settings.seed
    extra_body = dict(request.get("extra_body") or {})
    if request.get("json_mode"):
        kwargs["response_format"] = {"type": "json_object"}
    thinking = request.get("thinking", settings.thinking)
    if thinking is not None:
        extra_body["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if extra_body:
        kwargs["extra_body"] = extra_body

    stream = client.chat.completions.create(**kwargs)
    closed_by_cancel = {"v": False}

    def _close():
        closed_by_cancel["v"] = True
        try:
            stream.close()
        except Exception:
            pass

    unregister = cancel.add_callback(_close) if cancel is not None else (lambda: None)
    content, reasoning, usage = [], [], {}
    tracker = KeyPathTracker() if stream_event_map else None
    try:
        for chunk in stream:
            if cancel is not None and cancel.cancelled:
                raise RunCancelled(cancel.reason or "cancelled")
            if time.monotonic() - start > settings.timeout:
                raise LLMRetryableError(f"单次请求超过 {settings.timeout}s 总时长上限")
            if getattr(chunk, "usage", None):
                u = chunk.usage
                usage = {"prompt_tokens": u.prompt_tokens or 0, "completion_tokens": u.completion_tokens or 0,
                         "total_tokens": u.total_tokens or 0}
                details = getattr(u, "completion_tokens_details", None)
                if details is not None and getattr(details, "reasoning_tokens", None):
                    usage["reasoning_tokens"] = details.reasoning_tokens
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            rc = getattr(delta, "reasoning_content", None)
            if rc is None and getattr(delta, "model_extra", None):
                rc = delta.model_extra.get("reasoning_content")
            if rc:
                reasoning.append(rc)
                _emit(ctx, "agent.reasoning.stream", agent, step, {"text": rc, "model": model})
            if delta.content:
                content.append(delta.content)
                _route_token(ctx, agent, step, delta.content, model, tracker, stream_event_map, on_token)
    except RunCancelled:
        raise
    except BaseException as e:
        if closed_by_cancel["v"] or (cancel is not None and cancel.cancelled):
            raise RunCancelled(cancel.reason if cancel else "cancelled") from e
        raise
    finally:
        unregister()
        try:
            stream.close()
        except Exception:
            pass
    text = "".join(content)
    if not text.strip():
        raise LLMRetryableError("模型返回空内容")
    return LLMResult(content=text, reasoning="".join(reasoning), model=model, usage=usage,
                     duration=time.monotonic() - start)


def _do_stream_provider(request: dict, settings: "LLMSettings", provider, ctx, agent, step, stream_event_map,
                        on_token) -> "LLMResult":
    """用户自有 API（多协议适配）：OpenAI Chat / Responses / Anthropic Messages / Gemini"""
    from ..utils.stream_parser import KeyPathTracker
    from .providers import stream_chat
    cancel = ctx.cancel if ctx else None
    start = time.monotonic()
    content, reasoning, usage = [], [], {}
    tracker = KeyPathTracker() if stream_event_map else None
    for kind, val in stream_chat(provider, request["messages"], temperature=request.get("temperature", settings.temperature),
                                 max_tokens=request.get("max_tokens"), json_mode=request.get("json_mode", False),
                                 connect_timeout=settings.connect_timeout, idle_timeout=settings.idle_timeout,
                                 cancel_cb=cancel.add_callback if cancel is not None else None):
        if cancel is not None and cancel.cancelled:
            raise RunCancelled(cancel.reason or "cancelled")
        if time.monotonic() - start > settings.timeout:
            raise LLMRetryableError(f"单次请求超过 {settings.timeout}s 总时长上限")
        if kind == "content":
            content.append(val)
            _route_token(ctx, agent, step, val, provider.model, tracker, stream_event_map, on_token)
        elif kind == "reasoning":
            reasoning.append(val)
            _emit(ctx, "agent.reasoning.stream", agent, step, {"text": val, "model": provider.model})
        elif kind == "usage":
            usage = val
    text = "".join(content)
    if not text.strip():
        raise LLMRetryableError("模型返回空内容")
    return LLMResult(content=text, reasoning="".join(reasoning), model=provider.model, usage=usage,
                     duration=time.monotonic() - start)


def _route_token(ctx, agent, step, text, model, tracker, stream_event_map, on_token):
    _emit(ctx, "llm.token", agent, step, {"text": text, "model": model})
    if tracker is not None and stream_event_map:
        buf: Dict[str, List[str]] = {}
        for key_path, ch in tracker.feed(text):
            evt = stream_event_map.get(key_path)
            if evt:
                buf.setdefault(evt, []).append(ch)
        for evt, chars in buf.items():
            _emit(ctx, evt, agent, step, {"text": "".join(chars)})
    if on_token is not None:
        try:
            on_token(text)
        except Exception:
            pass


def call_llm(system_prompt: str, user_prompt: str, settings: Optional[LLMSettings] = None, *,
             agent: Optional[str] = None, step: Optional[int] = None,
             stream_event_map: Optional[dict] = None, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, json_mode: bool = False, thinking: Optional[bool] = None,
             messages: Optional[List[dict]] = None, on_token: Optional[Callable[[str], None]] = None,
             return_result: bool = False):
    """同步调用 LLM（内部始终流式），返回文本（或 LLMResult）

    Raises:
        RunCancelled: 被取消
        BudgetExceeded: 超预算
        LLMError: 重试与降级全部失败
    """
    ctx = current()
    user_provider = (ctx.tags or {}).get("provider") if ctx is not None else None
    settings = settings or LLMSettings()
    if user_provider is not None:
        # 用户自有 API：模型由用户配置决定，不受平台模型白名单限制；主备均为用户模型
        import copy as _copy
        settings = _copy.copy(settings)
        settings.model_id = settings.fallback_model_id = user_provider.model
        settings.base_url = user_provider.base_url
    else:
        settings = enforce_model_policy(settings)
    agent = agent or (ctx.agent if ctx else "unknown")
    step = step if step is not None else (ctx.step if ctx else None)
    if stream_event_map is None and ctx is not None:
        stream_event_map = ctx.stream_event_map
    cancel = ctx.cancel if ctx else None
    metrics = ctx.metrics if ctx else None

    if cancel is not None:
        cancel.raise_if_cancelled()
    if ctx is not None and ctx.budget is not None:
        ctx.budget.charge_llm_call()

    request = {
        "messages": messages or [{"role": "system", "content": system_prompt},
                                 {"role": "user", "content": user_prompt}],
        "system": system_prompt, "user": user_prompt,
        "temperature": settings.temperature if temperature is None else temperature,
        "max_tokens": max_tokens, "json_mode": json_mode, "thinking": thinking, "agent": agent,
    }
    models = [settings.model_id]
    if settings.fallback_model_id and settings.fallback_model_id != settings.model_id:
        models.append(settings.fallback_model_id)

    sem = _bulkhead(settings.base_url)
    attempts_total = max(1, int(settings.max_retries))
    last_exc: Optional[BaseException] = None
    model_idx = 0
    primary_failures = 0
    t0 = time.monotonic()

    for attempt in range(1, attempts_total + 1):
        if cancel is not None:
            cancel.raise_if_cancelled()
        model = models[model_idx]
        breaker = get_breaker(f"{settings.base_url}|{model}")
        if not breaker.allow():
            if model_idx + 1 < len(models):
                model_idx += 1
                _emit(ctx, "llm.fallback", agent, step, {"from": model, "to": models[model_idx],
                                                         "reason": "circuit_open"})
                if metrics:
                    metrics.note("fallback", agent=agent, model=model, reason="circuit_open")
                model = models[model_idx]
                breaker = get_breaker(f"{settings.base_url}|{model}")
            if not breaker.allow():
                last_exc = CircuitOpenError(f"模型 {model} 熔断中")
                if metrics:
                    metrics.note("circuit_open", agent=agent, model=model)
                _sleep_backoff(settings, attempt, None, cancel)
                continue

        request["attempt"] = attempt
        _emit(ctx, "llm.start", agent, step, {"model": model, "attempt": attempt})
        try:
            _acquire(sem, cancel)
            try:
                res = _do_stream(request, settings, model, ctx, agent, step, stream_event_map, on_token)
            finally:
                sem.release()
        except (RunCancelled, BudgetExceeded):
            raise
        except Exception as e:  # noqa: BLE001
            retryable, wait_hint = _classify(e)
            last_exc = e
            if not retryable:
                _emit(ctx, "llm.error", agent, step, {"model": model, "error": f"{type(e).__name__}: {e}",
                                                      "fatal": True})
                if metrics:
                    metrics.note("llm_fatal", agent=agent, model=model, error=str(e)[:300])
                raise LLMFatalError(f"{type(e).__name__}: {e}") from e
            breaker.record_failure()
            if metrics:
                metrics.note("retry", agent=agent, model=model, attempt=attempt, error=f"{type(e).__name__}: {e}"[:300])
            _emit(ctx, "llm.retry", agent, step, {"model": model, "attempt": attempt,
                                                  "error": f"{type(e).__name__}: {str(e)[:200]}"})
            if model_idx == 0:
                primary_failures += 1
                if primary_failures >= settings.fallback_after and len(models) > 1:
                    model_idx = 1
                    _emit(ctx, "llm.fallback", agent, step, {"from": model, "to": models[1],
                                                             "reason": "consecutive_failures"})
                    if metrics:
                        metrics.note("fallback", agent=agent, model=model, reason="consecutive_failures")
            if attempt < attempts_total:
                _sleep_backoff(settings, attempt, wait_hint, cancel)
            continue
        breaker.record_success()
        res.attempts = attempt
        res.fallback_used = model_idx > 0
        total_tokens = res.usage.get("total_tokens", 0)
        if ctx is not None and ctx.budget is not None:
            ctx.budget.charge_tokens(total_tokens)
        if metrics:
            metrics.record(agent, model, calls=1, prompt_tokens=res.usage.get("prompt_tokens", 0),
                           completion_tokens=res.usage.get("completion_tokens", 0), total_tokens=total_tokens,
                           duration=res.duration, retries=attempt - 1)
        _emit(ctx, "llm.done", agent, step, {"model": model, "usage": res.usage, "duration": round(res.duration, 2),
                                             "attempts": attempt, "fallback": res.fallback_used})
        if stream_event_map:
            _emit_outline_post_hook(ctx, agent, step, stream_event_map, res.content)
        return res if return_result else res.content.strip()

    _emit(ctx, "llm.error", agent, step, {"error": f"{type(last_exc).__name__}: {last_exc}", "fatal": False,
                                          "elapsed": round(time.monotonic() - t0, 1)})
    if metrics:
        metrics.note("llm_exhausted", agent=agent, error=str(last_exc)[:300])
    raise LLMError(f"LLM 调用在 {attempts_total} 次尝试后失败：{type(last_exc).__name__}: {last_exc}")


def _sleep_backoff(settings: LLMSettings, attempt: int, hint: Optional[float], cancel):
    delay = hint if hint is not None else settings.base_delay * (2 ** (attempt - 1))
    delay = min(delay, 30.0) * (0.5 + random.random() / 2)
    if cancel is not None:
        cancel.sleep(delay)
    else:
        time.sleep(delay)


def _emit_outline_post_hook(ctx, agent, step, stream_event_map, full_text):
    """兼容旧行为：若字段被映射为 agent.outline.stream，完成后整体推送一次结构化大纲"""
    paths = [kp for kp, evt in stream_event_map.items() if evt == "agent.outline.stream"]
    if not paths or ctx is None:
        return
    from ..utils.response_parser import ResponseParser
    parsed = ResponseParser.parse_agent_response(full_text)
    if "error" in parsed:
        return
    for kp in paths:
        val = parsed
        for key in kp:
            val = val.get(key) if isinstance(val, dict) else None
        if val is not None:
            _emit(ctx, "agent.outline.stream", agent, step,
                  {"text": json.dumps({kp[-1]: val}, ensure_ascii=False)})


# ---------------------------------------------------------------- 结构化输出
def call_llm_json(system_prompt: str, user_prompt: str, settings: Optional[LLMSettings] = None, *,
                  validator: Optional[Callable[[dict], Optional[str]]] = None, repair_attempts: int = 2,
                  json_schema: Optional[dict] = None, **kwargs) -> dict:
    """调用 LLM 并强制得到合法 JSON。

    解析失败或校验失败时，把错误反馈给模型自我修复（repair loop），最多 repair_attempts 次。
    validator(data) 返回 None 表示通过，否则返回错误描述。
    """
    from ..utils.response_parser import ResponseParser
    errors = []
    prompt = user_prompt
    raw = ""
    for i in range(repair_attempts + 1):
        raw = call_llm(system_prompt, prompt, settings, **kwargs)
        data = ResponseParser.parse_agent_response(raw)
        err = data.get("error") if isinstance(data, dict) and set(data.keys()) == {"error"} else None
        if err is None and json_schema is not None:
            err = validate_schema(data, json_schema)
        if err is None and validator is not None:
            try:
                err = validator(data)
            except Exception as e:  # noqa: BLE001
                err = f"校验异常：{e}"
        if err is None:
            return data
        errors.append(err)
        ctx = current()
        if ctx is not None and ctx.metrics is not None:
            ctx.metrics.note("json_repair", agent=kwargs.get("agent") or ctx.agent, error=str(err)[:200])
        prompt = (f"{user_prompt}\n\n【上一次输出不合规，必须修正】\n错误：{err}\n"
                  f"上一次输出（截断）：{raw[:1500]}\n请只输出修正后的合法 JSON，不要输出其他内容。")
    raise StructuredOutputError(f"结构化输出修复 {repair_attempts} 次后仍失败：{errors[-1] if errors else ''}")


def validate_schema(data, schema: dict) -> Optional[str]:
    try:
        import jsonschema
    except Exception:  # pragma: no cover
        return None
    try:
        jsonschema.validate(data, schema)
        return None
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path)
        return f"字段 {path or '<root>'} 不符合 Schema：{e.message[:300]}"
