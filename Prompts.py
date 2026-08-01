"""
存放所有提示词
"""
import json
from typing import List, Dict, Any, Optional
import textwrap
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

CONTRACT_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "标准化模板文本": {
            "type": "object",
            "description": "层级结构化合同，支持：章节 → 条款 → 子条款 → 子子条款（最多三级）",
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
                            "章节编号": {"type": "string","description": "章节编号，如：第1章、第2章等"},
                            "章节标题": {"type": "string","description": "原章节标题，如：合同条款、合同条件等"},
                            "条款列表": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "条款编号": {"type": "string","description": "条款编号，如：1.1、1.2等，这里只能是**二级标题**（跟随一级标题重新编号）"},
                                        "条款内容": {"type":"string"},
                                        "子条款列表": {
                                            "type": "array",
                                            "description": "如果没有保留空数组",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "子条款编号": {"type": "string","description": "子条款编号，如：1.1.1、1.1.2等"},
                                                    "子条款内容": {"type": "string"},
                                                    "子子条款列表": {
                                                        "type": "array",
                                                        "description": "如果没有保留空数组",
                                                        "items": {
                                                            "type": "object",
                                                            "properties": {
                                                                "子子条款编号": {"type": "string"},
                                                                "子子条款内容": {"type": "string"}
                                                            },
                                                            "required": ["子子条款编号", "子子条款内容","子子条款列表"]
                                                        }
                                                    }
                                                },
                                                "required": ["子条款编号", "子条款内容","子子条款列表"]
                                            }
                                        }
                                    },
                                    "required": ["条款编号", "条款内容","子条款列表"]
                                }
                            }
                        },
                        "required": ["章节编号", "章节标题", "条款列表"]
                    }
                },
                "合同尾部": {
                    "type": "string",
                    "description": "所有附件、附录、图表、当事人、盖章信息等（已脱敏）"
                },
            },
            "required": ["合同首部", "正文章节", "合同尾部"]
        },

        "占位符清单": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "变量名": {"type": "string"},
                    "原始文本": {"type": "string"},
                    "所在层级位置": {"type": "string", "description": "如：第一章 → 1.1 → 1.1.1"}
                },
                "required": ["变量名", "原始文本", "所在层级位置"]
            }
        },

        "最终审核结果": {"type": "string","enum": ["接受", "拒绝：理由xxx"],"description": "审查工具的最终结果，如：接受、拒绝"}
    },
    "required": ["标准化模板文本", "占位符清单", "最终审核结果"]
}

METADATA_CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_type": {"type": "string"},
        "party_type": {"type": "string"},
        "scene": {"type": "string"},
        "complexity": {"type": "string"},
        "region": {"type": "string"},
    },
    "required": ["contract_type", "party_type", "scene", "complexity", "region"]
}

def build_contract_system_prompt() -> str:
    return textwrap.dedent("""
    你是专业的合同结构化抽取专家。
    任务：将用户输入的【原始合同文本】转换为可入库的标准合同模板(严格层级结构化模板)。
    层级规则（最多支持三级，自动识别，有几级输出几级）：
    1. 合同首部
    2. 章节（第一条、第二条…）
       ↓
    3. 条款（1.1、2.1…）
       ↓
    4. 子条款（1.1.1、1.1.2…）
       ↓
    5. 子子条款（1.1.1.1、1.1.2.1…）【第四级，如不存在则不输出】
    你必须严格遵守以下规则：
    1. 只抽取、脱敏、标准化，不修改合同结构、不增删章节、不改变条款顺序。
    2. 所有需要脱敏的内容，必须统一替换为规范占位符 {变量名}。
    3. 占位符必须清晰、语义明确、**全局唯一**。
    4. 必须输出【脱敏后的完整模板文本】。
    5. 必须输出【占位符清单】，每条包含：变量名、原始文本、所在章节。
    6. 绝对不允许自由发挥、不允许解释、不允许总结，只输出指定格式内容。
    7. 有几级就解析几级，不强行补齐

    【脱敏规则(必须严格执行）】
    {MASKRULES}

    输出必须严格遵循以下Schema：
    """ + json.dumps(CONTRACT_EXTRACTION_SCHEMA, ensure_ascii=False, indent=2)
    ).strip()

def build_contract_user_prompt(contract_txt: str) -> str:
    file_manifest = {"原始合同": contract_txt}

    steps = textwrap.dedent("""
    执行步骤：
    1. 通读合同，保持章节结构、条款顺序完全不变。
    2. 抽取【合同首部】（标题、当事人、签订信息等）
    3. 抽取【正文章节】，自动识别层级：
       - 条款（1.1、2.1…）
       - 子条款（1.1.1、1.1.2…）
       - 子子条款（1.1.1.1、1.1.1.2…、1.1.2.1…）【最多四级】
    4. 抽取【合同尾部】（当事人、盖章信息等）
    5. 所有具体信息（公司名、项目名、地址、金额、日期等）替换为 {变量名}
    6：生成【占位符清单】，每条包含：
        - 变量名（占位符名称，不带 { }）
        - 原始文本（替换前的真实内容）
        - 所在章节（标注所在层级位置）
    如果原始合同本身已是模板（无真实信息），则原始文本填空。
    7:再次审查所有输出内，确保下列每一项均无误。
        - 标准化模板文本关键信息全部脱敏替换
        - 占位符清单包含所有需要替换敏的内容
        - 文本中同一个实体对应同一个占位符。
    """).strip()

    tail = "只输出JSON，严格遵循Schema，无多余内容。"
    return "\n\n".join([
        json.dumps(file_manifest, ensure_ascii=False),
        steps,
        tail
    ])
