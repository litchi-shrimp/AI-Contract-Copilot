import os
import json
import textwrap
from ..core.base_agent import BaseAgent, AGENTS_DIR, AGENT_DIR, BASE_DIR


# 定义每次输出格式
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "matched_result": {"type": "object","description": "本次用户输入匹配到的字段结果（如{\"contract_type\":\"技术服务合同\"}），无匹配则为空"},
        "collected": {"type": "object","description": "当前所有已收集的核心字段（需同步当前已收集的所有有效字段），如果字段暂未收集到则为\"None\"",
                       "properties": {
                           "contract_type": {"type": "string","enum": ["xxx","None"]},
                           "party_type": {"type": "string","enum": ["xxx","None"]},
                           "region": {"type": "string","enum": ["xxx","None"]},
                           "complexity": {"type": "string","enum": ["xxx","None"]},
                           "scene": {"type": "string","enum": ["xxx","None"]}
                       }
                      },
        "is_stop": {"type": "boolean","description": "当前是否字段全部收集完毕，并且用户确认无误（停止对话）"},
        "new_extra_need": {"type": "string","description": "当前用户新的额外需求（如\"需要包含合同条款\"），无新的需求则为空字符串"},
        "response": {"type": "string","description": "仅用于展示给用户的内容（问题、总结、提示等），自然口语化，无多余格式"}
    },
    "required": ["matched_result", "collected", "is_stop", "new_extra_need", "response"]
}


def get_leaf_categories(data, parent_key="", result=None):
    """
    递归获取 JSON 中最小级别的分类项（叶子节点）
    
    Args:
        data: 当前处理的字典
        parent_key: 父级键名（用于追踪路径）
        result: 存储结果的列表
    
    Returns:
        list: 叶子节点列表，每项包含路径和ID
    """
    if result is None:
        result = []
    
    for key, value in data.items():
        current_path = f"{parent_key}.{key}" if parent_key else key
        
        # 如果值是字典，继续递归
        if isinstance(value, dict):
            # 检查是否是叶子节点（只有ID，没有子分类）
            if "ID" in value and len(value) == 1:
                # 是最小级别分类
                result.append({
                    "path": current_path,
                    "name": key,
                    "ID": value["ID"]
                })
            else:
                # 还有子分类，继续递归
                get_leaf_categories(value, current_path, result)
    
    return result

def get_all_classify():
    """全部叶子分类路径（数据源：TemplateRepository）"""
    from ..repository import get_template_repository
    data = get_template_repository().classify_tree()
    return [item['path'] for item in get_leaf_categories(data)]
# ====================== 【核心字段】======================
REQUIRED_FIELDS = [
    "contract_type",
    "party_type",
    "region",
    "complexity",
    "scene"
]

# ===================== 加载动态枚举 =====================
def load_enum_from_template_library():
    """模板库中各元数据字段的取值（供 LLM 参考的枚举）"""
    from ..repository import get_template_repository
    try:
        return get_template_repository().enum_values(("party_type", "region", "complexity", "scene"))
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 加载模板枚举失败: {e}")
        return {k: [] for k in ("party_type", "region", "complexity", "scene")}# -------------------------- Agent核心类（含模糊匹配+二次反问逻辑）--------------------------
