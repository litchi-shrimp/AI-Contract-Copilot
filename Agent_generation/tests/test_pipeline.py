"""流水线：全流程 / 停止-继续（检查点恢复）/ 节点故障降级"""
import json
import threading
import time

import pytest

from agent.pipeline import ContractPipeline, PipelineFinished, PipelineState, WaitingForInput
from agent.runtime import RunCancelled, new_context, set_mock_handler, use_context
from fake_llm import FakeLLM

REQ = "我是房东，上海松江区个人对个人居住租赁，月租2500押一付三，允许养猫，违约金2个月租金。"


class MemStore:
    def __init__(self):
        self.saved = None
        self.count = 0

    def __call__(self, state):
        self.saved = json.loads(json.dumps(state.to_dict(), ensure_ascii=False))
        self.count += 1


def run_ctx(tmp_path, name="s1"):
    events = []
    ctx = new_context(workspace=tmp_path / name,
                      sink=lambda t, step=None, agent=None, data=None: events.append((t, step, agent, data)))
    return ctx, events


def test_full_pipeline_with_interaction(tmp_path):
    fake = FakeLLM()
    set_mock_handler(fake)
    store = MemStore()
    ctx, events = run_ctx(tmp_path)
    with use_context(ctx):
        p = ContractPipeline(PipelineState("s1"), checkpoint=store)
        r = p.advance()
        assert isinstance(r, WaitingForInput) and r.step == 1
        r = p.advance(REQ)
        assert isinstance(r, WaitingForInput) and r.step == 4, r
        r = p.advance("第三条再加一句：不得饲养犬类")
        assert isinstance(r, WaitingForInput) and r.step == 4
        r = p.advance("确认")
        assert isinstance(r, WaitingForInput) and r.step == 8
        r = p.advance("确认")
    assert isinstance(r, PipelineFinished)
    st = p.state
    assert st.status == "completed" and st.step_done == [1, 2, 3, 4, 5, 6, 7, 8]
    contract = st.artifacts["contract"]["标准化模板文本"]
    texts = json.dumps(contract, ensure_ascii=False)
    assert "（已扩写）" in texts and "维修责任由甲方承担" in texts
    assert len(st.artifacts["reviews"]) == 4
    kinds = {e[0] for e in events}
    assert {"step.start", "step.complete", "tool.call", "contract.snapshot", "workflow.complete"} <= kinds
    assert store.saved["status"] == "completed"


def test_stop_and_resume_from_checkpoint(tmp_path):
    """在 Step5 起草过程中停止 → 不写入半成品；从检查点恢复后继续完成"""
    fake = FakeLLM(delay=0.05)
    set_mock_handler(fake)
    store = MemStore()
    ctx, events = run_ctx(tmp_path)
    p = ContractPipeline(PipelineState("s2"), checkpoint=store, initial_requirement=REQ, auto_confirm=True)
    box = {}

    def worker():
        with use_context(ctx):
            try:
                box["r"] = p.advance()
            except RunCancelled as e:
                box["cancelled"] = e.reason
    th = threading.Thread(target=worker)
    th.start()
    deadline = time.time() + 20
    while time.time() < deadline and not any(c.startswith("DrafterAgent") for c in fake.calls):
        time.sleep(0.01)
    ctx.cancel.cancel("user_stop")
    th.join(20)
    assert box.get("cancelled") == "user_stop"
    saved = store.saved
    assert 5 not in saved["step_done"] and saved["current_step"] == 5
    calls_before = len(fake.calls)

    # 继续：新的运行上下文 + 从持久化状态恢复
    ctx2, _ = run_ctx(tmp_path, "s2-resume")
    with use_context(ctx2):
        p2 = ContractPipeline(PipelineState.from_dict(saved), checkpoint=store, auto_confirm=True)
        r = p2.advance()
    assert isinstance(r, PipelineFinished) and p2.state.status == "completed"
    resumed_agents = fake.calls[calls_before:]
    assert "UserFieldExtractionAgent" not in resumed_agents, "已完成步骤不应重跑"
    assert "OutlineGenerationAgent" not in resumed_agents


