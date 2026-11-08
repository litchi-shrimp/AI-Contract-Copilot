#!/usr/bin/env python3
"""
LeaderAgent：聚合审查结果并执行合同修订（Plan-Execute）
"""
import json
import re
import sys
from typing import Dict, Any, List, Tuple
from pathlib import Path

from ..llm.prompt_builder import LeaderAgentPrompt
from ..utils.tool_manager import ToolManager
from ..utils.outline_manager import OutlineManager
from ..core.base_agent import BaseAgent, AGENTS_DIR, AGENT_DIR, BASE_DIR


class LeaderAgent(BaseAgent):
    """汇总审查结果并修改 initial_contract 的主控 Agent"""

    # 这里定义了 Agent 的流事件映射，用于处理不同类型的事件，便于后续实时解析JSON实现同时流式输出不同类型的内容
    STREAM_EVENT_MAP = {
        ("strategy_summary",): "agent.think.stream",
        ("decisions",): "agent.act.stream",
    }

    def __init__(self, agent_name: str, contract_name: str = "initial_contract.json",
                 contract_data: dict = None, reviews: dict = None, user_need: dict = None):
        """初始化LeaderAgent
        
        Args:
            agent_name: Agent名称
            contract_name: 合同文件名，默认为"initial_contract.json"
            contract_data: 合同数据字典（内存模式，优先于 contract_name 读文件）
            reviews: 审查结果字典（内存模式，优先于从文件读）
        """
        super().__init__(agent_name, BASE_DIR / "configs" / "config_leader.json")
        
        from ..runtime import paths as _paths
        self.paths_outputs = _paths.outputs_dir()
        self.paths_memory = _paths.memory_dir()
        self.contract_path = self.paths_outputs / contract_name
        self.match_result_path = _paths.data_dir() / "match_result.json"
        self.tool_list_path = AGENTS_DIR / "leader_agent_tools.json"

        # 审查结果文件路径映射（文件模式兜底）
        self.review_paths = {
            "ReviewCompletenessAgent": self.paths_outputs / "ReviewCompletenessAgent.json",
            "ReviewConsistencyAgent": self.paths_outputs / "ReviewConsistencyAgent.json",
            "ReviewLegalAgent": self.paths_outputs / "ReviewLegalAgent.json",
            "ReviewUsageAgent": self.paths_outputs / "ReviewUsageAgent.json",
        }

        # 内存模式：存储外部传入的审查结果
        self._reviews = reviews

        self.tools = json.load(open(self.tool_list_path, "r", encoding="utf-8"))
        # 内存模式优先
        if contract_data is not None:
            self.outline_manager = OutlineManager(data=contract_data)
        else:
            self.outline_manager = OutlineManager(self.contract_path, self.match_result_path)
        self.prompt_builder = LeaderAgentPrompt(self.outline_manager, self.tools)
        self.prompt_builder.user_need = user_need
        self.tool_manager = ToolManager(self.outline_manager, tools_json=self.tools)
        self.harness = self.tool_manager.harness
        self.harness.agent_name = agent_name

        self.plan_output_path = self.paths_outputs / "LeaderPlan.json"
        self.report_output_path = self.paths_outputs / "LeaderExecutionReport.json"

    def _safe_load_json(self, path: Path) -> Dict[str, Any]:
        """安全加载JSON文件，处理文件不存在或解析错误的情况
        
        Args:
            path: JSON文件路径
            
        Returns:
            加载的JSON数据，或包含错误信息的字典
        """
        if not path.exists():
            return {"review_agent_name": path.stem, "problems": [], "load_error": f"文件不存在: {path.name}"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"review_agent_name": path.stem, "problems": [], "load_error": str(e)}

    def _load_all_reviews(self) -> Dict[str, Any]:
        """加载所有审查结果（内存优先，文件兜底）
        
        Returns:
            所有审查结果的字典
        """
        if self._reviews is not None:
            return self._reviews
        reviews = {}
        for name, path in self.review_paths.items():
            reviews[name] = self._safe_load_json(path)
        return reviews

    def _extract_problem_ids(self, text: str) -> List[str]:
        """从之前审查结果的文本中提取问题ID
        
        Args:
            text: 包含问题ID的文本
            
        Returns:
            问题ID列表
        """
        if not text:
            return []
        # 匹配不同类型的问题ID
        return re.findall(r"(risk_\d+|consistency_\d+|legal_\d+|requirement_\d+)", text)

    def _extract_locators(self, text: str) -> List[str]:
        """从之前审查结果的文本中提取定位器（章节编号）
        
        Args:
            text: 包含定位器的文本
            
        Returns:
            去重后的定位器列表
        """
        if not text:
            return []
        # 匹配数字形式的定位器（如1, 1.1, 1.1.1等）
        matches = re.findall(r"\b\d+(?:\.\d+){0,3}\b", text)
        # 去重并保序
        seen = set()
        locators = []
        for item in matches:
            if item not in seen:
                seen.add(item)
                locators.append(item)
        return locators

    def _generate_plan(self, review_payload: Dict[str, Any]) -> Dict[str, Any]:
        """生成修订计划（结构化输出 + 校验 + 自修复）"""
        from ..runtime.llm_gateway import call_llm_json
        allowed = {t.get("name") for t in self.tools}

        def _validate(plan):
            if not isinstance(plan, dict) or not isinstance(plan.get("decisions", None), list):
                return "必须包含 decisions 数组"
            for i, d in enumerate(plan["decisions"]):
                if not isinstance(d, dict):
                    return f"decisions[{i}] 必须是对象"
                if d.get("tool_name") not in allowed:
                    return f"decisions[{i}].tool_name={d.get('tool_name')} 不在可用工具 {sorted(allowed)} 中"
                if not isinstance(d.get("tool_params"), dict) or not d.get("tool_params"):
                    return f"decisions[{i}].tool_params 必须是非空对象"
            return None

        system_prompt = self.prompt_builder.build_plan_system_prompt()
        user_prompt = self.prompt_builder.build_plan_user_prompt(review_payload)
        self.history_manager.add_user_input(user_prompt)
        try:
            parsed = call_llm_json(system_prompt, str(self.history_manager.get_history()), self.config.settings,
                                   validator=_validate, repair_attempts=2, agent=self.agent_name,
                                   step=self._step, stream_event_map=self.STREAM_EVENT_MAP)
        except Exception as e:  # noqa: BLE001  计划失败 → 降级为“不修订”，由上层留痕
            return {"error": f"{type(e).__name__}: {e}"}
        parsed.setdefault("deferred_items", [])
        self.history_manager.add_agent_response(json.dumps(parsed, ensure_ascii=False)[:6000])
        return parsed
    def _locator_sort_key(self, tool_params: Dict[str, Any]) -> tuple:
        """从 insert_clause 的参数中提取 locator 并转为可排序的元组
        
        支持：
        - 单个：{"locator": "5.1"} → (5, 1)
        - 批量：{"locators": ["5.1", "6.2"]} → (6, 2)（取最大的）
        """
        locator = tool_params.get("locator", "")
        locators = tool_params.get("locators", [])
        if locators:
            max_parts = []
            for l in locators:
                parts = [int(x) if x.isdigit() else 0 for x in str(l).split(".")]
                max_parts.append(parts)
            max_parts.sort()
            return tuple(max_parts[-1]) if max_parts else (0,)
        if locator:
            return tuple(int(x) if x.isdigit() else 0 for x in str(locator).split("."))
        return (0,)

    def _execute_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """执行修订计划
        
        Args:
            plan: 修订计划
            
        Returns:
            执行报告
        """
        # 初始化执行报告
        report = {
            "leader_agent_name": self.agent_name,
            "contract_path": str(self.contract_path),
            "executed_actions": [],
            "failed_actions": [],
            "deferred_items": plan.get("deferred_items", []),
            "summary": ""
        }

        decisions = plan.get("decisions", [])
        # 对 insert_clause 按 locator 降序排列，避免顺序插入导致编号错乱
        insert_decisions = [d for d in decisions if d.get("tool_name") == "insert_clause"]
        other_decisions = [d for d in decisions if d.get("tool_name") != "insert_clause"]
        insert_decisions.sort(key=lambda d: self._locator_sort_key(d.get("tool_params", {})), reverse=True)
        decisions = other_decisions + insert_decisions

        # 执行每个决策
        for decision in decisions:
            decision_id = decision.get("decision_id", "")
            tool_name = decision.get("tool_name", "")
            tool_params = decision.get("tool_params", {})

            # 检查工具名称是否存在
            if not tool_name:
                report["failed_actions"].append({
                    "decision_id": decision_id,
                    "reason": "缺少 tool_name"
                })
                continue

            # 检查工具参数是否存在
            if not tool_params:
                report["failed_actions"].append({
                    "decision_id": decision_id,
                    "reason": "缺少 tool_params"
                })
                continue

            # 经 Harness 执行（白名单 / 参数校验 / 事务回滚 / 审计）
            self.check_cancelled()
            result = self.harness.execute(tool_name, tool_params)
            rec = self.harness.records[-1] if self.harness.records else None

            # 检查执行结果
            if rec is not None and rec.status in ("rejected", "error", "timeout"):
                report["failed_actions"].append({
                    "decision_id": decision_id,
                    "tool_name": tool_name,
                    "tool_params": tool_params,
                    "result": result
                })
            else:
                report["executed_actions"].append({
                    "decision_id": decision_id,
                    "tool_name": tool_name,
                    "tool_params": tool_params,
                    "result": result
                })

        # 生成执行摘要
        report["summary"] = (
            f"执行完成：成功 {len(report['executed_actions'])} 项，"
            f"失败 {len(report['failed_actions'])} 项，"
            f"暂缓 {len(report['deferred_items'])} 项。"
        )
        return report

    def _save_json(self, path: Path, data: Dict[str, Any]):
        from ..runtime.paths import atomic_write_json
        try:
            atomic_write_json(path, data)
        except Exception as e:  # 报告落盘失败不影响主流程
            print(f"[LeaderAgent] 写入 {path} 失败：{e}", file=sys.stderr)

    def run(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """运行 LeaderAgent：汇总审查 → 生成计划 → 执行计划

        Returns:
            (执行报告, 修订后的合同)。任何分支都返回二元组；计划失败时合同保持不变（降级）。
        """
        all_reviews = self._load_all_reviews()
        total_problems = sum(len(v.get("problems", []) or []) for v in all_reviews.values() if isinstance(v, dict))

        if total_problems == 0:
            result = {"leader_agent_name": self.agent_name, "message": "未检测到可处理的问题，合同保持不变。",
                      "total_review_problems": 0, "executed_actions": [], "failed_actions": [],
                      "deferred_items": []}
            self._save_json(self.report_output_path, result)
            return result, self.outline_manager.get_outline()

        plan = self._generate_plan(all_reviews)
        if "error" in plan:
            fail_result = {"leader_agent_name": self.agent_name, "message": "计划生成失败，合同保持不变（降级）",
                           "error": plan["error"], "degraded": True, "executed_actions": [],
                           "failed_actions": [], "deferred_items": []}
            self._save_json(self.report_output_path, fail_result)
            return fail_result, self.outline_manager.get_outline()

        self._save_json(self.plan_output_path, plan)
        report = self._execute_plan(plan)
        report["plan"] = plan
        report["tool_records"] = self.harness.export()
        self.outline_manager.save_outline()
        self._save_json(self.report_output_path, report)
        contract_data = self.outline_manager.get_outline()
        try:
            from ..runtime.paths import atomic_write_text
            atomic_write_text(self.paths_outputs / "modified_contract_text_review_after.txt",
                              self.outline_manager._format_outline())
        except Exception:
            pass
        self.history_manager.save_history(self.paths_memory / f"{self.agent_name}.json")
        return report, contract_data