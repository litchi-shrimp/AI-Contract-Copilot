#!/usr/bin/env python3
"""
Workflow Orchestrator — 异步编排 8 步合同生成流程

职责：
1. 按顺序/并行执行 8 个业务流程步骤
2. 通过 EventBus 推送步骤状态、Agent 输出、流式 Token
3. 每个 Agent 独立实例，通过上下文变量自动获取 EventBus
4. 支持用户在交互步骤（Step 4、Step 8）输入反馈
"""
import asyncio
import json
import time
import logging
import traceback
from typing import Optional
from pathlib import Path

from agent.utils.event_bus import EventBus, set_event_bus, release_event_bus
from agent.core.base_agent import BASE_DIR, AGENT_DIR
from .session_manager import SessionState

logger = logging.getLogger("orchestrator")


def _is_cancelled(state: SessionState) -> bool:
    return state._cancelled


async def _run_agent_in_executor(loop: asyncio.AbstractEventLoop, state: SessionState, fn, timeout: int = 1800):
    """在线程池中异步运行 Agent 函数，支持超时，取消"""
    """
    这里使用 asyncio.run_in_executor 来在线程池中运行 Agent 函数，以避免阻塞事件循环。
    也就是将原来同步代码的Agent包装一下，asyncio丢到线程池里，让其异步执行（外部await）。这样Agent在等待期间，主事件循环不会被阻塞，可以继续处理SSE推送，心跳等。
    整体而言，这就是一个"桥接层" ，它让同步的 Agent 代码能工作在 async 世界里，同时附带超时+取消保护。

    当然这个包装只是一个补救措施，因为历史原因Agent代码是同步的，但又需要集成到async的事件循环架构中，后续可以直接将各个Agent的代码内部都重写成 async
    """

    def _wrapped():
        if _is_cancelled(state):
            return None
        return fn()

    future = loop.run_in_executor(None, _wrapped)
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        logger.warning(f"Agent 执行超时 ({timeout}s)")
        state.cancel()
        return None
    except Exception as e:
        if _is_cancelled(state):
            logger.info("Agent 已被取消")
            return None
        raise


async def emit_outline_update(eb: EventBus, data: dict, step: int):
    """发射大纲更新事件到前端大纲面板

    自动兼容两种格式：
    - 已有 "标准化模板文本" 包裹 → 直接发射
    - 无包裹（合同结构） → 包装后发射
    """
    if not data:
        return
    if "标准化模板文本" in data and isinstance(data.get("标准化模板文本"), dict):
        outline_json = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        outline_json = json.dumps({"标准化模板文本": data}, ensure_ascii=False, indent=2)
    await eb.put("agent.outline.stream", step=step, agent="system", data={"text": outline_json})


# ==================== Step 1: 用户需求提取 ====================

async def step1_user_extraction(state: SessionState) -> Optional[dict]:
    """Step 1: 用户需求提取（SSE 交互式）

    UserFieldExtractionAgent 通过注入的 event_bus 与 SSE 前端交互，
    通过 emit_agent_ask / wait_for_user_input_sync 自主驱动对话。
    """
    # 从当前SessionState中获取event_bus(每个session唯一)
    eb = state.event_bus
    step = 1
    await eb.emit_step_start(step, "用户需求提取") # put step1开始 事件

    from agent.agents.UserFieldExtractionAgent import UserFieldExtractionAgent
    agent = UserFieldExtractionAgent("UserFieldExtractionAgent")
    # 这里直接将event_bus 注入在Agent的self.event_bus 中，让其能够将一些信息整块put到事件总线上。
    agent.event_bus = state.event_bus     
    agent.patch_llm_call_for_events(step=step) # 为LLM调用添加事件推送，这里是不想修改Agent内部的llm_call，所以直接在外部将llm_call包装成async_llm
    # 更新SessionState状态
    state.current_agent = "UserFieldExtractionAgent"
    state.status = "waiting_input"
    await eb.emit_agent_start("UserFieldExtractionAgent", step=step) # put Agent1启动事件
    loop = asyncio.get_event_loop() # 获取当前事件循环

    def _run():
        return agent.start_conversation()
    # 包装成异步函数，运行Agent，获取用户需求
    user_need = await _run_agent_in_executor(loop, state, _run, timeout=3600)

    if user_need is None or _is_cancelled(state):
        await eb.emit_step_complete(step, "用户需求提取", 0, cancelled=True)
        return None

    output_path = BASE_DIR / "agent" / "data" / "user_need_summary.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(user_need, f, ensure_ascii=False, indent=2)

    await eb.emit_step_complete(step, "用户需求提取", 0, output=str(output_path))
    return user_need


