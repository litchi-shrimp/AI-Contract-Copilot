#!/usr/bin/env python3
"""
起草Agent，负责补全合同大纲细节，生成初始完整合同（ReAct）

鲁棒性：
- 所有工具调用经 ToolHarness（白名单 / 参数校验 / 事务回滚 / 审计）
- JSON 解析失败：带错误信息让模型自修复，连续 N 次失败熔断退出（保留已完成的修改）
- 重复动作检测：连续相同的失败调用 → 注入提示打破死循环；连续 noop → 收敛退出
- 循环上限 max_loop + 取消检查（RunCancelled 冒泡，不吞）
- 产物写入会话工作区（runtime.paths），会话之间互不影响
"""
import json
import sys
from typing import Optional

from ..llm.prompt_builder import DrafterAgentPrompt
from ..utils.tool_manager import ToolManager
from ..utils.outline_manager import OutlineManager
from ..core.base_agent import BaseAgent, AGENTS_DIR, AGENT_DIR, BASE_DIR
from ..runtime import paths

MAX_CONSECUTIVE_PARSE_ERRORS = 3
MAX_REPEAT_FAILURES = 2


class DrafterAgent(BaseAgent):
    """补全合同大纲细节，生成初始完整合同"""

    STREAM_EVENT_MAP = {
        ("think",): "agent.think.stream",
        ("act",): "agent.act.stream",
        ("parameter", "answer_text"): "agent.answer.stream",
    }

    def __init__(self, agent_name: str, outline_name: str = None, chunk_data: dict = None, match_data: list = None,
                 reference_outline: dict = None, user_need: dict = None):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_initial_contract.json")

        self.outline_name = outline_name or ""
        self.outline_chunk_path = paths.data_dir() / outline_name if outline_name else None
        self.outline_path = paths.data_dir() / "initial_outline.json"
        self.match_result_path = paths.data_dir() / "match_result.json"
        self.tool_list_path = AGENTS_DIR / "drafter_agent_tools.json"
        self.match_data = match_data or None
        self.user_need = user_need or None
        self.have_template = True
        if not match_data:  # 无匹配模板：不提供 retrieve_template_reference，只有增/改/websearch
            self.tool_list_path = AGENTS_DIR / "drafter_agent_tools_wo_tem.json"
            self.have_template = False

        with open(self.tool_list_path, "r", encoding="utf-8") as f:
            self.tools = json.load(f)
        if chunk_data:
            self.outline_manager = OutlineManager(data=chunk_data, match_data=match_data)
        else:
            self.outline_manager = OutlineManager(outline_path=self.outline_chunk_path,
                                                  match_result_path=self.match_result_path)
        if reference_outline:
            self.outline_manager2 = OutlineManager(data=reference_outline, match_data=match_data)
        else:
            self.outline_manager2 = OutlineManager(self.outline_path, self.match_result_path)

        self.prompt_builder = DrafterAgentPrompt(self.outline_manager, self.tools, self.outline_manager2,
                                                 have_tem=self.have_template, user_need=self.user_need)
        self.tool_manager = ToolManager(self.outline_manager, tools_json=self.tools)
        self.harness = self.tool_manager.harness
        self.harness.agent_name = agent_name
        self.stop_reason = ""
        self.loops_used = 0

    def run(self) -> str:
        system_prompt = self.prompt_builder.build_system_prompt()
        user_prompt = self.prompt_builder.build_user_prompt()
        self.history_manager.add_user_input(user_prompt)
        answer_text = ""
        parse_errors = 0
        last_fail_sig: Optional[str] = None
        repeat_fail = 0
        self.stop_reason = "max_loop"

        for loop in range(self.config.max_loop):
            self.check_cancelled()
            self.loops_used = loop + 1
            response = self.llm_call(system_prompt, str(self.history_manager.get_history()))
            # 检索结果用完即删，避免污染上下文
            self.history_manager.del_typeall_history("retrieval")
            parsed = self.response_parser.parse_agent_response(response)

            if "error" in parsed and len(parsed) == 1:
                parse_errors += 1
                if parse_errors >= MAX_CONSECUTIVE_PARSE_ERRORS:
                    self.stop_reason = "parse_error_breaker"
                    print(f"[{self.agent_name}] 连续 {parse_errors} 次输出非法 JSON，熔断退出（保留已完成修改）",
                          file=sys.stderr)
                    break
                self.history_manager.add_agent_response(response[:2000])
                self.history_manager.add_user_input(f"输出格式错误（{parsed['error'][:120]}），请严格按 Schema 返回合法 JSON")
                continue
            parse_errors = 0

            think = parsed.get("think", "")
            act = parsed.get("act")
            param = parsed.get("parameter", {}) or {}
            completed = bool(parsed.get("completed", False))
            self.history_manager.add_agent_response(think)

            if act == "use_tool":
                tool_name = param.get("tool_name")
                tool_params = param.get("tool_params", {}) or {}
                result = self.harness.execute(tool_name, tool_params)
                rec = self.harness.records[-1] if self.harness.records else None
                if tool_name in ("retrieve_template_reference", "web_search"):
                    self.history_manager.add_retrieval_info(result)
                else:
                    self.history_manager.add_tool_observation(f"工具执行结果：{result}")
                # 重复失败检测：同一个失败调用反复出现 → 提示换策略；仍重复 → 退出
                if rec is not None and rec.status in ("rejected", "error", "timeout"):
                    sig = f"{tool_name}|{json.dumps(rec.params, ensure_ascii=False, sort_keys=True)[:400]}"
                    repeat_fail = repeat_fail + 1 if sig == last_fail_sig else 1
                    last_fail_sig = sig
                    if repeat_fail >= MAX_REPEAT_FAILURES + 1:
                        self.stop_reason = "repeat_failure_breaker"
                        break
                    if repeat_fail >= MAX_REPEAT_FAILURES:
                        self.history_manager.add_user_input(
                            "你已连续两次执行相同且失败的操作。请先阅读当前合同 locator，换一种参数或策略；"
                            "如果该处无需修改，请继续处理其他条款或输出 final_answer。")
                else:
                    last_fail_sig, repeat_fail = None, 0
            elif act == "final_answer":
                answer_text = param.get("answer_text", "")
                self.history_manager.add_agent_response(answer_text)
                completed = True
            else:
                self.history_manager.add_user_input(f"未知 act：{act}。act 只能是 use_tool 或 final_answer。")

            if completed:
                self.stop_reason = "completed"
                break

        self.outline_manager.save_outline()
        history_name = f"{self.agent_name}_{self.outline_name}.json" if self.outline_name else f"{self.agent_name}.json"
        try:
            self.history_manager.save_history(paths.memory_dir() / history_name)
        except Exception as e:  # 记忆落盘失败不影响主流程
            print(f"[{self.agent_name}] 保存记忆失败：{e}", file=sys.stderr)
        return answer_text if answer_text else "执行结束"
