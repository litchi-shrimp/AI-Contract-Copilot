"""ContractPipeline —— 8 步合同生成流水线（可中断、可恢复、可降级）

运行模型（Reentrant State Machine）：
    pipeline.advance(user_input=None) → 一直执行到“需要用户输入”或“流程结束”
    - 返回 WaitingForInput(step, prompt) 或 PipelineFinished(state)
    - 每完成一个步骤 / 每轮交互：调用 checkpoint(state) 持久化（由调用方注入：文件 / MySQL）
    - 取消：RunContext.cancel 触发 → 当前步骤抛 RunCancelled，不写入半成品；
      恢复时 load(state) 后再次 advance()，从最后完成的步骤之后继续（未完成步骤整体重放 = 原子性）

节点鲁棒性（每个步骤都有 主策略 → 重试 → 备选 → 兜底）：
    Step1 需求提取：LLM 失败 → 提示用户重试本轮（不丢历史）；轮数上限后强制进入下一步
    Step2 模板匹配：LLM 精排 → BM25 → 空（零模板路径）
    Step3 大纲生成：有模板生成 → 零模板对抗生成 → 通用骨架
    Step4/8 交互修改：单轮超时/失败 → 回滚本轮修改并提示用户
    Step5 并行起草：单 chunk 重试 → 失败 chunk 保留大纲原文（降级）；全部失败才算失败
    Step6 并行审查：单维度重试 → 失败维度跳过（降级，记录）
    Step7 修订：计划失败 → 合同保持不变（降级）
"""
import copy
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..runtime import paths
from ..runtime.context import agent_scope, bind, current
from ..runtime.errors import BudgetExceeded, RunCancelled
from ..runtime.fallback import Strategy, StepFailed, run_with_fallback, run_with_timeout
from .skeleton import generic_skeleton
from .state import INTERACTIVE_STEPS, STEP_NAMES, PipelineState

CONFIRM_WORDS = {"确认", "没问题", "可以", "确定", "ok", "OK", "好的", "确认无误", "没问题了"}


@dataclass
class WaitingForInput:
    step: int
    prompt: str


@dataclass
class PipelineFinished:
    state: PipelineState


class StepTimeouts:
    """每步超时（秒），可由环境/配置覆盖"""
    template_matching = 180
    outline = 600
    adversarial = 900
    draft_chunk = 900
    review = 420
    leader = 1200
    interactive_turn = 300


def _emit(event_type: str, data: dict = None, step: Optional[int] = None, agent: str = "system"):
    ctx = current()
    if ctx is not None:
        ctx.emit(event_type, data or {}, agent=agent, step=step)