# ==================== Step 2: 模板匹配 ====================

async def step2_template_matching(state: SessionState, user_need: dict) -> list:
    eb = state.event_bus
    step = 2
    await eb.emit_step_start(step, "模板匹配")
    await eb.emit_system("system", "正在匹配模板...", step=step)

    from agent.agents.TemplateMatcher import match_templates
    TOPK = 8

    loop = asyncio.get_event_loop()
    # 包装成异步函数，运行Agent，获取匹配结果
    match_result = await _run_agent_in_executor(
        loop, state, lambda: match_templates(topK=TOPK, user_need=user_need, event_bus=state.event_bus), timeout=120
    )

    SAVE_MATCH_RESULT = BASE_DIR / "agent" / "data" / "match_result.json"
    with open(SAVE_MATCH_RESULT, "w", encoding="utf-8") as f:
        json.dump(match_result, f, ensure_ascii=False, indent=4)

    await eb.emit_system("system", f"匹配到 {min(TOPK, len(match_result))} 个合同模板", step=step)
    await eb.emit_step_complete(step, "模板匹配", 0, template_count=len(match_result))
    return match_result


# ==================== Step 3: 大纲生成 ====================

async def step3_outline_generation(state: SessionState, match_result: list, user_need: dict) -> Optional[dict]:
    eb = state.event_bus
    step = 3
    await eb.emit_step_start(step, "初始合同大纲生成")
    await eb.emit_system("system", "正在生成初始合同大纲...", step=step)

    loop = asyncio.get_event_loop()

    if _is_cancelled(state):
        return None

    if len(match_result) == 0:
        # 未匹配到模板，启动对抗式大纲生成策略
        await eb.emit_system("system", "未匹配到模板，启动对抗式大纲生成...", step=step)
        from agent.agents.OutlineGenerationWOTem import AdversarialOutlineAgent
        agent = AdversarialOutlineAgent(
            agent_name="AdversarialOutlineAgent",
            user_need=user_need,
        )
        # 同样，这里直接将event_bus 注入在Agent的self.event_bus 中，让其能够将一些信息整块put到事件总线上。
        agent.event_bus = state.event_bus
        agent.patch_llm_call_for_events(step=step)
        state.current_agent = "AdversarialOutlineAgent"
        await eb.emit_agent_start("AdversarialOutlineAgent", step=step)
        def _run_adversarial():
            return agent.generate()
        # 包装成异步函数，运行Agent，通过对抗方式获得大纲
        outline = await _run_agent_in_executor(loop, state, _run_adversarial, timeout=600)
        await eb.emit_agent_end("AdversarialOutlineAgent", 0, step=step)

    else:
        # 匹配到模板，启动正常大纲生成策略
        await eb.emit_system("system", "匹配到模板，启动正常大纲生成...", step=step)
        from agent.agents.OutlineGenerationAgent import OutlineGenerationAgent
        agent = OutlineGenerationAgent(
            agent_name="OutlineGenerationAgent",
            retrieved_templates=match_result,
            user_need=user_need,
        )
        agent.event_bus = state.event_bus
        agent.patch_llm_call_for_events(step=step)
        state.current_agent = "OutlineGenerationAgent"
        await eb.emit_agent_start("OutlineGenerationAgent", step=step)
        def _run_outline():
            return agent.generate_outline()
        # 包装成异步函数，运行Agent，通过正常方式获得大纲
        outline = await _run_agent_in_executor(loop, state, _run_outline, timeout=600)
        await eb.emit_agent_end("OutlineGenerationAgent", 0, step=step)

    if _is_cancelled(state):
        return None

    output_path = BASE_DIR / "agent" / "data" / "initial_outline.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(outline, f, ensure_ascii=False, indent=4)

    await emit_outline_update(eb, outline, step)

    await eb.emit_step_complete(step, "初始合同大纲生成", 0, output=str(output_path))
    return outline


# ==================== Step 4: 大纲修改（交互） ====================

