import textwrap
import json
from ..core.base_agent import BaseAgent
from pathlib import Path

COMPLEXITY_STANDARD = """
大纲详细程度遵循合同行业标准定义：
- 简单/标准版：仅包含合同必备基础章节与常规场景，不拆分多余子条款，不涉及特殊风险约定。
- 复杂/详细版：在基础结构上，增加风险场景、责任细分、违约情形、特殊约定、约束条款与风控条款，对高风险模块拆分为多级子条款，覆盖更多边缘情况。
"""

MASKRULES = """
【脱敏替换规则（必须严格执行）】
    需要脱敏并替换为占位符的内容包括：
    1. 主体信息：
    - 个人姓名、公司名称、组织名称
    - 统一社会信用代码、税号、注册号
    - 法定代表人、授权代表、负责人
    - 地址、住所、注册地址
    2. 数值与编码：
    - 所有日期：签署日、生效日、到期日、起止日期
    - 所有金额：租金、押金、违约金、总价、预付款
    - 百分比、比例、费率
    - 数量、面积、时长、重量、体积
    - 银行账号、开户行、电话、传真
    - 合同编号、项目编号、订单号、邮编
    3. 脱敏规则：
    - 相同含义 → 相同变量名
    - 不同含义 → 不同变量名
    - 变量名必须语义清晰
    示例：
        张三 → {甲方姓名}
        北京市朝阳区XX路 → {房屋地址}
        5000元 → {月租金金额}
        2025年1月1日 → {合同生效日期}
"""


OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "标准化模板文本": {
            "type": "object",
            "description": "层级结构化合同，支持：章节 → 条款 → 子条款 → 子子条款 → 详细条款（最多3级）",
            "properties": {
                "合同首部": {
                    "type": "string",
                    "description": "标题、当事人、签订信息等（已脱敏）"
                },
                "正文章节": {
                    "type": "array",
                    "description": "所有章节，每章可包含：条款、子条款、子子条款（最多3级）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "章节编号": {"type": "string"},
                            "章节标题": {"type": "string"},
                            "条款列表": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "条款编号": {"type": "string"},
                                        "条款大致说明": {"type":"string","description":"概括本条核心含义，不写全文"},
                                        "子条款列表": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "子条款编号": {"type": "string"},
                                                    "子条款大致说明": {"type": "string","description":"概括本条核心含义，不写全文"},
                                                    "子子条款列表": {
                                                        "type": "array",
                                                        "items": {
                                                            "type": "object",
                                                            "properties": {
                                                                "子子条款编号": {"type": "string"},
                                                                "子子条款大致说明": {"type": "string"}
                                                            },
                                                            "required": ["子子条款编号", "子子条款大致说明"]
                                                        }
                                                    }
                                                },
                                                "required": ["子条款编号", "子条款大致说明"]
                                            }
                                        }
                                    },
                                    "required": ["条款编号", "条款大致说明"]
                                }
                            }
                        },
                        "required": ["章节编号", "章节标题", "条款列表"]
                    }
                },
                "合同尾部": {
                    "type": "string",
                    "description": "当事人、盖章信息等（已脱敏）"
                },
            },
            "required": ["合同首部", "正文章节", "合同尾部"]
        },
        "思考": {
            "type": "string",
            "description": "思考为了符合用户需求而生成大纲的要求"
        }
    },
    "required": ["标准化模板文本", "思考"]
}

# 路径设置（从 base_agent 统一导入）
from ..core.base_agent import AGENTS_DIR, AGENT_DIR, BASE_DIR

