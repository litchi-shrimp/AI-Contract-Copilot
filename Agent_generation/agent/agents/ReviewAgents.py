#!/usr/bin/env python3
"""
合同审查Agent集合，从多个维度并行审查合同
"""
import json
from ..llm.prompt_builder import ReviewAgentPrompt
from ..utils.outline_manager import OutlineManager
from ..core.base_agent import BaseAgent, AGENTS_DIR, AGENT_DIR, BASE_DIR


class _BaseReviewAgent(BaseAgent):
    """审查Agent基类，封装公共的初始化与运行逻辑"""

    def __init__(self, agent_name: str, outline_name: str = None, data: dict = None):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_review.json")
        self.outline_path = BASE_DIR / "outputs" / outline_name if outline_name else None
        self.match_result_path = AGENT_DIR / "data" / "match_result.json"
        # 内存模式优先
        if data is not None:
            self.outline_manager = OutlineManager(data=data)
        else:
            self.outline_manager = OutlineManager(self.outline_path, self.match_result_path)
        self.prompt_builder = ReviewAgentPrompt(self.outline_manager, agent_name)

    def _run_with_prompts(self, system_prompt: str, user_prompt: str) -> dict:
        self.history_manager.add_user_input(user_prompt)
        response = self.llm_call(system_prompt, str(self.history_manager.get_history()))
        parsed = self.response_parser.parse_agent_response(response)
        self.history_manager.add_agent_response(response)
        return parsed


class ReviewCompletenessAgent(_BaseReviewAgent):
    """完整性审查"""
    def run(self) -> dict:
        return self._run_with_prompts(
            self.prompt_builder.build_completeness_system_prompt(),
            self.prompt_builder.build_completeness_user_prompt()
        )


class ReviewConsistencyAgent(_BaseReviewAgent):
    """一致性审查"""
    def run(self) -> dict:
        return self._run_with_prompts(
            self.prompt_builder.build_consistency_system_prompt(),
            self.prompt_builder.build_consistency_user_prompt()
        )


class ReviewLegalAgent(_BaseReviewAgent):
    """合法性审查"""
    def run(self) -> dict:
        return self._run_with_prompts(
            self.prompt_builder.build_legal_system_prompt(),
            self.prompt_builder.build_legal_user_prompt()
        )


class ReviewUsageAgent(_BaseReviewAgent):
    """用户需求符合性审查"""
    def __init__(self, agent_name: str, outline_name: str = None, data: dict = None, user_need: dict = None):
        super().__init__(agent_name, outline_name, data=data)
        self._user_need = user_need  # 内存模式
        self.usage_need_path = AGENT_DIR / "data" / "user_need_summary.json"

    def run(self) -> dict:
        if self._user_need is not None:
            usage = json.dumps(self._user_need, ensure_ascii=False)
        else:
            usage = json.dumps(json.load(open(self.usage_need_path, "r", encoding="utf-8")), ensure_ascii=False)
        user_prompt = self.prompt_builder.build_user_user_prompt() + "\n 【当前用户需求】\n" + usage
        return self._run_with_prompts(
            self.prompt_builder.build_user_system_prompt(),
            user_prompt
        )


if __name__ == "__main__":
    agent = ReviewConsistencyAgent("ReviewConsistencyAgent", "contract_outline")
    response = agent.run()
    print(response)