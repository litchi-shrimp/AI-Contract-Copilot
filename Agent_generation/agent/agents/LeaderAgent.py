#!/usr/bin/env python3
"""
LeaderAgent：聚合审查结果并执行合同修订（Plan-Execute）
"""
import json
import re
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
                 contract_data: dict = None, reviews: dict = None):
        """初始化LeaderAgent
        
        Args:
            agent_name: Agent名称
            contract_name: 合同文件名，默认为"initial_contract.json"
            contract_data: 合同数据字典（内存模式，优先于 contract_name 读文件）
            reviews: 审查结果字典（内存模式，优先于从文件读）
        """
        super().__init__(agent_name, BASE_DIR / "configs" / "config_leader.json")
        
        self.contract_path = BASE_DIR / "outputs" / contract_name
        self.match_result_path = AGENT_DIR / "data" / "match_result.json"
        self.tool_list_path = AGENTS_DIR / "leader_agent_tools.json"

        # 审查结果文件路径映射（文件模式兜底）
        self.review_paths = {
            "ReviewCompletenessAgent": BASE_DIR / "outputs" / "ReviewCompletenessAgent.json",
            "ReviewConsistencyAgent": BASE_DIR / "outputs" / "ReviewConsistencyAgent.json",
            "ReviewLegalAgent": BASE_DIR / "outputs" / "ReviewLegalAgent.json",
            "ReviewUsageAgent": BASE_DIR / "outputs" / "ReviewUsageAgent.json",
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
        self.tool_manager = ToolManager(self.outline_manager, tools_json=self.tools)

        self.plan_output_path = BASE_DIR / "outputs" / "LeaderPlan.json"
        self.report_output_path = BASE_DIR / "outputs" / "LeaderExecutionReport.json"

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
        """生成修订计划
        
        Args:
            review_payload: 审查结果
            
        Returns:
            修订计划
        """
        # 构建系统提示词和用户提示词
        system_prompt = self.prompt_builder.build_plan_system_prompt()
        user_prompt = self.prompt_builder.build_plan_user_prompt(review_payload)
        # 添加用户输入到历史记录
        self.history_manager.add_user_input(user_prompt)
        # 调用LLM生成计划
        response = self.llm_call(system_prompt, str(self.history_manager.get_history()))
        # 解析LLM响应
        parsed = self.response_parser.parse_agent_response(response)
        # 添加Agent响应到历史记录
        self.history_manager.add_agent_response(response)
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
                parts = [int(x) for x in l.split(".")]
                max_parts.append(parts)
            max_parts.sort()
            return tuple(max_parts[-1]) if max_parts else (0,)
        if locator:
            return tuple(int(x) for x in locator.split("."))
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

            # 执行工具
            result = self.tool_manager.execute_tool(tool_name, tool_params)

            # 检查执行结果
            if result.startswith("错误") or result.startswith("执行失败") or "未找到定位标识" in result:
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
        """保存JSON数据到文件
        
        Args:
            path: 文件路径
            data: 要保存的数据
        """
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def run(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """运行LeaderAgent，执行完整的修订流程
        
        Returns:
            执行报告
        """
        # 加载所有审查结果
        all_reviews = self._load_all_reviews()
        # 统计总问题数
        total_problems = sum(len(v.get("problems", [])) for v in all_reviews.values())

        # 如果没有问题，直接返回
        if total_problems == 0:
            result = {
                "leader_agent_name": self.agent_name,
                "message": "未检测到可处理的问题，合同保持不变。",
                "total_review_problems": 0
            }
            self._save_json(self.report_output_path, result)
            return result

        # 生成修订计划
        plan = self._generate_plan(all_reviews)
        # 检查计划生成是否失败
        if "error" in plan:
            fail_result = {
                "leader_agent_name": self.agent_name,
                "message": "计划生成失败",
                "error": plan["error"]
            }
            self._save_json(self.report_output_path, fail_result)
            return fail_result

        # 保存Plan
        self._save_json(self.plan_output_path, plan)
        # 执行Plan
        report = self._execute_plan(plan)
        # 所有操作完成后，统一保存一次合同
        self.outline_manager.save_outline()
        # 保存执行报告
        self._save_json(self.report_output_path, report)
        contract_data = self.outline_manager.get_outline()
        # 最后将合同原文（非结构文本）保存到文件
        modified_text = self.outline_manager._format_outline()
        with open(BASE_DIR / "outputs" / "modified_contract_text_review_after.txt", "w", encoding="utf-8") as f:
            f.write(modified_text)

        # 最后将所有memory保存到文件
        self.history_manager.save_history(AGENT_DIR / "memory" / (f"{self.agent_name}.json"))
        return (report, contract_data)