def test_node_failures_degrade_not_crash(tmp_path):
    """注入故障：审查 Legal 持续失败、Leader 持续失败 → 流程仍完成并记录降级"""
    fake = FakeLLM(fail={"ReviewLegalAgent": 999, "LeaderAgent": 999})
    set_mock_handler(fake)
    ctx, events = run_ctx(tmp_path)
    with use_context(ctx):
        p = ContractPipeline(PipelineState("s3"), initial_requirement=REQ, auto_confirm=True)
        r = p.advance()
    assert isinstance(r, PipelineFinished)
    st = p.state
    whats = {d["what"] for d in st.degraded}
    assert "skip_ReviewLegalAgent" in whats and "keep_contract" in whats
    assert "ReviewLegalAgent" not in st.artifacts["reviews"]


def test_outline_fallback_chain(tmp_path):
    """有模板生成失败 → 对抗生成失败 → 通用骨架兜底"""
    fake = FakeLLM(fail={"OutlineGenerationAgent": 999, "AdversarialOutlineAgent": 999})
    set_mock_handler(fake)
    ctx, _ = run_ctx(tmp_path)
    with use_context(ctx):
        p = ContractPipeline(PipelineState("s4"), initial_requirement=REQ, auto_confirm=True)
        p.advance()
    assert p.state.artifacts["outline_strategy"] == "generic_skeleton"
    assert p.state.status == "completed"


def test_template_matcher_full_library_fallback(tmp_path, monkeypatch):
    """合同类型未命中分类 → 全库 BM25 粗筛 + LLM 精排（覆盖兜底分支，防止回归）"""
    from agent.agents import TemplateMatcher as tm
    set_mock_handler(lambda req: '{"indices": [1, 0], "reason": "x"}')
    ctx, _ = run_ctx(tmp_path)
    with use_context(ctx):
        out = tm.match_templates(topK=2, user_need={"collected": {"contract_type": "完全不存在的类型"},
                                                    "extra_need": ["保密"], "summary": "加工承揽合同"})
    assert 1 <= len(out) <= 2 and all("template" in x for x in out)


def test_dirty_user_need_does_not_break_downstream(tmp_path):
    """回归：LLM 漏填 new_extra_need → extra_need 含 None，曾导致模板匹配与对抗大纲全部失败"""
    fake = FakeLLM()
    set_mock_handler(fake)
    ctx, _ = run_ctx(tmp_path)
    st = PipelineState("s6")
    st.artifacts["user_need"] = {"collected": {"contract_type": "房地产与物业管理类.房屋租赁合同（含长租公寓合同）",
                                               "scene": None}, "extra_need": [None, "允许养猫", ""], "summary": None}
    st.step_done, st.current_step = [1], 2
    st.interactive["1"] = {"started": True}
    with use_context(ctx):
        p = ContractPipeline(st, auto_confirm=True)
        p.advance()
    assert p.state.status == "completed" and not p.state.degraded, p.state.degraded
    assert p.state.artifacts["user_need"]["extra_need"] == ["允许养猫"]


def test_original_request_reaches_downstream_prompts(tmp_path):
    """回归：摘要改写丢失“单价28万元”等数值 → 下游 Agent 提示词必须包含用户原话"""
    seen = []
    fake = FakeLLM()
    set_mock_handler(lambda req: (seen.append((req.get("agent"), req.get("system", "") + req.get("user", ""))), fake(req))[1])
    ctx, _ = run_ctx(tmp_path)
    with use_context(ctx):
        p = ContractPipeline(PipelineState("s7"), initial_requirement="采购10台数控机床，单价28万元，总价280万元",
                             auto_confirm=True)
        p.advance()
    assert "单价28万元" in p.state.artifacts["user_need"]["original_request"]
    # 大纲生成（有模板或对抗式，取决于模板库）与修订主管都必须拿到用户原话
    assert any(a in ("OutlineGenerationAgent", "AdversarialOutlineAgent") and "单价28万元" in txt for a, txt in seen)
    assert any(a == "LeaderAgent" and "单价28万元" in txt for a, txt in seen)
    assert any(a.startswith("DrafterAgent") and "单价28万元" in txt for a, txt in seen)


def test_transient_failures_recovered_by_retry(tmp_path):
    fake = FakeLLM(fail={"ReviewUsageAgent": 1, "DrafterAgent_2": 1, "TemplateMatcher": 1})
    set_mock_handler(fake)
    ctx, _ = run_ctx(tmp_path)
    with use_context(ctx):
        p = ContractPipeline(PipelineState("s5"), initial_requirement=REQ, auto_confirm=True)
        p.advance()
    assert p.state.status == "completed"
    assert not p.state.degraded, p.state.degraded