async def step4_outline_modification(state: SessionState, outline: dict, match_result: list) -> tuple:
    eb = state.event_bus
    step = 4
    await eb.emit_step_start(step, "合同大纲修改")
    await eb.emit_system("system", "进入大纲修改交互阶段...", step=step)

    from agent.agents.OutlineModificationAgent import OutlineModificationAgent
    agent = OutlineModificationAgent("modification_agent", outline_name="initial_outline.json", data=outline, match_data=match_result)
    # 同样，这里直接将event_bus 注入在Agent的self.event_bus 中，让其能够将一些信息整块put到事件总线上。
    agent.event_bus = state.event_bus
    agent.patch_llm_call_for_events(step=step)

    state.current_agent = "OutlineModificationAgent"
    state.status = "waiting_input"
    await eb.emit_agent_ask("OutlineModificationAgent",
        "合同大纲已为您生成完毕，如果你有任何问题或者疑惑都可以与我交流，如果大纲没有问题，回复确认即可~",
        step=step)
    # 包装成异步函数，运行大纲修改交互Agent，最后获得用户确认Label和最终大纲
    user_confirmed, final_outline = await _interactive_agent_loop(state, agent, step)

    if user_confirmed and not _is_cancelled(state):
        with open(BASE_DIR / "agent" / "data" / "initial_outline.json", "w", encoding="utf-8") as f:
            json.dump(final_outline, f, ensure_ascii=False, indent=4)
        await emit_outline_update(eb, final_outline, step)

    await eb.emit_step_complete(step, "合同大纲修改", 0, confirmed=user_confirmed)
    return user_confirmed, final_outline


# ==================== Step 5: 合同起草（chunk 并行） ====================

async def step5_drafting(state: SessionState, outline: dict, match_result: list, user_need: dict) -> Optional[dict]:
    eb = state.event_bus
    step = 5
    await eb.emit_step_start(step, "合同起草")
    await eb.emit_system("system", "正在起草完整合同...", step=step)

    if _is_cancelled(state):
        return None

    from agent.utils.deal_chunk import split_contract_chunks, merge_contract_chunks
    from agent.agents.DrafterAgent import DrafterAgent

    CHUNK_COUNT = 3
    chunks = split_contract_chunks(data=outline, chunk_count=CHUNK_COUNT)

    await eb.emit_parallel_start(
        "drafter", [f"DrafterAgent_chunk_{i+1}" for i in range(len(chunks))], step=step
    )

    async def process_chunk_async(chunk_data: dict, idx: int) -> Optional[dict]:
        if _is_cancelled(state):
            return None
        chunk_name = f"chunk_{idx}"
        await eb.emit_parallel_agent_start("drafter", f"DrafterAgent_{chunk_name}", f"{idx}/{len(chunks)}", step=step)

        draft_agent = DrafterAgent(
            "DrafterAgent",
            chunk_data=chunk_data,
            match_data=match_result,
            reference_outline=outline,
            user_need=user_need,
        )
        draft_agent.event_bus = eb
        draft_agent.patch_llm_call_for_events(step=step)

        def _run():
            if _is_cancelled(state):
                return None
            draft_agent.run()
            return chunk_data

        loop = asyncio.get_event_loop()
        # 包装成异步函数，运行单个合同起草Agent，获得合同起草结果
        result = await _run_agent_in_executor(loop, state, _run, timeout=600)
        await eb.emit_parallel_agent_complete("drafter", f"DrafterAgent_{chunk_name}", 0, step=step)
        return result

    tasks = [process_chunk_async(chunk, i + 1) for i, chunk in enumerate(chunks)]
    processed_chunks = await asyncio.gather(*tasks)
    processed_chunks = [c for c in processed_chunks if c is not None]

    if _is_cancelled(state) or not processed_chunks:
        return None

    await eb.emit_parallel_complete("drafter", len(processed_chunks), step=step)

    contract = merge_contract_chunks(
        original_outline=outline,
        chunk_dicts=processed_chunks,
        output_json=BASE_DIR / "outputs" / "initial_contract.json",
    )

    await emit_outline_update(eb, contract, step)

    await eb.emit_system("system", "合同起草完成", step=step)
    await eb.emit_step_complete(step, "合同起草", 0)
    return contract


# ==================== Step 6: 审查（并行） ====================

