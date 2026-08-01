import os
import sys
import threading
from typing import Dict, List, Any, Optional

# 导入工具和大纲管理器
from ..utils.tool_manager import ToolManager
from ..utils.outline_manager import OutlineManager
from ..utils.skill_manager import SkillManager
from ..utils.Session import Session
from ..llm.prompt_builder import OutlineModificationAgentPrompt
from ..core.base_agent import BaseAgent
from ..agents.config import LLMError

import json
import time

MODIFY_TOOLS = ['update_clause', 'insert_clause', 'delete_clause']

from ..core.base_agent import AGENTS_DIR, AGENT_DIR, BASE_DIR

class OutlineModificationAgent(BaseAgent):
    """大纲修改Agent，负责管理和修改合同大纲"""

    # 这里定义了 Agent 的流事件映射，用于处理不同类型的事件，便于后续实时解析JSON实现同时流式输出不同类型的内容
    STREAM_EVENT_MAP = {
        ("think",): "agent.think.stream",
        ("act",): "agent.act.stream",
        ("parameter", "answer_text"): "agent.answer.stream",
        ("parameter", "question"): "agent.answer.stream",
    }

    def __init__(self, agent_name: str, outline_name: str = None, data: dict = None, match_data:list = None):
        """初始化大纲修改Agent

        Args:
            agent_name: Agent名称
            outline_name: 大纲文件名（文件模式）
            data: 大纲数据字典（内存模式，优先于 outline_name 读文件）
        """
        super().__init__(agent_name, BASE_DIR / "configs" / "config_outline_modification.json")
        
        self.outline_path = AGENT_DIR / "data" / outline_name if outline_name else None
        self.match_result_path = AGENT_DIR / "data" / "match_result.json"

        self.skill_list_path = AGENTS_DIR / "outline_modification_agent_skills.json"
        if not match_data:
            self.skill_list_path = AGENTS_DIR / "outline_modification_agent_wotem_skills.json"
        
        self.skills = json.load(open(self.skill_list_path, "r", encoding="utf-8"))
        # 内存模式优先
        if data is not None:
            save_path = self.outline_path or (AGENT_DIR / "data" / f"{agent_name}_outline.json")
            self.outline_manager = OutlineManager(data=data, match_data=match_data, outline_path=save_path)
        else:
            self.outline_manager = OutlineManager(outline_path = self.outline_path, match_result_path = self.match_result_path)
        self.skill_manager = SkillManager(AGENT_DIR / "skills")

        self.session = Session(timeout_seconds=1800)
        
        # 加载技能描述信息
        self.skill_descriptions = {}
        self.skill_names = []
        for skill in self.skills:
            skill_name = skill.get("name")
            self.skill_names.append(skill_name)
            # 优先读取Skill.md前几行获取技能描述，如果出问题就兜底使用JSON中自定义的描述（outline_modification_agent_skills.json中的description）
            description = self.skill_manager.get_skill_description(skill_name)
            if not description:
                description = skill.get("description")
            self.skill_descriptions[skill_name] = description
        
        # 初始工具列表为空，后续根据skill动态加载
        self.tools = []
        self.prompt_builder = OutlineModificationAgentPrompt(self.outline_manager, skill_descriptions=self.skill_descriptions)
        self.tool_manager = ToolManager(self.outline_manager, tools_json=self.tools)
        self.last_modification_time = None
        self._loaded_skills = set()  # 记录已加载的skill，防止重复加载
        self._cancelled = False  # 超时标志，run() 内部检测到后主动退出
        self._timeout_seconds = 200  # 单次 run() 超时时间


    def run(self, user_input: str) -> str:
        """Agent的ReAct中，单轮Loop

        Args:
            user_input: 用户输入

        Returns:
            Agent响应
        """
        system_prompt = self.prompt_builder.build_system_prompt()
        self.history_manager.add_user_input(user_input)
        answer_text = ""
        # 每次新执行都重置超时标志，确保不被前一次超时的残留线程影响
        self._cancelled = False

        for loop in range(self.config.max_loop):
            # 超时检查：主线程已放弃等待
            if self._cancelled:
                answer_text = "执行超时"
                print(f"【超时】检测到超时标志，主动退出", file=sys.stderr)
                break

            # 1. 调用 LLM
            if loop == 0:   #说明此轮用户刚输入完(显性添加用户输入到记忆中，后续都是添加assistant和tool的记忆了)
                response = self.llm_call(system_prompt,
                                         str(self.history_manager.get_history()[:-1]) + "\n 【当前用户问题】\n" + str(
                                             self.history_manager.get_history()[-1]))
            else:
                response = self.llm_call(system_prompt, str(self.history_manager.get_history()))
            # 删除所有检索历史（调用完后就删除，无论是依次检索还是多次检索；多次检索也删除，因为触发多次说明第一次检索效果不理想，为了避免污染上下文也删除。）
            self.history_manager.del_typeall_history("retrieval")
            parsed = self.response_parser.parse_agent_response(response)
            # 清空合同信息类型的记忆
            self.history_manager.del_typeall_history("contract")
            
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

            print(f"\n{'=' * 50}", file=sys.stderr)
            print(f"【思考】{think}", file=sys.stderr)
            self.history_manager.add_agent_response(think)
            # 4. 执行动作
            if act == "answer_user":
                print(f"【答案】{param.get('answer_text', '')}", file=sys.stderr)
                answer_text = param.get("answer_text", "")
                answer_text += "如果没有其他问题，且当前大纲满足您要求，可以回复确认了"
                print(f"{'=' * 50}\n", file=sys.stderr)

            elif act == "ask_user":
                question = param.get("question", "")
                print(f"【追问】{question}", file=sys.stderr)
                print(f"{'=' * 50}\n", file=sys.stderr)
                answer_text = question

            elif act == "load_skill":
                skill_name = param.get("skill_name")
                # 判断skill是否存在
                if skill_name not in self.skill_names:
                    self.history_manager.add_agent_response(response)
                    self.history_manager.add_skill_observation(f"Skill {skill_name} 不存在，无法加载")
                    continue

                # 幂等检查：已加载过的skill直接跳过（这里默认全局只加载一次Skill，没有设置更复杂的定时清理该类型记忆的逻辑）
                if skill_name in self._loaded_skills:
                    print(f"【加载Skill】{skill_name} 已加载，跳过", file=sys.stderr)
                    self.history_manager.add_agent_response(response)
                    self.history_manager.add_skill_observation(f"Skill {skill_name} 已加载，无需重复加载")
                    continue

                print(f"【加载Skill】load_skill(skill_name=\"{skill_name}\")", file=sys.stderr)
                # 加载Skill的SKILL.md文件内容
                skill_content = self.skill_manager.load_skill(skill_name)
                if skill_content:
                    # 尝试加载skill对应的tools.json文件
                    skill_tools = []
                    skill_dir = AGENT_DIR / "skills" / skill_name
                    tools_file = skill_dir / "tools.json"
                    if tools_file.exists():
                        try:
                            with open(tools_file, "r", encoding="utf-8") as f:
                                skill_tools = json.load(f)
                            # 将技能的工具添加到tool_manager
                            for tool in skill_tools:
                                self.tool_manager.add_tool(tool)
                            print(f"【加载工具】成功加载Skill {skill_name} 里的所有工具", file=sys.stderr)
                            # 记录已加载状态
                            self._loaded_skills.add(skill_name)
                        except Exception as e:
                            print(f"【加载工具】加载Skill {skill_name} 的工具失败: {e}", file=sys.stderr)
                    
                    # 将SKILL.md内容和工具schema添加到observation中
                    self.history_manager.add_agent_response(response)
                    self.history_manager.add_skill_observation(f"Skill {skill_name} 全文加载成功")
                    self.history_manager.add_skill_observation(f"Skill内容: {skill_content}")
                    if skill_tools:
                        self.history_manager.add_observation(f"Skill {skill_name} 包含以下工具:")
                        for tool in skill_tools:
                            tool_info = f"- {tool['name']}: {tool['description']}"
                            tool_info += f"\n  参数: {json.dumps(tool['parameters'], ensure_ascii=False)}"
                            self.history_manager.add_tool_observation(tool_info)
                    answer_text = f"Skill {skill_name} 已加载，包含 {len(skill_tools)} 个工具"
                else:
                    self.history_manager.add_agent_response(response)
                    self.history_manager.add_skill_observation(f"加载Skill {skill_name} 失败")
                    answer_text = f"加载Skill {skill_name} 失败"

            elif act == "use_tool":
                tool_name = param.get("tool_name")
                tool_params = param.get("tool_params", {})
                print(
                    f"【调用工具】use_tool(tool_name=\"{tool_name}\", tool_params={json.dumps(tool_params, ensure_ascii=False)})",
                    file=sys.stderr)

                # 执行工具
                result = self.tool_manager.execute_tool(tool_name, tool_params)
                print(f"【工具执行结果】{result}", file=sys.stderr)

                # 记录到历史
                self.history_manager.add_agent_response(response)
                if tool_name == "retrieve_template_reference" or tool_name == "web_search":
                    self.history_manager.add_retrieval_info(result)
                else:
                    self.history_manager.add_tool_observation(f"工具执行结果：{result}")
                self.history_manager.add_contract_info(f"当前合同大纲：\n{self.outline_manager._format_outline()}")
                print(f"{'=' * 50}\n", file=sys.stderr)

                # 将工具执行结果设为 answer_text，避免空消息污染历史
                answer_text = f"工具 {tool_name} 执行完毕：{result}"

                # 如果是修改操作，记录修改时间
                if tool_name in MODIFY_TOOLS:
                    self.last_modification_time = time.time()

            print("【completed】:" + str(completed), file=sys.stderr)
            self.history_manager.add_agent_response(answer_text)
            # 5. 如果完成，退出
            if completed:
                break
        
            if loop == self.config.max_loop-1:
                self.history_manager.add_observation("当前已超过最大循环，请精简逻辑")
        
        return answer_text if answer_text else "执行结束"

    def interactive_run(self) -> tuple:
        """交互式运行Agent

        Returns:
            (user_confirmed, outline_data): 
                user_confirmed: True=用户确认退出，可以进入下一步；False=异常/超时/取消
                outline_data: 最终大纲数据字典
        """
        eb = self.event_bus
        welcome = "合同大纲已为您生成完毕，如果你有任何问题或者疑惑都可以与我交流，如果大纲没有问题，回复确认即可~"
        if eb:
            eb.put_sync("agent.ask", agent=self.agent_name, data={"question": welcome, "expects_input": True})
        else:
            print(f"🤖：{welcome}")
            sys.stdout.flush()

        system_prompt = """
            你是一个专业的用户意图确认助手，你的任务是根据用户的回答确认用户是否确认合同大纲。
            输出严格遵循下面JSON格式：
            {
                "completed": true or false
            }
        """

        user_confirmed = False

        while True:
            try:
                if self.session.is_expired():
                    if eb:
                        eb.put_sync("session.timeout", data={"timeout_seconds": self.session.timeout, "last_active": self.session.last_active})
                    break

                if eb:
                    user_input = str(eb.wait_for_user_input_sync(timeout=self.session.timeout) or "").strip()
                else:
                    user_input = input("\n👤用户：").strip()
                if not user_input:
                    continue
                self.session.refresh()

                try:
                    if user_input.lower() in ['退出', 'quit', 'exit', '没问题', '确认']:
                        user_confirmed = True # 先过一遍简要判断是否确认退出
                        break
                    all_memory = self.history_manager.get_history()
                    # 滑动窗口，仅保留最近10条memory（且type为user和agent），也就是将最近的对话丢给Intent Recognition Agent，判断是否停止了。
                    filter_memory = [item for item in all_memory if item["role"] in ["user", "agent"]][-10:]
                    response = self.llm_call(system_prompt, str(filter_memory)+"\n【当前用户回答】"+user_input)
                    response = self.response_parser.parse_agent_response(response)
                    if response.get("completed") == True:
                        user_confirmed = True
                        break
                except Exception as e:
                    msg = f"⚠️ 意图识别失败（{e}），请重新回答或输入「退出」"
                    if eb:
                        eb.put_sync("system", agent=self.agent_name, data={"message": msg})
                    else:
                        print(msg, file=sys.stderr)
                    continue
                
                # 到这，说明用户没有确认退出输入，继续执行后续流程（回答，修改等）
                # 先保存一个快照，用于后续恢复状态
                outline_snapshot = self.outline_manager.snapshot()
                history_snapshot = self.history_manager.snapshot()
                # 设置定时器，防止Loop内超时
                timer = threading.Timer(self._timeout_seconds, lambda: setattr(self, '_cancelled', True))
                timer.start()
                result = None
                llm_error = None
                # 将self.run 包装成一个可以监控超时的线程，如果超时自动断掉线程
                def run_with_timeout():
                    nonlocal result, llm_error
                    try:
                        result = self.run(user_input)
                    except LLMError as e:
                        llm_error = e
                thread = threading.Thread(target=run_with_timeout)
                thread.start()
                thread.join()
                timer.cancel()
                if llm_error:
                    msg = f"❌ LLM 调用失败（{llm_error}），流程终止"
                    if eb:
                        eb.put_sync("system", agent=self.agent_name, data={"message": msg})
                    else:
                        print(msg, file=sys.stderr)
                    user_confirmed = False
                    break
                if self._cancelled:
                    self._cancelled = False
                    self.outline_manager.restore(outline_snapshot)
                    self.history_manager.restore(history_snapshot)
                    msg = "❌ 执行超时，请简化要求或检查系统状态，或者重新输入"
                    if eb:
                        eb.put_sync("system", agent=self.agent_name, data={"message": msg})
                    else:
                        print(msg, file=sys.stderr)
                    continue

                if eb:
                    # 流式 KeyPathTracker 已自动推送 agent.answer.stream 通过SSE实现流式输出，这里只是将一大段结果直接推送给客户端再显示一次结果而已。
                    eb.put_sync("agent.ask", agent=self.agent_name, data={"question": result, "expects_input": True})
                else:
                    print(f"🤖：{result}")
                    sys.stdout.flush()

                self.outline_manager.save_outline()

            except EOFError:
                user_confirmed = False
                break
            except KeyboardInterrupt:
                user_confirmed = False
                break
            except Exception as e:
                msg = f"❌ 执行过程中出错（{e}）"
                if eb:
                    eb.put_sync("system", agent=self.agent_name, data={"message": msg})
                else:
                    print(msg, file=sys.stderr)
                user_confirmed = False
                break
        
        # 最后用户确认跳出循环后，保存一下最终的合同文本。这里只是默认用户确认再保存，前面中途出错不保存。
        self.outline_manager.save_outline()
        outline_data = self.outline_manager.get_outline()
        if user_confirmed:
            modified_contract_text = self.outline_manager._format_outline()
            with open(AGENT_DIR / "data" / "initial_contract_text.txt", "w", encoding="utf-8") as f:
                f.write(modified_contract_text)
        # 保存一下当前Agent的所有memory（暂时用不到）
        self.history_manager.save_history(AGENT_DIR / "memory" / (f"{self.agent_name}.json"))
        return (user_confirmed, outline_data)
