#!/usr/bin/env python3
"""
合同审查Agent集合，从多个维度并行审查合同

鲁棒性：
- 结构化输出：JSON 解析 + Schema 校验，失败时把错误反馈给模型自修复（最多 2 次）
- 结果规范化：problem_id 去重补全、problem_level 归一到 高/中/低、截断超长字段
- 全部失败时返回带 degraded 标记的空问题列表（由编排层决定是否降级），而不是抛出导致整步失败
"""
import json

from ..llm.prompt_builder import ReviewAgentPrompt
from ..utils.outline_manager import OutlineManager
from ..core.base_agent import BaseAgent, AGENT_DIR, BASE_DIR
from ..runtime import paths
from ..runtime.llm_gateway import call_llm_json

PREFIX = {
    "ReviewCompletenessAgent": "risk",
    "ReviewConsistencyAgent": "consistency",
    "ReviewLegalAgent": "legal",
    "ReviewUsageAgent": "requirement",
}
LEVELS = {"高": "高", "中": "中", "低": "低", "high": "高", "medium": "中", "low": "低", "严重": "高", "一般": "中"}


def _validate_review(data) -> str:
    if not isinstance(data, dict):
        return "输出必须是 JSON 对象"
    problems = data.get("problems")
    if problems is None:
        return "缺少 problems 字段（无问题时输出空数组）"
    if not isinstance(problems, list):
        return "problems 必须是数组"
    for i, p in enumerate(problems):
        if not isinstance(p, dict):
            return f"problems[{i}] 必须是对象"
        if not str(p.get("problem_description", "")).strip():
            return f"problems[{i}] 缺少 problem_description"
    return None


def normalize_review(agent_name: str, data: dict) -> dict:
    prefix = PREFIX.get(agent_name, "issue")
    out = []
    seen = set()
    for i, p in enumerate(data.get("problems") or [], 1):
        pid = str(p.get("problem_id") or f"{prefix}_{i}")
        if not pid.startswith(prefix):
            pid = f"{prefix}_{i}"
        while pid in seen:
            pid = f"{pid}_{i}"
        seen.add(pid)
        sugg = p.get("suggestions") or []
        if isinstance(sugg, str):
            sugg = [sugg]
        out.append({
            "review_agent_name": agent_name,
            "problem_id": pid,
            "problem_type": str(p.get("problem_type", ""))[:40],
            "problem_level": LEVELS.get(str(p.get("problem_level", "中")).strip().lower(),
                                        LEVELS.get(str(p.get("problem_level", "中")).strip(), "中")),
            "problem_description": str(p.get("problem_description", ""))[:800],
            "impact_scope": str(p.get("impact_scope", ""))[:200],
            "suggestions": [str(s)[:500] for s in sugg][:6],
        })
    return {"review_agent_name": agent_name, "problems": out}


class _BaseReviewAgent(BaseAgent):
    """审查Agent基类，封装公共的初始化与运行逻辑"""

    def __init__(self, agent_name: str, outline_name: str = None, data: dict = None):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_review.json")
        self.outline_path = paths.outputs_dir() / outline_name if outline_name else None
        self.match_result_path = paths.data_dir() / "match_result.json"
        if data is not None:
            self.outline_manager = OutlineManager(data=data)
        else:
            self.outline_manager = OutlineManager(self.outline_path, self.match_result_path)
        self.prompt_builder = ReviewAgentPrompt(self.outline_manager, agent_name)

    def _run_with_prompts(self, system_prompt: str, user_prompt: str) -> dict:
        self.check_cancelled()
        self.history_manager.add_user_input(user_prompt)
        stream_map = getattr(self.__class__, "STREAM_EVENT_MAP", None)
        data = call_llm_json(system_prompt, str(self.history_manager.get_history()), self.config.settings,
                             validator=_validate_review, repair_attempts=2, agent=self.agent_name,
                             step=self._step, stream_event_map=stream_map)
        result = normalize_review(self.agent_name, data)
        self.history_manager.add_agent_response(json.dumps(result, ensure_ascii=False)[:4000])
        return result


class ReviewCompletenessAgent(_BaseReviewAgent):
    """完整性审查"""
    def run(self) -> dict:
        return self._run_with_prompts(self.prompt_builder.build_completeness_system_prompt(),
                                      self.prompt_builder.build_completeness_user_prompt())


class ReviewConsistencyAgent(_BaseReviewAgent):
    """一致性审查"""
    def run(self) -> dict:
        return self._run_with_prompts(self.prompt_builder.build_consistency_system_prompt(),
                                      self.prompt_builder.build_consistency_user_prompt())


class ReviewLegalAgent(_BaseReviewAgent):
    """合法性审查"""
    def run(self) -> dict:
        return self._run_with_prompts(self.prompt_builder.build_legal_system_prompt(),
                                      self.prompt_builder.build_legal_user_prompt())


class ReviewUsageAgent(_BaseReviewAgent):
    """用户需求符合性审查"""
    def __init__(self, agent_name: str, outline_name: str = None, data: dict = None, user_need: dict = None):
        super().__init__(agent_name, outline_name, data=data)
        self._user_need = user_need
        self.usage_need_path = paths.data_dir() / "user_need_summary.json"

    def run(self) -> dict:
        if self._user_need is not None:
            usage = json.dumps(self._user_need, ensure_ascii=False)
        else:
            with open(self.usage_need_path, "r", encoding="utf-8") as f:
                usage = json.dumps(json.load(f), ensure_ascii=False)
        user_prompt = self.prompt_builder.build_user_user_prompt() + "\n 【当前用户需求】\n" + usage
        return self._run_with_prompts(self.prompt_builder.build_user_system_prompt(), user_prompt)


REVIEW_AGENTS = {
    "ReviewConsistencyAgent": ReviewConsistencyAgent,
    "ReviewLegalAgent": ReviewLegalAgent,
    "ReviewUsageAgent": ReviewUsageAgent,
    "ReviewCompletenessAgent": ReviewCompletenessAgent,
}