async def step6_review(state: SessionState, contract: dict, user_need: dict) -> dict:
    eb = state.event_bus
    step = 6
    await eb.emit_step_start(step, "合同审查")
    await eb.emit_system("system", "正在并行审查合同...", step=step)

    from agent.agents.ReviewAgents import (
        ReviewConsistencyAgent, ReviewUsageAgent,
        ReviewLegalAgent, ReviewCompletenessAgent,
    )

    review_classes = [
        ("ReviewConsistencyAgent", ReviewConsistencyAgent),
        ("ReviewLegalAgent", ReviewLegalAgent),
        ("ReviewUsageAgent", ReviewUsageAgent),
        ("ReviewCompletenessAgent", ReviewCompletenessAgent),
    ]

    await eb.emit_parallel_start("review", [name for name, _ in review_classes], step=step)

    async def run_single_review(name: str, agent_class, contract_data: dict) -> Optional[tuple]:
        if _is_cancelled(state):
            return None
        await eb.emit_parallel_agent_start("review", name, "", step=step)
        start_t = time.time()

        kwargs = {"agent_name": name, "data": contract_data}
        if agent_class is ReviewUsageAgent:
            kwargs["user_need"] = user_need

        agent = agent_class(**kwargs)
        agent.event_bus = eb
        agent.patch_llm_call_for_events(step=step)

        def _run():
            for attempt in range(3):
                if _is_cancelled(state):
                    return None
                try:
                    return agent.run()
                except Exception as e:
                    logger.warning(f"[{name}] 第 {attempt+1} 次尝试失败: {e}")
                    if attempt == 2:
                        raise
                    time.sleep(1)
            return None

        loop = asyncio.get_event_loop()
        # 包装成异步函数，运行单个合同审查Agent，获得审查结果
        result = await _run_agent_in_executor(loop, state, _run, timeout=600)

        if result is not None:
            output_path = BASE_DIR / "outputs" / f"{name}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=4)

        duration = time.time() - start_t
        await eb.emit_parallel_agent_complete("review", name, duration, step=step)
        return (name, result) if result else None

    tasks = [run_single_review(name, cls, contract) for name, cls in review_classes]
    results = await asyncio.gather(*tasks)
    reviews = {k: v for k, v in results if v is not None}

    await eb.emit_parallel_complete("review", len(reviews), step=step)
    await eb.emit_step_complete(step, "合同审查", 0)
    return reviews


# ==================== Step 7: Leader 修订 ====================

async def step7_leader_revision(state: SessionState, contract: dict, reviews: dict) -> Optional[dict]:
    eb = state.event_bus
    step = 7
    await eb.emit_step_start(step, "Leader 修订")
    await eb.emit_system("system", "正在运行 LeaderAgent 汇总审查结果并执行修订...", step=step)

    from agent.agents.LeaderAgent import LeaderAgent
    agent = LeaderAgent("LeaderAgent", contract_data=contract, reviews=reviews)
    agent.event_bus = eb
    agent.patch_llm_call_for_events(step=step)

    state.current_agent = "LeaderAgent"
    await eb.emit_agent_start("LeaderAgent", step=step)

    loop = asyncio.get_event_loop()
    # 包装成异步函数，运行LeaderAgent，获得修订结果
    report, modified_contract = await _run_agent_in_executor(loop, state, agent.run, timeout=600)

    if _is_cancelled(state):
        return None
    
    with open(BASE_DIR / "outputs" / "initial_contract.json", "w", encoding="utf-8") as f:
        json.dump(modified_contract, f, ensure_ascii=False, indent=4)
    await emit_outline_update(eb, modified_contract, step)

    await eb.emit_agent_end("LeaderAgent", 0, step=step)
    await eb.emit_step_complete(step, "Leader 修订", 0)
    return modified_contract


# ==================== Step 8: 终稿确认（交互） ====================

async def step8_final_confirmation(state: SessionState, contract: dict) -> tuple:
    eb = state.event_bus
    step = 8
    await eb.emit_step_start(step, "终稿确认")
    await eb.emit_system("system", "进入合同终稿确认阶段...", step=step)

    from agent.agents.ContractModificationAgent import ContractModificationAgent
    agent = ContractModificationAgent("modification_agent", contract_name="initial_contract.json", data=contract)
    agent.event_bus = eb
    agent.patch_llm_call_for_events(step=step)

    state.current_agent = "ContractModificationAgent"
    state.status = "waiting_input"
    await eb.emit_agent_ask("ContractModificationAgent",
        "整体合同已为您生成完毕，如果你有任何问题或者进一步修改需求都可以与我交流，如果确认最后的合同，请输入确认~",
        step=step)

    user_confirmed, final_contract = await _interactive_agent_loop(state, agent, step)

    if user_confirmed and not _is_cancelled(state):
        with open(BASE_DIR / "outputs" / "initial_contract.json", "w", encoding="utf-8") as f:
            json.dump(final_contract, f, ensure_ascii=False, indent=4)
        with open(BASE_DIR / "outputs" / "final_contract_text.txt", "w", encoding="utf-8") as f:
            f.write(json.dumps(final_contract, ensure_ascii=False, indent=4))
        await emit_outline_update(eb, final_contract, step)

    await eb.emit_step_complete(step, "终稿确认", 0, confirmed=user_confirmed)
    return user_confirmed, final_contract