class UserFieldExtractionAgent(BaseAgent):
    """用户需求提取Agent"""
    # 这里定义了 Agent 的流事件映射，用于处理不同类型的事件，便于后续实时解析JSON实现同时流式输出不同类型的内容
    # 后续每个Agent都需要制作一个对应的STREAM_EVENT_MAP，不然无法通过KeyPathTracker实时解析不同类型的事件，也就无法SSE流式输出不同类型的内容。
    # 这里的 ("response",) 括号层级代表schema中的字段层级。
    STREAM_EVENT_MAP = {
        ("response",): "agent.answer.stream",
    }

    def __init__(self, agent_name: str):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_user_need_extraction.json")
        
        # 加载业务数据（合同类型、枚举）
        self.contract_types_list = get_all_classify()   # 原始合同类型列表
        self.enum_str = load_enum_from_template_library()  # 加载枚举示例数据
        
        # 初始化状态
        self.collected = {}  # 已提取字段（key：字段名，value：字段值）
        self.missing = REQUIRED_FIELDS.copy()  # 待提取字段
        self.extra_need = []  # 用户额外需求（如"需要包含合同条款"），无需求则为空列表
        self.summary = "" #最后问询完毕后调用LLM填充摘要字段


    def _generate_system_prompt(self):
        """生成LLM系统提示模板（含模糊匹配+二次反问规则，动态替换业务数据）"""
        return textwrap.dedent(f"""
        你是一个专业的信息提取助手，负责通过自然多轮对话，逐步提取用户的5个核心字段：contract_type（合同类型）、party_type（参与方类型）、region（适用地区）、complexity（合同复杂度）、scene（使用场景）。
        请严格遵循以下所有规则，不得违反任何一条：

        ### 核心规则（模糊匹配+二次反问，重点）
        1.  模糊匹配规则：用户回答不精准、不具体、有歧义时，必须主动进行模糊匹配，优先匹配业务数据（contract_type严格匹配合同类型列表，其他字段可以参考“可参考枚举”），匹配逻辑如下：
            1.1  合同类型（contract_type）：用户输入包含关键词（如“服务”“买卖”“租赁”），立即关联合同类型列表中含该关键词的所有类型直接确认最相似的(如果没有，禁止强制对齐，类型定为"ELSE")；若用户输入与列表中多个类型匹配（如输入“服务”，匹配“技术服务合同”“咨询服务合同”），需列出所有匹配项，让用户选择；
            1.2  其他4个字段（party_type/region/complexity/scene）：用户输入不精准（如complexity输入“中等”，scene输入“线上做事”），优先匹配“可参考枚举”中语义最接近的选项；
            1.3  匹配优先级：先精准匹配 → 再模糊匹配（关键词、语义） → 若无法匹配（无相关关键词、语义差异大），则友好提示业务数据范围，重新反问，不擅自猜测。

        2.  二次反问规则：
            2.1  若模糊匹配后仍不确定（如用户输入“服务”，但合同类型中只有“技术服务合同”，仍需反问确认：“你说的服务类合同，是否是‘技术服务合同’？”）；
            2.2  若用户回答过于模糊（如“随便”“都行”“不清楚”），不跳过该字段，需补充提示（结合业务数据），重新反问，直至获取有效信息；
            2.3  二次反问需自然，不重复之前的提问话术，补充更多引导信息（如之前问“请问合同类型？”，二次反问可问“请问你需要哪种合同呀？目前支持买卖合同、技术服务合同等，你可以说说关键词”）。

        ### 基础交互规则
        3.  字段提取规则：
            3.1  合同类型（contract_type）必须从给定的合同类型列表中匹配，不可接受列表外的类型；若用户输入列表外的类型，需提示列表中的所有可选类型，重新反问；若没有找到与用户输入最相似的合同类型，需提示用户后续需要详细描述需求，并将contract_type设为"ELSE"；
            3.2  其他4个字段可参考“可参考枚举”，用户输入超出枚举范围时，友好提示枚举选项，同时允许用户自定义输入（需确认用户是否坚持自定义，不强制拒绝）；
            3.3  解析用户输入时，需精准提取字段值，若用户一句话回答多个字段（如“我要一份北京的企业对企业的服务合同”），需同时提取所有相关字段，更新对应信息，无需重复询问。

        4.  提问规则：
            4.1  每次对话只问一个缺失的字段，不一次性问多个；
            4.2  问题需自然、口语化，符合真人对话逻辑，不使用“字段”“枚举”等技术术语，不使用生硬模板（如不说“请输入region字段”，要说“请问这份合同适用于哪个地区呀？”）；
            4.3  优先询问缺失字段中与当前上下文关联度高的（如用户先提及“线上合作”，优先问scene字段），无关联则按“contract_type → party_type → region → complexity → scene”顺序询问；
            4.4  问题简洁不冗余，每次只输出一个问题（反问用户），不输出任何多余内容（不解释规则、不总结已收集信息）；只有当所有字段收集完成，才输出总结结果。
                               
        ### 关键提问与解释规则
            5.  每次只问一个缺失字段，按顺序：contract_type → party_type → region → complexity → scene。
            6.  如果用户反问/不懂某个字段是什么意思（例如“使用场景是什么”“复杂度指什么”），**必须先通俗解释该字段**，再重新提问，不直接继续反问。
            7.  解释要简单口语化，例如：
                - scene（使用场景）：这份合同用来做什么，比如居住、办公、买卖货物、提供服务、仲裁等。
                - complexity（复杂度）：简单/标准/复杂，代表条款多少、是否需要细致权责约定。
            8.  解释后，再自然抛出问题，不要生硬重复原问题。

        ### 异常处理规则
        9.   答非所问：用户回答与当前提问无关，不跳转字段，重新提问，语气友好，补充提示（结合业务数据）；
        10.  字段修改：用户想修改已回答的字段（如“刚才说的合同类型错了”），及时更新已收集字段，无需重新询问其他字段，继续问当前缺失的字段；
        11.  空值处理：用户未回答某字段（如“不知道”“暂时不确定”），进一步引导，说明该字段的必要性，重新反问，不可留空。

        ### 输出规则
        12.  未收集完所有字段时，仅在response中输出一个反问问题，无任何多余文字；
        13.  所有字段收集完成后，仅在response中输出标准化总结（自然语言+结构化列表），包含所有5个字段，末尾询问用户“以上信息是否准确？若有修改，可直接告知”；
        14.  若用户确认结果后，is_stop设置为True，结束对话。不输出任何内容
        
        ### 业务数据
        - 【合同类型列表】：{str(self.contract_types_list)}
        - 【可参考枚举】：{self.enum_str}
        - 【当前已收集字段】：{self.collected}
        - 【当前待提取字段】：{self.missing}

        输出必须严格遵循以下Schema：
    """ )+ json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    
    def _generate_user_prompt(self):
        """动态生成用户提示（结合对话历史+最新回答）"""
        return textwrap.dedent(f""""
            - 请结合业务数据（合同类型列表、枚举示例），对我的最新回答进行解析、模糊匹配。
            - 依次遍历每个缺失字段，尽可能根据用户需求，同时提取每一个对应的最匹配的字段值，最好一次性完成，如若实在有不确定的字段，后续再反问用户。
            - 同时尽可能提取用户的额外需求（如“需要包含合同条款”），如果提取到额外需求并且当前历史extra_need中未包含该需求，就将其记录在new_extra_need字段中（不重复）；若无需求则为空字符串；
            - 若提取成功，在response中构造下一个缺失字段的反问问题。response仅输出问题（或二次反问），无多余内容。 
            - 若未提取有效字段，直接在response中输出用户最新回答，无任何解释。
            - 如果识别到用户有修改已回答的字段，及时更新已收集字段，无需重新询问其他字段，继续问当前缺失的字段。
            - 最后，如果判断当前所有字段已经收集完成，务必要和用户先确认所有字段及额外需求，用户确认后再停止（is_stop=True）。
            - 只输出JSON，严格遵循Schema，无多余内容。
        """).strip()

    def _parse_llm_response(self, llm_response, user_input=None):
        """解析LLM响应，提取字段，更新状态（区分“问题”和“总结结果”）"""
        # Step1：拿到LLM的JSON
        try:
            data = self.response_parser.parse_agent_response(llm_response)
            if "error" in data:
                return False, "question","遇到未知错误，请重新输入"  # 非JSON格式，直接标记为问题
        except json.JSONDecodeError:
            return False, "question","遇到未知错误，请重新输入"  # 非JSON格式，直接标记为问题
        
        # 校验：contract_type 必须在合同类型列表中，若不在，将错误信息返回，外部添加到observation中重试LLM
        collected = data.get("collected", {})
        if collected:
            ct = collected.get("contract_type", "")
            if ct and ct != "None" and ct != "ELSE" and ct not in self.contract_types_list:
                error_msg = (
                    f"contract_type \"{ct}\" 不在可选合同类型列表中，请重新匹配。\n"
                    f"可选合同类型列表：\n" + "\n".join(f"  - {t}" for t in self.contract_types_list)
                )
                return False, "retry", error_msg
        
        # Step2：检查是否有额外需求，记录到self.extra_need
        new_need = data.get("new_extra_need")
        if isinstance(new_need, list):
            new_need = "；".join(str(x) for x in new_need if x)
        new_need = str(new_need or "").strip()
        if new_need and new_need not in ("None", "无", "null") and new_need not in self.extra_need:
            self.extra_need.append(new_need)  # 记录额外需求（过滤空值 / None，防止下游 join 失败）
        # Step3：修改当前字段
        if data.get("collected"):
            self.collected = data.get("collected")
            # 修改missing字段
            for field in self.collected:
                if field in self.missing and self.collected[field]!="None": self.missing.remove(field)
        

        # Step3：检查是否结束对话（所有字段收集完成+用户确认），如果结束就停止，否则继续提问
        if data.get("is_stop")==True and len(self.missing)==0:
            return True, "complete",data.get("response")  # 标记为收集完成
        else:
            return False, "question",data.get("response")  # 标记为继续提问

    def _generate_summary(self):
        """根据对话历史生成摘要"""
        example_template_summary = textwrap.dedent(f"""
                本合同为一份特定活动服务合同，适用于甲方委托乙方策划并执行2026年新春客户活动的场景。
                签约双方中，甲方为委托方，乙方为服务提供方。核心业务约定为乙方需提供从活动整体策划、视觉设计、物料制作到现场搭建、人员协调及最终执行的全流程服务。
                重要特殊条款包括乙方需承担其工作人员人身安全责任，以及若其整改超过约定次数仍不符合要求，甲方有权单方解约并追索费用。
                关键法律约定着重于乙方的违约责任，包括逾期交付、服务质量不合格及违法违规行为导致的违约金、赔偿及甲方单方解除权，并约定争议由甲方所在地法院管辖。
                合同复杂度中等，条款较为详尽，主要适用于中国大陆地区。
                """).strip()
        system_prompt = textwrap.dedent("""
                你是一名专业的合同需求分析师。你的任务是根据用户对话历史、已收集的合同字段，生成一份**与合同模板摘要同构**的用户需求摘要。
                规则如下：
                1. 输出必须是一段连贯、通顺的纯文本摘要，**结构、表述方式等要尽量一致**，以便后续向量检索。
                2. 必须覆盖以下维度：
                    - 需求的合同类型
                    - 签约双方身份/关系
                    - 适用地区
                    - 使用场景与目的
                    - 合同复杂度偏好
                    - 用户明确提出的核心诉求
                    - 用户期望的关键条款
                3. 能从对话或已收集字段中推断的，务必合理推断；
                4. 实在无法推断的内容，例如没有明确诉求、没有指定条款，统一使用话术：
                    - 无明确诉求，使用通用版本即可
                    - 无指定特殊条款，采用标准条款即可
                5. 语言正式、简洁、客观，长度控制在100–180字。
                6. 只输出摘要文本，不输出任何解释、标题、列表。
            """).strip()
        user_prompt = textwrap.dedent(f"""
                根据以下信息生成用户需求摘要：
                【用户对话历史】
                {self.history_manager.get_history()}
                【已收集的用户信息】
                {self.collected}
                【示例模板摘要(仅示例，和用户需求无关)】
                {example_template_summary}
                请生成用户需求摘要。
            """).strip()
        llm_response = self.llm_call(system_prompt, user_prompt)
        if not llm_response:
            print("❌ 生成摘要失败，请重试")
            return ""
        return llm_response.strip()

    @staticmethod
    def original_request(need: dict) -> str:
        """用户原话（所有 user 轮次拼接）：摘要由 LLM 改写，可能丢失具体数值，下游必须同时参考原话"""
        turns = [str(h.get("content", "")) for h in (need or {}).get("history", []) or [] if h.get("role") == "user"]
        return "\n".join(t for t in turns if t.strip())[:4000]

    @staticmethod
    def normalize_need(need: dict) -> dict:
        """需求结构规范化（供流水线在 Step1 结束及恢复时调用）：extra_need 只保留非空字符串，collected 值转字符串"""
        need = dict(need or {})
        need["extra_need"] = [str(x).strip() for x in (need.get("extra_need") or [])
                              if x is not None and str(x).strip() and str(x).strip() not in ("None", "null")]
        need["collected"] = {k: ("" if v is None else str(v)) for k, v in (need.get("collected") or {}).items()}
        need["summary"] = str(need.get("summary") or "")
        if not need.get("original_request"):
            need["original_request"] = UserFieldExtractionAgent.original_request(need)
        return need

    def get_result(self):
        """返回当前智能体收集到的需求"""
        # 收集history, extra_need, collected
        result = {
            "history": self.history_manager.get_history(),
            "extra_need": self.extra_need,
            "collected": self.collected,
            "summary": self.summary
        }
        return result

    OPENING = textwrap.dedent("""
        您好，我将帮您收集合同相关信息。为了更精准地匹配合同方案，您可以把需求一次性告诉我：
        1. 您的身份？
        2. 需要什么类型的合同（比如：劳动合同，技术服务等）
        3. 签约双方是个人还是企业（比如：个人-个人，个人-企业，企业-企业等）
        4. 适用地区（比如：“通用”或者特定地区）
        5. 合同复杂程度（比如：简单，标准，复杂等）
        6. 使用场景（比如：居住，仲裁等）
        7. 其他详细需求（比如：需要包含特殊合同条款，以及您个性化需求）
        注：以上若有疑问，直接询问即可。
    """).strip()

    CONFIRM_WORDS = ('退出', 'quit', 'exit', '没问题', '确认')

    # ------------------------------------------------------------ 可序列化状态（检查点）
    def export_state(self) -> dict:
        return {"collected": self.collected, "missing": self.missing, "extra_need": self.extra_need,
                "history": self.history_manager.get_history(), "turns": getattr(self, "turns", 0)}

    def load_state(self, state: dict):
        if not state:
            return
        self.collected = state.get("collected", {}) or {}
        self.missing = state.get("missing", REQUIRED_FIELDS.copy())
        self.extra_need = state.get("extra_need", []) or []
        self.history_manager.history = list(state.get("history", []) or [])
        self.turns = int(state.get("turns", 0) or 0)

    def opening(self) -> str:
        if not self.history_manager.get_history():
            self.history_manager.add_agent_response(self.OPENING)
        return self.OPENING

    def turn(self, user_input: str):
        """处理一轮用户输入 → (是否收集完成, 回复文本)

        幂等性：本函数只修改内存状态，调用方在成功返回后再持久化检查点；
        中途取消/失败时调用方丢弃本轮状态，从上一个检查点恢复即可重放。
        """
        self.turns = getattr(self, "turns", 0) + 1
        user_input = str(user_input or "").strip()
        if not user_input:
            return False, "请输入有效内容～"
        if user_input.lower() in self.CONFIRM_WORDS and not self.missing:
            return True, ""
        self.history_manager.add_user_input(user_input)
        if self.turns > self.config.max_loop:
            # 轮数上限：不再追问，用已收集信息继续（缺失字段在后续步骤以通用方式处理）
            return True, "信息已基本收集，我将基于现有信息为您生成合同。"
        system_prompt = self._generate_system_prompt()
        user_prompt = ("【生成要求】\n" + self._generate_user_prompt() + "\n【历史对话】\n"
                       + str(self.history_manager.get_history()[:-1]) + "\n【当前用户回答】\n"
                       + str(self.history_manager.get_history()[-1]))
        is_complete, status, real_response = False, None, ""
        for _ in range(2):
            llm_response = self.llm_call(system_prompt, user_prompt)
            is_complete, status, real_response = self._parse_llm_response(llm_response, user_input)
            if status != "retry":
                break
            user_prompt += "\n【校验反馈】\n" + real_response
        if status == "retry":
            # contract_type 校验仍失败：标记为 ELSE，后续走零模板对抗生成（降级，不阻塞用户）
            self.collected["contract_type"] = "ELSE"
            if "contract_type" in self.missing:
                self.missing.remove("contract_type")
            real_response = real_response.split("\n")[0]
            return (len(self.missing) == 0), "暂未在模板库中找到完全对应的合同类型，我会根据您的描述定制生成。"
        if is_complete and status == "complete":
            self.history_manager.add_agent_response(real_response or "")
            return True, real_response or ""
        self.history_manager.add_agent_response(real_response)
        return False, real_response

    def finalize(self) -> dict:
        """收集结束：生成需求摘要（失败时降级为拼接字段，保证下游可用）"""
        try:
            self.summary = self._generate_summary()
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "RunCancelled":
                raise
            self.summary = ""
        if not self.summary:
            c = self.collected or {}
            self.summary = (f"用户需要一份{c.get('contract_type', '合同')}，签约主体为{c.get('party_type', '未指定')}，"
                            f"适用地区{c.get('region', '通用')}，复杂度{c.get('complexity', '标准')}，"
                            f"使用场景为{c.get('scene', '通用')}。额外需求：{'；'.join(self.extra_need) or '无'}。")
        for k in REQUIRED_FIELDS:
            v = self.collected.get(k)
            if not v or v == "None":
                self.collected[k] = {"region": "通用", "complexity": "标准"}.get(k, "未指定")
        self.history_manager.add_agent_response("【用户需求摘要】\n" + self.summary)
        from ..runtime import paths
        self.history_manager.save_history(paths.memory_dir() / f"{self.agent_name}.json")
        return self.get_result()

    def start_conversation(self):
        """CLI 交互模式（命令行 input()）"""
        print(self.opening())
        while True:
            user_input = str(input("👤 用户：")).strip()
            done, reply = self.turn(user_input)
            if done:
                break
            print(f"🤖 Agent：{reply}")
        return self.finalize()
# -------------------------- 启动Agent --------------------------
if __name__ == "__main__":
    try:
        agent = UserFieldExtractionAgent("UserFieldExtractionAgent")
        result = agent.start_conversation()
        print(result.get("collected"))
        print(result.get("extra_need"))
        # 保存结果到文件
        output_path = AGENT_DIR / "data" / "user_need_summary.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"结果已保存到: {output_path}")
    except Exception as e:
        print(f"❌ Agent运行异常：{str(e)}")