class ContractPipeline:
    CHUNK_COUNT = 3
    TOPK = 8
    LIVE_INTERVAL = 0.8   # 并行起草时实时文档推送的最小间隔（秒）

    def __init__(self, state: PipelineState, checkpoint: Optional[Callable[[PipelineState], None]] = None,
                 initial_requirement: Optional[str] = None, auto_confirm: bool = False,
                 timeouts: StepTimeouts = None):
        self.state = state
        self._checkpoint = checkpoint
        self.initial_requirement = initial_requirement
        self.auto_confirm = auto_confirm  # 评测/批处理：跳过交互确认
        self.t = timeouts or StepTimeouts()
        self._interactive_agents: Dict[int, Any] = {}

    # ------------------------------------------------------------ 持久化
    def checkpoint(self):
        self.state.version += 1
        self.state.updated_at = time.time()
        ctx = current()
        if ctx is not None and ctx.metrics is not None:
            self.state.metrics = ctx.metrics.to_dict()
            self.state.metrics.pop("events", None)
        if self._checkpoint is not None:
            self._checkpoint(self.state)

    def _degrade(self, step: int, what: str, detail: str = ""):
        item = {"step": step, "what": what, "detail": detail[:300], "ts": time.time()}
        self.state.degraded.append(item)
        _emit("step.degraded", {"step_name": STEP_NAMES.get(step), "strategy": what, "detail": detail[:300]},
              step=step)

    # ------------------------------------------------------------ 主循环
    def advance(self, user_input: Optional[str] = None):
        st = self.state
        if st.status in ("completed", "closed"):
            return PipelineFinished(st)
        st.status = "running"
        st.error = None
        pending_input = user_input
        while st.current_step <= 8:
            ctx = current()
            if ctx is not None:
                ctx.cancel.raise_if_cancelled()
            step = st.current_step
            name = STEP_NAMES[step]
            if step not in INTERACTIVE_STEPS or not st.interactive.get(str(step), {}).get("started"):
                partial = bool(st.interactive.get(str(step), {}).get("chunks") or
                               st.interactive.get(str(step), {}).get("reviews"))
                _emit("step.start", {"step_name": name, "resumed": partial}, step=step)
            t0 = time.time()
            with agent_scope(step=step):
                if step in INTERACTIVE_STEPS:
                    res = self._run_interactive(step, pending_input)
                    pending_input = None
                    if isinstance(res, WaitingForInput):
                        st.status = "waiting_input"
                        self.checkpoint()
                        return res
                else:
                    getattr(self, f"_step{step}")()
            st.mark_done(step)
            self.checkpoint()
            _emit("step.complete", {"step_name": name, "duration": round(time.time() - t0, 2)}, step=step)
        st.status = "completed"
        self.checkpoint()
        _emit("workflow.complete", {"degraded": st.degraded})
        return PipelineFinished(st)

    # ------------------------------------------------------------ 交互步骤通用
    def _run_interactive(self, step: int, user_input: Optional[str]):
        if step == 1:
            return self._step1(user_input)
        return self._modify_step(step, user_input)

    # ============================================================ Step 1 需求提取
    def _step1(self, user_input: Optional[str]):
        st = self.state
        if self.initial_requirement and not st.interactive.get("1", {}).get("history"):
            # 直接给定需求（评测 / 批处理）：单轮抽取，必要时自动补全缺失字段
            user_input = user_input or self.initial_requirement
        from ..agents.UserFieldExtractionAgent import UserFieldExtractionAgent
        agent = UserFieldExtractionAgent("UserFieldExtractionAgent")
        agent.patch_llm_call_for_events(step=1)
        istate = st.interactive.setdefault("1", {})
        agent.load_state(istate.get("agent"))
        if not istate.get("started"):
            istate["started"] = True
            opening = agent.opening()
            istate["agent"] = agent.export_state()
            if user_input is None:
                return WaitingForInput(1, opening)
        if user_input is None:
            return WaitingForInput(1, istate.get("last_prompt") or agent.OPENING)
        snapshot = agent.export_state()
        try:
            done, reply = agent.turn(user_input)
        except (RunCancelled, BudgetExceeded):
            agent.load_state(snapshot)
            raise
        except Exception as e:  # noqa: BLE001  本轮失败：回滚状态，提示用户重试（不丢已收集的信息）
            agent.load_state(snapshot)
            istate["agent"] = snapshot
            msg = "抱歉，系统暂时繁忙，请稍后重新发送刚才的内容。"
            _emit("system", {"message": "本轮处理失败，已回滚", "error": str(e)[:200]}, step=1)
            return WaitingForInput(1, msg)
        if self.auto_confirm and not done:
            # 无人值守：一次给全需求后直接收尾，缺失字段由 finalize 填默认值
            done = True
        istate["agent"] = agent.export_state()
        if not done:
            istate["last_prompt"] = reply
            return WaitingForInput(1, reply)
        need = UserFieldExtractionAgent.normalize_need(agent.finalize())
        st.artifacts["user_need"] = need
        paths.atomic_write_json(paths.data_dir() / "user_need_summary.json", need)
        _emit("artifact.user_need", {"collected": need.get("collected"), "summary": need.get("summary"),
                                     "extra_need": need.get("extra_need")}, step=1)
        return None

    # ============================================================ Step 2 模板匹配
    def _step2(self):
        st = self.state
        from ..agents.UserFieldExtractionAgent import UserFieldExtractionAgent
        need = st.artifacts["user_need"] = UserFieldExtractionAgent.normalize_need(st.artifacts["user_need"])
        from ..agents.TemplateMatcher import match_templates, stage2_bm25_rank, build_result_item

        def primary():
            with agent_scope(agent="TemplateMatcher"):
                return match_templates(topK=self.TOPK, user_need=need)

        try:
            result, used = run_with_fallback("模板匹配", [
                Strategy("llm_rank", primary, timeout=self.t.template_matching, retries=1),
            ])
        except StepFailed as e:
            self._degrade(2, "no_template", str(e))
            result = []
        result = result or []
        st.artifacts["match_result"] = result
        paths.atomic_write_json(paths.data_dir() / "match_result.json", result)
        names = [r.get("metadata", {}).get("template_name", "") for r in result]
        _emit("artifact.templates", {"count": len(result), "names": names[:8]}, step=2)

    # ============================================================ Step 3 大纲生成
    def _step3(self):
        st = self.state
        need = st.artifacts["user_need"]
        match = st.artifacts.get("match_result") or []
        from ..utils.outline_schema import validate_outline

        def with_template():
            from ..agents.OutlineGenerationAgent import OutlineGenerationAgent
            agent = OutlineGenerationAgent(agent_name="OutlineGenerationAgent", retrieved_templates=match,
                                           user_need=need)
            agent.patch_llm_call_for_events(step=3)
            with agent_scope(agent="OutlineGenerationAgent"):
                return agent.generate_outline()

        def adversarial():
            from ..agents.OutlineGenerationWOTem import AdversarialOutlineAgent
            agent = AdversarialOutlineAgent(agent_name="AdversarialOutlineAgent", user_need=need)
            agent.patch_llm_call_for_events(step=3)
            with agent_scope(agent="AdversarialOutlineAgent", stream_event_map=agent.STREAM_EVENT_MAP):
                outline = agent.generate()
            st.artifacts["adversarial_trace"] = getattr(agent, "trace", None)
            return outline

        strategies = []
        if match:
            strategies.append(Strategy("with_template", with_template, timeout=self.t.outline, retries=1,
                                       accept=lambda o: validate_outline(o) is None))
        strategies.append(Strategy("adversarial", adversarial, timeout=self.t.adversarial, retries=0,
                                   accept=lambda o: validate_outline(o) is None))
        try:
            outline, used = run_with_fallback("大纲生成", strategies)
        except StepFailed as e:
            self._degrade(3, "generic_skeleton", str(e))
            outline, used = generic_skeleton(need), "generic_skeleton"
        from ..utils.outline_manager import OutlineManager
        om = OutlineManager(data=outline)
        om.section_offset = 0
        om.renumber()
        st.artifacts["outline"] = om.get_outline()
        st.artifacts["outline_strategy"] = used
        paths.atomic_write_json(paths.data_dir() / "initial_outline.json", st.artifacts["outline"])
        _emit("contract.snapshot", {"kind": "outline", "outline": st.artifacts["outline"], "strategy": used}, step=3)

    # ============================================================ Step 4 / 8 交互修改
    def _modify_step(self, step: int, user_input: Optional[str]):
        st = self.state
        key = "outline" if step == 4 else "contract"
        istate = st.interactive.setdefault(str(step), {})
        if self.auto_confirm:
            istate["started"] = True
            istate["confirmed"] = True
            return None
        agent = self._interactive_agents.get(step)
        if agent is None:
            agent = self._make_modify_agent(step, st.artifacts[key])
            if istate.get("history"):
                agent.history_manager.history = list(istate["history"])
            self._interactive_agents[step] = agent
        if not istate.get("started"):
            istate["started"] = True
            prompt = ("合同大纲已生成。您可以提出修改意见（增删改条款），确认无误后回复「确认」进入起草。"
                      if step == 4 else
                      "完整合同已生成并通过多维审查修订。您可以继续提出修改意见，确认无误后回复「确认」完成。")
            istate["prompt"] = prompt
            if user_input is None:
                return WaitingForInput(step, prompt)
        if user_input is None:
            return WaitingForInput(step, istate.get("prompt", ""))
        text = str(user_input).strip()
        if text in CONFIRM_WORDS or text.lower() in {w.lower() for w in CONFIRM_WORDS}:
            istate["confirmed"] = True
            self._interactive_agents.pop(step, None)
            return None
        manager = agent.outline_manager if step == 4 else agent.contract_manager
        snap_doc = manager.snapshot()
        snap_hist = agent.history_manager.snapshot()
        try:
            reply = run_with_timeout(lambda: agent.run(text), self.t.interactive_turn,
                                     label=f"step{step}_turn")
        except (RunCancelled, BudgetExceeded):
            manager.restore(snap_doc)
            agent.history_manager.restore(snap_hist)
            raise
        except Exception as e:  # noqa: BLE001  单轮失败：回滚本轮修改，提示用户
            manager.restore(snap_doc)
            agent.history_manager.restore(snap_hist)
            msg = ("本轮处理超时，已撤销本轮修改，请简化要求后重试。" if isinstance(e, TimeoutError)
                   else "抱歉，本轮修改未能完成，已撤销本轮改动，请重试或换一种说法。")
            _emit("system", {"message": "本轮处理失败，已回滚", "error": f"{type(e).__name__}: {str(e)[:200]}"},
                  step=step)
            return WaitingForInput(step, msg)
        st.artifacts[key] = manager.get_outline()
        if step == 8:
            st.artifacts["contract"] = st.artifacts[key]
        _emit("contract.snapshot", {"kind": "outline" if step == 4 else "contract", "outline": st.artifacts[key]},
              step=step)
        istate["history"] = agent.history_manager.get_history()[-40:]
        istate["prompt"] = reply
        return WaitingForInput(step, reply)

    def _make_modify_agent(self, step: int, doc: dict):
        match = self.state.artifacts.get("match_result") or []
        if step == 4:
            from ..agents.OutlineModificationAgent import OutlineModificationAgent
            agent = OutlineModificationAgent("OutlineModificationAgent", outline_name="initial_outline.json",
                                             data=doc, match_data=match)
        else:
            from ..agents.ContractModificationAgent import ContractModificationAgent
            agent = ContractModificationAgent("ContractModificationAgent", contract_name="final_contract.json",
                                              data=doc, match_data=match)
        agent.patch_llm_call_for_events(step=step)
        return agent

    # ============================================================ Step 5 并行起草
    def _step5(self):
        st = self.state
        outline = st.artifacts["outline"]
        need = st.artifacts["user_need"]
        match = st.artifacts.get("match_result") or []
        from ..agents.DrafterAgent import DrafterAgent
        from ..utils.deal_chunk import merge_contract_chunks, split_contract_chunks

        chunks = split_contract_chunks(data=copy.deepcopy(outline), chunk_count=self.CHUNK_COUNT)
        if not chunks:
            raise StepFailed("合同起草", [("split", "大纲没有可起草的章节")])
        done_chunks: Dict[str, dict] = st.interactive.setdefault("5", {}).setdefault("chunks", {})
        names = [f"DrafterAgent_{i + 1}" for i in range(len(chunks))]
        # 实时视图：各 chunk 的当前状态拼成完整合同推送前端（单个 chunk 的局部文档不能直接展示）
        live = [done_chunks.get(str(i)) or chunks[i] for i in range(len(chunks))]
        live_lock = threading.Lock()
        # 每次工具编辑都推送整份合同会产生大量重复的大对象（N 个 Drafter × 每次编辑 × 全文），
        # 这里合并为最多每 LIVE_INTERVAL 秒推送一次；最后一次编辑由 parallel.complete 后的 snapshot 兜底
        throttle = {"last": 0.0, "pending": None}

        def publish_live(tool=None, locators=None, force=False):
            now = time.monotonic()
            with live_lock:
                if not force and now - throttle["last"] < self.LIVE_INTERVAL:
                    throttle["pending"] = (tool, locators)
                    return
                throttle["last"], throttle["pending"] = now, None
                secs = [s for c in live for s in c["标准化模板文本"]["正文章节"]]
                tpl = outline["标准化模板文本"]
                merged = {"标准化模板文本": {"合同首部": tpl.get("合同首部", ""), "正文章节": secs,
                                        "合同尾部": tpl.get("合同尾部", "")}}
                payload = json.loads(json.dumps(merged, ensure_ascii=False))
            _emit("contract.update", {"kind": "contract", "tool_name": tool, "locators": locators or [],
                                      "outline": payload}, step=5)
        _emit("contract.snapshot", {"kind": "contract", "outline": outline, "transient": True}, step=5)
        _emit("parallel.start", {"group": "drafter", "agents": names}, step=5)
        traces: Dict[str, Any] = st.artifacts.setdefault("drafter_traces", {})

        def draft(idx: int):
            name = names[idx]
            if str(idx) in done_chunks:   # 断点续跑：已完成的 chunk 不重跑
                _emit("parallel.agent_complete", {"group": "drafter", "resumed": True}, step=5, agent=name)
                return idx, done_chunks[str(idx)], "resumed"
            original = copy.deepcopy(chunks[idx])

            def run_once():
                data = copy.deepcopy(original)
                agent = DrafterAgent("DrafterAgent", chunk_data=data, match_data=match, reference_outline=outline,
                                     user_need=need)
                agent.agent_name = name
                agent.harness.agent_name = name
                with live_lock:
                    live[idx] = data
                agent.harness.on_change = publish_live
                agent.patch_llm_call_for_events(step=5)
                with agent_scope(agent=name, stream_event_map=DrafterAgent.STREAM_EVENT_MAP):
                    agent.run()
                traces[name] = {"stop_reason": agent.stop_reason, "loops": agent.loops_used,
                                "tool_records": agent.harness.export()}
                return data

            _emit("parallel.agent_start", {"group": "drafter", "progress": f"{idx + 1}/{len(chunks)}"}, step=5,
                  agent=name)
            try:
                data, used = run_with_fallback(f"起草-{name}", [
                    Strategy("react", run_once, timeout=self.t.draft_chunk, retries=1),
                ])
            except StepFailed as e:
                self._degrade(5, f"{name}_keep_outline", str(e))
                data, used = original, "keep_outline"
            if used != "keep_outline":
                done_chunks[str(idx)] = data
            if throttle["pending"] is not None:
                publish_live(*throttle["pending"], force=True)
            _emit("parallel.agent_complete", {"group": "drafter", "strategy": used}, step=5, agent=name)
            return idx, data, used

        results = self._parallel([lambda i=i: draft(i) for i in range(len(chunks))])
        results.sort(key=lambda r: r[0])
        if all(r[2] == "keep_outline" for r in results):
            raise StepFailed("合同起草", [("all_chunks", "所有分块起草均失败")])
        contract = merge_contract_chunks(original_outline=outline, chunk_dicts=[r[1] for r in results])
        st.artifacts["contract"] = contract
        st.artifacts["draft_contract"] = copy.deepcopy(contract)
        _emit("parallel.complete", {"group": "drafter", "agents_count": len(results)}, step=5)
        _emit("contract.snapshot", {"kind": "contract", "outline": contract}, step=5)

    # ============================================================ Step 6 并行审查
    def _step6(self):
        st = self.state
        contract = st.artifacts["contract"]
        need = st.artifacts["user_need"]
        from ..agents.ReviewAgents import REVIEW_AGENTS, ReviewUsageAgent
        done: Dict[str, dict] = st.interactive.setdefault("6", {}).setdefault("reviews", {})
        names = list(REVIEW_AGENTS)
        _emit("parallel.start", {"group": "review", "agents": names}, step=6)

        def review(name: str):
            if name in done:
                return name, done[name]
            cls = REVIEW_AGENTS[name]

            def run_once():
                kwargs = {"agent_name": name, "data": copy.deepcopy(contract)}
                if cls is ReviewUsageAgent:
                    kwargs["user_need"] = need
                agent = cls(**kwargs)
                agent.patch_llm_call_for_events(step=6)
                with agent_scope(agent=name):
                    return agent.run()

            _emit("parallel.agent_start", {"group": "review"}, step=6, agent=name)
            try:
                res, _ = run_with_fallback(f"审查-{name}", [Strategy("llm", run_once, timeout=self.t.review, retries=1)])
                done[name] = res
                paths.atomic_write_json(paths.outputs_dir() / f"{name}.json", res)
            except StepFailed as e:
                self._degrade(6, f"skip_{name}", str(e))
                res = None
            _emit("parallel.agent_complete", {"group": "review", "problems": len((res or {}).get("problems", []))},
                  step=6, agent=name)
            return name, res

        results = self._parallel([lambda n=n: review(n) for n in names])
        reviews = {n: r for n, r in results if r is not None}
        st.artifacts["reviews"] = reviews
        _emit("artifact.reviews", {n: [{"id": p["problem_id"], "level": p["problem_level"],
                                       "desc": p["problem_description"][:120]} for p in r.get("problems", [])]
                                   for n, r in reviews.items()}, step=6)
        _emit("parallel.complete", {"group": "review", "agents_count": len(reviews)}, step=6)

    # ============================================================ Step 7 修订
    def _step7(self):
        st = self.state
        contract = copy.deepcopy(st.artifacts["contract"])
        reviews = st.artifacts.get("reviews") or {}
        from ..agents.LeaderAgent import LeaderAgent

        def run_once():
            agent = LeaderAgent("LeaderAgent", contract_data=copy.deepcopy(contract), reviews=reviews,
                                user_need=st.artifacts.get("user_need"))
            agent.patch_llm_call_for_events(step=7)
            with agent_scope(agent="LeaderAgent", stream_event_map=LeaderAgent.STREAM_EVENT_MAP):
                return agent.run()

        try:
            (report, revised), _ = run_with_fallback("修订", [Strategy("plan_execute", run_once,
                                                                     timeout=self.t.leader, retries=1)])
            if report.get("degraded"):
                self._degrade(7, "keep_contract", report.get("error", ""))
        except StepFailed as e:
            self._degrade(7, "keep_contract", str(e))
            report, revised = {"message": "修订失败，保留起草版本", "degraded": True}, contract
        st.artifacts["contract"] = revised
        st.artifacts["leader_report"] = {k: v for k, v in report.items() if k != "plan"}
        paths.atomic_write_json(paths.outputs_dir() / "final_contract.json", revised)
        _emit("contract.snapshot", {"kind": "contract", "outline": revised}, step=7)

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _parallel(fns: List[Callable[[], Any]]) -> list:
        """并行执行（携带 RunContext）；任一子任务抛 RunCancelled 则整体取消"""
        if not fns:
            return []
        with ThreadPoolExecutor(max_workers=len(fns)) as pool:
            futures = [pool.submit(bind(fn)) for fn in fns]
            out, cancelled = [], None
            for f in futures:
                try:
                    out.append(f.result())
                except (RunCancelled, BudgetExceeded) as e:
                    cancelled = e
            if cancelled is not None:
                raise cancelled
            return out