# ==================== 交互式 Agent 循环 ====================

async def _interactive_agent_loop(state: SessionState, agent, step: int) -> tuple:
    """交互式 Agent 循环：EventBus 驱动，替代 input()"""
    """这里将ReAct循环部分的Intent识别和内部单轮agent run 重写了一下，将”等用户输入“这个事件异步化，不阻塞主线程"""
    eb = state.event_bus
    user_confirmed = False

    while True:
        # ─── 第一部分：等用户输入（纯异步） ───
        if _is_cancelled(state):
            break
        if state.is_expired:
            await eb.emit_timeout(state.session.timeout, state.session.last_active)
            break
        user_input = await eb.wait_for_user_input(timeout=state.session.timeout)
        if user_input is None:
            await eb.emit_system(agent.agent_name, "等待用户输入超时", step=step)
            break
        if _is_cancelled(state):
            break
        state.refresh()
        if user_input.lower() in ['确认', '没问题', '退出', 'quit', 'exit']:
            user_confirmed = True
            break
        
        # ─── 第二部分：简单 LLM 意图识别 ───
        try:
            all_memory = agent.history_manager.get_history() if hasattr(agent, 'history_manager') else []
            filter_memory = [item for item in all_memory if item["role"] in ["user", "agent"]][-10:]
            intent_prompt = """
                你是一个专业的用户意图确认助手，根据用户的回答确认用户是否确认合同。
                输出严格遵循下面JSON格式：
                {"completed": true or false}
            """
            loop = asyncio.get_event_loop()
            response = await _run_agent_in_executor(
                loop, state,
                lambda: agent.llm_call(
                    intent_prompt,
                    str(filter_memory) + "\n【当前用户回答】" + user_input
                ),
                timeout=30,
            )
            from agent.utils.response_parser import ResponseParser
            parsed = ResponseParser.parse_agent_response(response)
            if parsed.get("completed") is True:
                user_confirmed = True
                break
        except Exception:
            await eb.emit_system(agent.agent_name, "意图识别失败，请重新输入", step=step)
            continue
        

        # ─── 第三部分：执行 Agent，将原同步Agent代码（agent.run）丢进线程池，内部llmcall不阻塞主线程， ───

        state.status = "running"
        outline_snapshot = None
        history_snapshot = None
        if hasattr(agent, 'outline_manager') and hasattr(agent.outline_manager, 'snapshot'):
            outline_snapshot = agent.outline_manager.snapshot()
        if hasattr(agent, 'history_manager') and hasattr(agent.history_manager, 'snapshot'):
            history_snapshot = agent.history_manager.snapshot()

        try:
            def run_agent():
                if _is_cancelled(state):
                    return "执行已取消"
                return agent.run(user_input)

            loop = asyncio.get_event_loop()
            result = await _run_agent_in_executor(
                loop, state, run_agent, timeout=getattr(agent, '_timeout_seconds', 200),
            )
            if _is_cancelled(state):
                break
            await eb.emit_agent_ask(agent.agent_name, result, step=step)
            if hasattr(agent, 'outline_manager') and hasattr(agent.outline_manager, 'save_outline'):
                agent.outline_manager.save_outline()

        except asyncio.TimeoutError:
            await eb.emit_system(agent.agent_name, "执行超时，请简化要求或重新输入", step=step)
            if hasattr(agent, 'outline_manager') and hasattr(agent.outline_manager, 'restore') and outline_snapshot:
                agent.outline_manager.restore(outline_snapshot)
            if hasattr(agent, 'history_manager') and hasattr(agent.history_manager, 'restore') and history_snapshot:
                agent.history_manager.restore(history_snapshot)
            continue

        state.status = "waiting_input"

    data = None
    if hasattr(agent, 'outline_manager') and hasattr(agent.outline_manager, 'get_outline'):
        data = agent.outline_manager.get_outline()
    elif hasattr(agent, 'contract_manager') and hasattr(agent.contract_manager, 'get_outline'):
        data = agent.contract_manager.get_outline()

    return user_confirmed, data


