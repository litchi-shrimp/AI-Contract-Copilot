"""运行时内核：重试 / 降级 / 熔断 / 取消 / 预算 / 结构化输出"""
import threading
import time

import openai
import httpx
import pytest

from agent.runtime import (Budget, BudgetExceeded, CancelToken, LLMError, LLMFatalError, LLMSettings, RunCancelled,
                           call_llm, call_llm_json, new_context, set_mock_handler, use_context)
from agent.runtime.breaker import get_breaker
from agent.runtime.fallback import Strategy, StepFailed, run_with_fallback, run_with_timeout

import agent.runtime.llm_gateway as _gw

# 机制测试使用两个虚拟模型名（主/备），仅在测试中加入白名单；生产白名单只有 deepseek-flash
_gw.ALLOWED_MODELS |= {"primary", "backup"}
S = LLMSettings(model_id="primary", fallback_model_id="backup", max_retries=4, fallback_after=2)


def _timeout_exc():
    return openai.APITimeoutError(request=httpx.Request("POST", "http://x"))


def test_retry_then_success(ctx):
    calls = []

    def h(req):
        calls.append(req["model"])
        if len(calls) == 1:
            raise _timeout_exc()
        return "OK"
    set_mock_handler(h)
    assert call_llm("s", "u", S) == "OK"
    assert calls == ["primary", "primary"]
    assert any(e["type"] == "llm.retry" for e in ctx.events)


def test_fallback_after_consecutive_failures(ctx):
    calls = []

    def h(req):
        calls.append(req["model"])
        if req["model"] == "primary":
            raise _timeout_exc()
        return "from-backup"
    set_mock_handler(h)
    assert call_llm("s", "u", S) == "from-backup"
    assert calls == ["primary", "primary", "backup"]
    assert any(e["type"] == "llm.fallback" for e in ctx.events)


def test_fatal_error_not_retried(ctx):
    calls = []

    def h(req):
        calls.append(1)
        raise openai.BadRequestError("bad", response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                                     body=None)
    set_mock_handler(h)
    with pytest.raises(LLMFatalError):
        call_llm("s", "u", S)
    assert len(calls) == 1


def test_empty_response_is_retryable(ctx):
    outs = iter(["", "  ", "real"])
    set_mock_handler(lambda req: next(outs))
    assert call_llm("s", "u", S) == "real"


def test_exhausted_raises_llm_error(ctx):
    set_mock_handler(lambda req: (_ for _ in ()).throw(_timeout_exc()))
    with pytest.raises(LLMError):
        call_llm("s", "u", S)


def test_circuit_breaker_opens_and_skips_primary(ctx):
    br = get_breaker("https://api.deepseek.com|primary", failure_threshold=5)
    for _ in range(5):
        br.record_failure()
    assert br.state == "open"
    calls = []

    def h(req):
        calls.append(req["model"])
        return "ok"
    set_mock_handler(h)
    assert call_llm("s", "u", S) == "ok"
    assert calls == ["backup"], "熔断打开后应直接走备用模型"


def test_breaker_half_open_recovers():
    br = get_breaker("unit|x", failure_threshold=1, recovery_timeout=0.05)
    br.record_failure()
    assert br.state == "open"
    time.sleep(0.08)
    assert br.allow() is True
    br.record_success()
    assert br.state == "closed"


def test_cancel_interrupts_stream(tmp_path):
    ctx = new_context(workspace=tmp_path)
    started = threading.Event()

    def h(req):
        started.set()
        return "x" * 5000
    set_mock_handler(h)
    ctx.cancel.cancel("user_stop")
    with use_context(ctx):
        with pytest.raises(RunCancelled):
            call_llm("s", "u", S)


def test_cancel_not_swallowed_by_except_exception(ctx):
    ctx.cancel.cancel("stop")
    caught = []
    try:
        try:
            ctx.cancel.raise_if_cancelled()
        except Exception:  # Agent 内部常见写法
            caught.append("swallowed")
    except RunCancelled:
        caught.append("propagated")
    assert caught == ["propagated"]


