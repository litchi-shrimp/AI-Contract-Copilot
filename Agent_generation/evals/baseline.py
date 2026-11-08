"""单 LLM 基线：与系统相同模型（deepseek-flash）单次生成，输出与系统相同的结构化 Schema，保证可比"""
import json

from agent.runtime.llm_gateway import LLMSettings, call_llm_json
from agent.utils.outline_schema import normalize_outline, validate_outline

SCHEMA_HINT = """输出 JSON：
{"标准化模板文本": {"合同首部": "标题、当事人信息栏等", "正文章节": [{"章节编号": "第一条", "章节标题": "...",
  "条款列表": [{"条款编号": "1.1", "条款大致说明": "条款全文", "子条款列表": [{"子条款编号": "1.1.1", "子条款大致说明": "子条款全文"}]}]}],
  "合同尾部": "签章栏、日期等"}}"""

CONTRACT_SYSTEM = f"""你是资深合同律师。请根据用户需求，一次性起草一份完整、专业、可直接使用的中文合同。
要求：符合中国现行法律（《民法典》等）；覆盖该类合同的全部必备条款与风险条款；用户给出的具体数值必须写入；
当事人姓名、证件号、地址等未提供的信息用 {{占位符}} 表示；条款完整成文（不是概要）。
{SCHEMA_HINT}
只输出 JSON。"""

OUTLINE_SYSTEM = f"""你是资深合同律师。请根据用户需求，生成一份合同大纲：章节 → 条款 → 子条款（最多三级），
每个条款写一句“条款大致说明”概括本条要约定的要点（不写全文），覆盖必备条款与用户的全部特殊需求。
{SCHEMA_HINT}
只输出 JSON。"""

SETTINGS = LLMSettings(model_id="deepseek-flash", fallback_model_id="deepseek-flash", temperature=0.4,
                       timeout=600, idle_timeout=180, max_retries=4, fallback_after=3)


def single_llm_contract(requirement: str) -> dict:
    data = call_llm_json(CONTRACT_SYSTEM, f"【用户需求】\n{requirement}", SETTINGS, validator=validate_outline,
                         repair_attempts=2, agent="Baseline:contract")
    return normalize_outline(data)


def single_llm_outline(requirement: str) -> dict:
    data = call_llm_json(OUTLINE_SYSTEM, f"【用户需求】\n{requirement}", SETTINGS, validator=validate_outline,
                         repair_attempts=2, agent="Baseline:outline")
    return normalize_outline(data)