# ==================== 主workflow编排函数 ====================

async def run_workflow(session_id: str, user_need_input: str = None):
    """运行完整的 8 步合同生成流程

    Args:
        session_id: Session ID，每个会话单独一个异步事件循环
        user_need_input: 如果提供则跳过 Step 1，直接使用该文本作为用户需求（一般用不到，仅快速调试使用）
    """
    from .session_manager import get_session_manager
    # 创建当前会话
    sm = get_session_manager()
    state = sm.get_session(session_id)
    if not state:
        logger.error(f"Session {session_id} 不存在")
        return
    # 设置当前会话的事件总线
    eb = state.event_bus
    set_event_bus(eb)

    overall_start = time.time()
    await eb.emit_workflow_start(steps_total=8)
    state.status = "running"

    try:
        # === Step 1  用户需求提取 ===
        if user_need_input:
            user_need = {"collected": {}, "extra_need": [], "summary": user_need_input}
            await eb.emit_step_start(1, "用户需求提取")
            output_path = BASE_DIR / "agent" / "data" / "user_need_summary.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(user_need, f, ensure_ascii=False, indent=2)
            await eb.emit_step_complete(1, "用户需求提取", 0, note="使用用户提供的初始需求")
        else:
            state.current_step = 1
            user_need = await step1_user_extraction(state)
            if user_need is None:
                state.status = "completed"
                await eb.emit_workflow_complete(time.time() - overall_start)
                state.result = {"message": "用户取消了需求提取"}
                return
        # 每次执行后检查是否有取消信号，如果有则抛出异常
        if _is_cancelled(state): raise BaseException("cancelled")

        # === Step 2  模板匹配 ===
        state.current_step = 2
        match_result = await step2_template_matching(state, user_need)

        if _is_cancelled(state): raise BaseException("cancelled")

        # === Step 3  大纲生成 ===
        state.current_step = 3
        outline = await step3_outline_generation(state, match_result, user_need)
        if outline is None: raise BaseException("cancelled")

        if _is_cancelled(state): raise BaseException("cancelled")

        # === Step 4  大纲交互修改 ===
        state.current_step = 4
        user_confirmed, outline = await step4_outline_modification(state, outline, match_result)
        if _is_cancelled(state): raise BaseException("cancelled")
        if not user_confirmed:
            state.status = "completed"
            await eb.emit_workflow_complete(time.time() - overall_start)
            state.result = {"message": "用户未确认大纲，流程终止"}
            return

        # === Step 5  并行合同草稿生成 ===
        state.current_step = 5
        contract = await step5_drafting(state, outline, match_result, user_need)
        if contract is None: raise BaseException("cancelled")

        if _is_cancelled(state): raise BaseException("cancelled")

        # === Step 6  合同审查 ===
        state.current_step = 6
        reviews = await step6_review(state, contract, user_need)

        if _is_cancelled(state): raise BaseException("cancelled")

        # === Step 7  Leader 修订 ===
        state.current_step = 7
        contract = await step7_leader_revision(state, contract, reviews)
        if contract is None: raise BaseException("cancelled")

        if _is_cancelled(state): raise BaseException("cancelled")

        # === Step 8  最终确认合同，交互修改 ===
        state.current_step = 8
        user_confirmed, contract = await step8_final_confirmation(state, contract)
        if not user_confirmed:
            await eb.emit_system("system", "用户未确认最终合同", step=8)

        state.status = "completed"
        state.result = {"message": "合同生成全部流程完成", "confirmed": user_confirmed}
        await eb.emit_workflow_complete(time.time() - overall_start)

    except BaseException as e:
        if _is_cancelled(state):
            state.status = "completed"
            state.result = {"message": "Session 已取消"}
            await eb.emit_system("system", "Session 已取消")
        else:
            state.status = "error"
            state.error = str(e)
            logger.error(f"Workflow 异常: {traceback.format_exc()}")
            await eb.emit_error("workflow", type(e).__name__, str(e))
        await eb.emit_workflow_complete(time.time() - overall_start)
    finally:
        release_event_bus()