def test_child_token_inherits_parent():
    parent = CancelToken()
    child = parent.child()
    parent.cancel("p")
    assert child.cancelled and child.reason == "p"
    other = CancelToken()
    c2 = other.child()
    c2.cancel("child-only")
    assert not other.cancelled


def test_budget_limits_llm_calls(tmp_path):
    ctx = new_context(workspace=tmp_path, budget=Budget(max_llm_calls=2))
    set_mock_handler(lambda req: "ok")
    with use_context(ctx):
        call_llm("s", "u", S)
        call_llm("s", "u", S)
        with pytest.raises(BudgetExceeded):
            call_llm("s", "u", S)


def test_json_repair_loop(ctx):
    outs = iter(["not json", '{"a": 1}', '{"a": 1, "b": 2}'])
    set_mock_handler(lambda req: next(outs))
    data = call_llm_json("s", "u", S, validator=lambda d: None if "b" in d else "缺少 b", repair_attempts=2)
    assert data == {"a": 1, "b": 2}


def test_stream_event_routing(ctx):
    set_mock_handler(lambda req: '{"think": "思考内容", "act": "final_answer", "parameter": {"answer_text": "答"}}')
    call_llm("s", "u", S, stream_event_map={("think",): "agent.think.stream",
                                             ("parameter", "answer_text"): "agent.answer.stream"})
    think = "".join(e["data"]["text"] for e in ctx.events if e["type"] == "agent.think.stream")
    ans = "".join(e["data"]["text"] for e in ctx.events if e["type"] == "agent.answer.stream")
    assert think == "思考内容" and ans == "答"


def test_run_with_timeout_cancels_child(ctx):
    seen = {}

    def slow():
        from agent.runtime.context import current
        c = current()
        for _ in range(100):
            if c.cancel.cancelled:
                seen["child_cancelled"] = True
                raise RunCancelled("timeout")
            time.sleep(0.02)
    with pytest.raises(TimeoutError):
        run_with_timeout(slow, 0.2, "slow")
    assert seen.get("child_cancelled") and not ctx.cancel.cancelled


def test_run_with_fallback_degrades(ctx):
    def bad():
        raise ValueError("boom")
    res, used = run_with_fallback("x", [Strategy("a", bad, retries=1), Strategy("b", lambda: 42)])
    assert (res, used) == (42, "b")
    assert any(e["type"] == "step.degraded" for e in ctx.events)
    with pytest.raises(StepFailed):
        run_with_fallback("y", [Strategy("a", bad)])


def test_model_policy_forbids_other_models(ctx):
    """模型白名单：配置为其它模型（如 deepseek-v4-pro）时一律改写为 deepseek-flash，且主/备都不会被调用"""
    import json, glob, os
    from agent.agents.config import AgentConfig
    seen = []
    assert "deepseek-v4-pro" not in _gw.ALLOWED_MODELS
    set_mock_handler(lambda req: (seen.append(req["model"]), "OK")[1])
    call_llm("s", "u", LLMSettings(model_id="deepseek-v4-pro", fallback_model_id="deepseek-v4-pro"))
    assert seen == ["deepseek-flash"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in glob.glob(os.path.join(root, "configs", "*.json")):
        cfg = AgentConfig(p).settings
        assert cfg.model_id == "deepseek-flash" and cfg.fallback_model_id == "deepseek-flash", p


def test_isolated_contexts_do_not_cross_talk(tmp_path):
    """两个会话并行，事件不串台（修复原 _streaming_ctx 类级全局变量问题）"""
    set_mock_handler(lambda req: '{"think": "' + req["user"] + '"}')
    out = {"a": [], "b": []}

    def run(name):
        c = new_context(workspace=tmp_path / name,
                        sink=lambda t, step=None, agent=None, data=None: out[name].append((t, agent, data)))
        with use_context(c):
            for _ in range(5):
                call_llm("s", name, S, agent=f"agent-{name}", stream_event_map={("think",): "agent.think.stream"})
    ts = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    for name in ("a", "b"):
        agents = {a for _, a, _ in out[name]}
        assert agents == {f"agent-{name}"}
        think = "".join(d["text"] for t, _, d in out[name] if t == "agent.think.stream")
        assert set(think.replace(" ", "")) == set(name)