# ==================== 核心：OutlineGenerationAgent 类====================
class OutlineGenerationAgent(BaseAgent):
    """大纲生成Agent（有模板）"""
    # 这里定义了 Agent 的流事件映射，用于处理不同类型的事件，便于后续实时解析JSON实现同时流式输出不同类型的内容
    STREAM_EVENT_MAP = {
        ("思考",): "agent.think.stream",
        ("标准化模板文本",): "agent.outline.stream",
    }

    def __init__(self, agent_name: str, match_result_path: Path = None, user_need_path: Path = None,
                 retrieved_templates: list = None, user_need: dict = None):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_outline_generation.json")
        
        # 内存模式优先
        if retrieved_templates is not None:
            self.templates = retrieved_templates
        elif match_result_path:
            with open(match_result_path, "r", encoding="utf-8") as f:
                self.templates = json.load(f)
        else:
            raise ValueError("必须提供 retrieved_templates 或 match_result_path")

        if user_need is not None:
            self.user_need = user_need
        elif user_need_path:
            with open(user_need_path, "r", encoding="utf-8") as f:
                self.user_need = json.load(f)
        else:
            raise ValueError("必须提供 user_need 或 user_need_path")

        self.system_prompt = self.build_system_prompt()
        self.user_prompt = self.build_user_prompt()
        self.current_outline = {}

    def build_system_prompt(self) -> str:
        """
        只生成大纲 + 严格JSON + 贴合用户需求
        """
        return textwrap.dedent(f"""
            你是专业的【合同智能大纲生成专家】，唯一任务：基于召回的合同模板 + 用户完整需求，生成**标准、结构化、层级化**的合同大纲骨架。
            【核心铁律 - 必须100%遵守】
            1. 必须**完全覆盖用户所有需求**：例如（基本信息、个性化附加条款、需求等）禁止遗漏、禁止删减。
            2. 以【参考合同模板】为**可选骨架**，而非强制标准：
               - 模板质量高、结构合理 → 沿用并优化
               - 模板质量一般、相关性弱 → 仅借鉴合理章节，**拒绝生硬套用**
               - 模板章节冗余/无关/过时 → 直接删除，不保留无效内容
               - 若模板metadata和用户元数据冲突，**以用户真实需求为准**，模板必须让步
            3. 当模板与用户需求冲突时，**以用户真实需求为准**，模板必须让步
            4. 大纲的详细程度、完整度必须**与用户需求强度匹配**。具体规则遵循【合同行业复杂度标准】
                                   
            5. 大纲层级**最多3级**：章节 → 子条款 → 子子条款，严禁超过3级；
               层级说明：子条款可分为「并列子项」和「从属子项」
               - 并列子项：如7.1、7.2（同一章节下的并列条款），或7.1.1、7.1.2（同一子条款下的并列子项），各自独立承载具体内容，无“父项仅写总述”的限制；
               - 从属子项：仅当某一条款需要拆分更细的细则时使用（如7.1为总述，7.1.1为具体细则），此时父项写总述，子项写细则；
               - 禁止将「并列关系」误设为「从属父子关系」（如7.1与7.1.1为并列子项，不可设为父项-从属子项）。
            6. 大纲中的”合同首部“和”合同尾部”需要详细列出具体细节,涉及的项目字段要全面（详细参考模板）
            7. 章节名称统一用”第一/二/xxx条“（汉字形式），章节名称必须专业、标准、符合法律文书规范
            8. 所有子条款（含并列子项、从属子项）仅写**核心概括**，不写具体细节，但是要展示每个条款需要涵盖的方面（尽可能全面），例如：“租金标准及支付方式：租金计算方式（如按套内/建筑面积）、月租金总额、支付方式（现金/银行转账等）、指定收款账户”；
               并列子项（如7.1、7.1.1、7.1.2）需各自明确核心概括，避免内容重复或嵌套。
            9. 所有个性化需求必须**明确体现在大纲对应位置**，不可隐藏、不可含糊
            
            10. 如有必要，可以添加表格/图片等辅助说明，表格仅展示必要表头即可，图片需用占位符表示并简要说明。
            11. 禁止编造内容、禁止条款冲突、禁止结构冗余
            12. 生成大纲后需要自检审查，具体规则见【自检审查清单】，确保最后生成的合同大纲符合所有要求。
            13. 严格执行脱敏规则，如果出现敏感信息，必须替换为规范占位符{{变量名}}，不要有其他标识符（如【】等）
            14. 占位符必须**语义明确、全局唯一、不重复、不冲突**
            
            【自检审查清单】
            1. **必备条款缺失**：根据合同类型（如股东协议、租赁合同等），检查是否缺少行业惯例或法律要求的核心章节/条款。示例：股东协议缺少"股权转让限制"或"优先购买权"；租赁合同缺少"维修责任"或"提前解约条件"。
            2. **模糊或空洞**：检查是否有章节标题过于笼统以至于无法指导后续起草（如"其他约定"、"补充事项"）。
                - 允许章节只有标题，不要求附带描述
                - 仅当标题本身无法传递任何约束方向时，才视为空洞
            3. **用户特殊需求未覆盖**：对照用户提供的额外需求（如"需要保密条款""要求有拖售权"），确认是否已体现在大纲中。
            4. **可执行性缺陷**：判断关键义务是否缺乏可操作的细节（如"违约赔偿"未约定计算方式或上限）。
            5. **法律关系完整**：该类合同行业标准中不可或缺的法律约定要素（如标的、价款、履行方式、违约责任、争议解决等），大纲中是否至少以条款的形式提及了这些"面"？
                - 例如租赁合同 → 检查是否有"租赁标的、租金、租期、违约责任、争议解决"这几个面
            6. **场景适用覆盖**：结合用户描述的使用场景，是否有必要增加特殊/边缘情况的条款维度？
                - 例如用户说"用于商业办公" → 检查是否有商业用途限制/装修相关的条款面
            7. **结构闭合**：合同结构是否完整——首部（标题+当事人）、正文、尾部（签章+日期）是否都有？
            
            【合同行业复杂度标准】
            {COMPLEXITY_STANDARD}
            
            {MASKRULES}
            
            【输出格式】
            1.  输出中要包含**思考过程**，说明为了符合用户需求而生成大纲的要求（可能但不限于复杂度，需求，个性需求，模板质量，可参考模板部分），100字以内。
            2.  输出必须严格遵循以下Schema：
    """ + json.dumps(OUTLINE_SCHEMA, ensure_ascii=False, indent=4)).strip()

    def build_user_prompt(self) -> str:
        """构建用户输入prompt"""
        collected = self.user_need["collected"]
        extra_need = self.user_need["extra_need"]
        summary = self.user_need["summary"]
        template_info = self.templates
        # 删除每个item的”template“字段，保留其他字段
        template_info_filter = []
        for item in template_info:
            filtered_item = item.copy()   # 浅拷贝，不修改原始match_result的元素。
            filtered_item.pop("template", None)
            template_info_filter.append(filtered_item)

        
        return textwrap.dedent(f"""
            【用户需求信息】
                - 用户需求元数据：{collected}
                - 个性化附加需求：{extra_need}
                - 需求摘要：{summary}
            【参考合同模板（仅供参考，非强制）当前列表排序顺序就是参考优先级顺序】
                - {template_info_filter}
            【生成要求】
            1. 优先满足用户需求，模板仅作结构参考，不可完全照抄。
            2. 模板质量不高时，可重构、删减、合并章节
            3. 无关/冗余模板章节直接删除，不保留
            4. 将用户所有个性化需求**精准插入对应章节/条款**
            5. 大纲详细程度根据用户需求复杂度自动调整：
            6. 保持最多3级结构，条款仅保留核心方面概括
            7. 严格脱敏，使用全局唯一占位符
            8. 仅输出符合Schema的合法JSON，无任何其他内容
        """).strip()

    def generate_outline(self) -> dict:
        """
        生成初始合同大纲骨架
        """
        system = self.system_prompt
        user = self.user_prompt
        response = self.llm_call(system, user)
        json_data = self.response_parser.parse_agent_response(response)
        if json_data and "error" not in json_data:
            self.current_outline = json_data

        return json_data
