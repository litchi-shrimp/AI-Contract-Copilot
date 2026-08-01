#!/usr/bin/env python3
"""
起草Agent，负责补全合同大纲细节，生成初始完整合同
"""
import json
from typing import Dict, Any, List
import sys
import os
from ..llm.prompt_builder import DrafterAgentPrompt
from ..utils.tool_manager import ToolManager
from ..utils.outline_manager import OutlineManager
from ..core.base_agent import BaseAgent, AGENTS_DIR, AGENT_DIR, BASE_DIR

class DrafterAgent(BaseAgent):
    """补全合同大纲细节，生成初始完整合同"""

    # 这里定义了 Agent 的流事件映射，用于处理不同类型的事件，便于后续实时解析JSON实现同时流式输出不同类型的内容
    STREAM_EVENT_MAP = {
        ("think",): "agent.think.stream",
        ("act",): "agent.act.stream",
        ("parameter", "answer_text"): "agent.answer.stream",
    }

    def __init__(self, agent_name: str, outline_name: str = None, chunk_data: dict = None, match_data:list = None, reference_outline:dict=None,user_need:dict = None):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_initial_contract.json")

        self.outline_name = outline_name or ""
        self.outline_chunk_path = AGENT_DIR / "data" / outline_name if outline_name else None
        self.outline_path = AGENT_DIR / "data" / "initial_outline.json"
        self.match_result_path = AGENT_DIR / "data" / "match_result.json"
        self.tool_list_path = AGENTS_DIR / "drafter_agent_tools.json"
        self.match_data = match_data or None
        self.user_need = user_need or None
        self.have_template = True
        if not match_data:   # 如果之前没有匹配模板，起草补全时不给其配template_retrieval工具，只有增/改/websearch工具
            self.tool_list_path = AGENTS_DIR / "drafter_agent_tools_wo_tem.json"
            self.have_template = False
        
        self.tools = json.load(open(self.tool_list_path, "r", encoding="utf-8"))
        # 内存模式优先
        if chunk_data:
            self.outline_manager = OutlineManager(data=chunk_data,match_data=match_data)
        else:
            self.outline_manager = OutlineManager(outline_path = self.outline_chunk_path, match_result_path = self.match_result_path)

        if reference_outline:
            self.outline_manager2 = OutlineManager(data=reference_outline,match_data=match_data)
        else:
            self.outline_manager2 = OutlineManager(self.outline_path, self.match_result_path)

        self.prompt_builder = DrafterAgentPrompt(self.outline_manager, self.tools,self.outline_manager2,have_tem = self.have_template,user_need = self.user_need)
        self.tool_manager = ToolManager(self.outline_manager, tools_json=self.tools)

    
    def run(self) -> str:
        """运行决策引擎
        Returns:
            Agent响应
        """
        system_prompt = self.prompt_builder.build_system_prompt()
        user_prompt = self.prompt_builder.build_user_prompt()
        self.history_manager.add_user_input(user_prompt)
        answer_text = ""

        for loop in range(self.config.max_loop):
            # 1. 调用 LLM
            response = self.llm_call(system_prompt,str(self.history_manager.get_history()))
            # 删除所有检索历史（调用完后就删除，无论是依次检索还是多次检索；多次检索也删除，因为触发多次说明第一次检索效果不理想，为了避免污染上下文也删除。）
            self.history_manager.del_typeall_history("retrieval")
            parsed = self.response_parser.parse_agent_response(response)

            # 2. 解析失败 → 重试
            if "error" in parsed:
                self.history_manager.add_agent_response(response)
                self.history_manager.add_user_input("输出格式错误，请返回合法JSON")
                continue

            # 3. 记录思考和行动到stderr
            think = parsed.get("think", "")
            act = parsed.get("act")
            param = parsed.get("parameter", {})
            completed = parsed.get("completed", False)

            print(f"\n{'='*50}", file=sys.stderr)
            print(f"【思考】{think}", file=sys.stderr)
            self.history_manager.add_agent_response(think)

            # 4. 执行动作
            if act == "use_tool":
                tool_name = param.get("tool_name")
                tool_params = param.get("tool_params", {})
                print(f"【调用工具】use_tool(tool_name=\"{tool_name}\", tool_params={json.dumps(tool_params, ensure_ascii=False)})", file=sys.stderr)
                # 执行工具
                result = self.tool_manager.execute_tool(tool_name, tool_params)
                print(f"【工具执行结果】{result}", file=sys.stderr)
                # 记录到历史
                if tool_name == "retrieve_template_reference" or tool_name == "web_search":
                    self.history_manager.add_retrieval_info(result)
                else:
                    self.history_manager.add_tool_observation(f"工具执行结果：{result}，新内容：{tool_params.get('content', '')}")
                print(f"{'='*50}\n", file=sys.stderr)
            elif act == "final_answer":
                answer_text = param.get("answer_text", "")
                self.history_manager.add_agent_response(answer_text)
                print(f"【完成结果】{answer_text}", file=sys.stderr)
                print(f"{'='*50}\n", file=sys.stderr)
            
            print("【completed】:"+str(completed),file=sys.stderr )

            # 5. 如果完成，退出
            if completed:
                break
        
        # 所有操作完成后，统一保存一次合同chunk
        self.outline_manager.save_outline()
        
        # 最后将所有memory保存到文件
        history_name = f"{self.agent_name}_{self.outline_name}.json" if self.outline_name else f"{self.agent_name}.json"
        self.history_manager.save_history(AGENT_DIR / "memory" / history_name)
        return answer_text if answer_text else "执行结束"
