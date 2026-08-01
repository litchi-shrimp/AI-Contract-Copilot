import os
import json
import glob
import re
import jieba
from rank_bm25 import BM25Okapi
from ..core.base_agent import AGENTS_DIR, AGENT_DIR, BASE_DIR
from ..llm.async_llm import sync_llm_call_with_events

TEMPLATE_LIBRARY_DIR = BASE_DIR.parent / "template_library"
USER_NEED_SUMMARY_PATH = AGENT_DIR / "data" / "user_need_summary.json"
SAVE_MATCH_RESULT = AGENT_DIR / "data" / "match_result.json"

RANK_SYSTEM_PROMPT = """你是一个合同模板检索专家。根据用户的实际业务需求，从候选模板中选出最匹配的 TopK 个模板。

判断标准（按优先级排序）：
1. **类型/场景匹配**：模板的业务场景/类型是否与用户需求一致？（权重最高）
2. **条款覆盖**：模板的条款结构是否覆盖用户提出的特殊需求？
3. **复杂度匹配**：模板的复杂程度（简单/标准/复杂）是否与用户期望匹配？
4. **主体匹配**：模板的签约主体类型（企业-企业/个人-企业/个人-个人）是否匹配？
5. **地区匹配**：模板的适用地区是否与用户一致？

严格输出 JSON 格式，不要输出其他内容，不要用 markdown 代码块包裹：
{
    "indices": [2, 0, 1],
    "reason": "简要说明排序理由"
}"""


def get_contract_id(contract_type, classify_data):
    """根据合同类型找到对应的 ID（classify_data中的ID）"""
    contract_type = contract_type.split(".")[-1]

    def search_contract_id(data, target):
        for key, value in data.items():
            if key == target and isinstance(value, dict) and "ID" in value:
                return value["ID"]
            elif target in key and isinstance(value, dict) and "ID" in value:
                return value["ID"]
            elif isinstance(value, dict):
                result = search_contract_id(value, target)
                if result:
                    return result
        return None

    return search_contract_id(classify_data, contract_type)


def load_user_need():
    with open(USER_NEED_SUMMARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_classify_data():
    classify_path = os.path.join(TEMPLATE_LIBRARY_DIR, "all_classify.json")
    with open(classify_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_one_template_metadata(template_data):
    metadata = {}
    if isinstance(template_data, dict):
        metadata_fields = ["template_id", "template_name", "contract_type", "template_version",
                           "create_time", "source_file", "party_type", "scene", "complexity", "region"]
        for field in metadata_fields:
            if field in template_data:
                metadata[field] = template_data[field]
    return metadata


def get_one_template_chapter(template_data):
    chapter_information = []
    if "standard_template_content" in template_data and "正文章节" in template_data["standard_template_content"]:
        chapters = template_data['standard_template_content']['正文章节']
        for info in chapters:
            chapter_information.append(info["章节编号"] + ":" + info["章节标题"])
    return chapter_information


def get_one_template_chapter_details(template_item):
    result = []

    def process_clause(clause):
        content_parts = []
        clause_no = clause.get("条款编号", "").strip()
        clause_content = clause.get("条款内容", "").strip()
        if clause_no and clause_content:
            content_parts.append(f"{clause_no} {clause_content}")
        elif clause_content:
            content_parts.append(clause_content)
        sub_clauses = clause.get("子条款列表", [])
        for sub_cl in sub_clauses:
            sub_no = sub_cl.get("子条款编号", "").strip()
            sub_content = sub_cl.get("子条款内容", "").strip()
            if sub_no and sub_content:
                content_parts.append(f"{sub_no} {sub_content}")
            elif sub_content:
                content_parts.append(sub_content)
            sub_sub_clauses = sub_cl.get("子子条款列表", [])
            for sub_sub_cl in sub_sub_clauses:
                sub_sub_no = sub_sub_cl.get("子子条款编号", "").strip()
                sub_sub_content = sub_sub_cl.get("子子条款内容", "").strip()
                if sub_sub_no and sub_sub_content:
                    content_parts.append(f"{sub_sub_no} {sub_sub_content}")
                elif sub_sub_content:
                    content_parts.append(sub_sub_content)
        return "\n".join(content_parts)

    standard_content = template_item.get("standard_template_content", {})
    header = standard_content.get("合同首部", "").strip()
    if header:
        result.append(header)
    chapters = standard_content.get("正文章节", [])
    for chapter in chapters:
        chapter_parts = []
        chap_no = chapter.get("章节编号", "").strip()
        chap_title = chapter.get("章节标题", "").strip()
        if chap_no and chap_title:
            chapter_parts.append(f"{chap_no} {chap_title}")
        elif chap_title:
            chapter_parts.append(chap_title)
        clauses = chapter.get("条款列表", [])
        for clause in clauses:
            clause_text = process_clause(clause)
            if clause_text:
                chapter_parts.append(clause_text)
        if chapter_parts:
            result.append("\n".join(chapter_parts))
    footer = standard_content.get("合同尾部", "").strip()
    if footer:
        result.append(footer)
    return result


def stage1_prefix_recall(template_id: str) -> list:
    """阶段1：根据 template_id 前缀召回同类所有模板

    如 "Engineering_EPC" → prefix="Engineering" → glob "Engineering_*.json"
    如 "Lease" → prefix="Lease" → glob "Lease*.json"
    """
    prefix = template_id.split("_")[0] if "_" in template_id else template_id
    pattern = os.path.join(TEMPLATE_LIBRARY_DIR, f"{prefix}_*.json")
    candidate_files = glob.glob(pattern)

    all_candidates = []
    for file_path in candidate_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                template_data = json.load(f)
        except Exception:
            continue

        if isinstance(template_data, dict):
            if all(key.isdigit() for key in template_data.keys()):
                for key, value in template_data.items():
                    if isinstance(value, dict) and "template_name" in value:
                        value["_file"] = file_path
                        value["_key"] = key
                        all_candidates.append(value)
            elif "template_name" in template_data:
                template_data["_file"] = file_path
                template_data["_key"] = "0"
                all_candidates.append(template_data)

    return all_candidates


RANK_STREAM_EVENT_MAP = {
    ("reason",): "agent.think.stream",
}


def stage2_llm_rank(user_need: dict, candidates: list, topK: int, event_bus=None) -> list:
    """阶段2：LLM 语义精排，输出 TopK 索引"""
    collected = user_need.get("collected", {})
    extra_need = user_need.get("extra_need", [])
    summary = user_need.get("summary", "")

    extra_text = "; ".join(extra_need) if extra_need else "无"
    user_text = (
        f"【用户需求】\n"
        f"- 合同类型：{collected.get('contract_type', '')}\n"
        f"- 签约主体：{collected.get('party_type', '')}\n"
        f"- 适用地区：{collected.get('region', '')}\n"
        f"- 复杂程度：{collected.get('complexity', '')}\n"
        f"- 使用场景：{collected.get('scene', '')}\n"
        f"- 特殊需求：{extra_text}\n"
        f"- 需求摘要：{summary}"
    )

    template_lines = []
    for i, tpl in enumerate(candidates):
        abstract = tpl.get("abstract", "")
        party = tpl.get("party_type", "")
        scene = tpl.get("scene", "")
        complexity = tpl.get("complexity", "")
        region = tpl.get("region", "")
        name = tpl.get("template_name", "")

        line = (
            f"[{i}] {name}\n"
            f"    主体: {party} | 场景: {scene} | 复杂度: {complexity} | 地区: {region}\n"
            f"    摘要: {abstract}"
        )
        template_lines.append(line)

    templates_text = "\n\n".join(template_lines)
    actual_topK = min(topK, len(candidates))

    user_prompt = (
        f"{user_text}\n\n"
        f"【候选模板】（共 {len(candidates)} 个）\n"
        f"{templates_text}\n\n"
        f"请选出最匹配的 {actual_topK} 个模板，按相关度从高到低输出索引 JSON。"
    )

    try:
        response = sync_llm_call_with_events(
            RANK_SYSTEM_PROMPT, user_prompt,
            event_bus=event_bus,
            agent_name="TemplateMatcher",
            step=2,
            stream_event_map=RANK_STREAM_EVENT_MAP,
            temperature=0.3,
        )
        print(f"[LLM排名] 原始响应: {response}", flush=True)
        match = re.search(r'\{[^{}]*"indices"\s*:\s*\[[^\]]*\][^{}]*\}', response, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            indices = parsed.get("indices", [])
            return [candidates[i] for i in indices if 0 <= i < len(candidates)]
    except Exception as e:
        print(f"[LLM排名] 失败，降级为 BM25: {e}", flush=True)

    return stage2_bm25_rank(user_need, candidates, topK)


def stage2_bm25_rank(user_need: dict, candidates: list, topK: int) -> list:
    """阶段2降级：BM25 词袋匹配，用用户原始对话 → 模板全文"""
    if not candidates:
        return []
    # 用户侧：原始对话历史
    user_history = "\n".join([h.get("content", "") for h in user_need.get("history", [])])
    if not user_history:
        user_history = user_need.get("summary", "")
    # 模板侧：完整合同文本
    corpus = ["\n".join(get_one_template_chapter_details(tpl)) for tpl in candidates]
    tokenized_corpus = [list(jieba.cut(doc)) for doc in corpus]
    tokenized_query = list(jieba.cut(user_history))
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(tokenized_query).tolist()
    ranked = [candidates[i] for i in sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)]
    return ranked[:topK]


def build_result_item(tpl: dict) -> dict:
    return {
        "file_path": tpl.get("_file", ""),
        "metadata": get_one_template_metadata(tpl),
        "chapter_information": get_one_template_chapter(tpl),
        "all_context": "\n".join(get_one_template_chapter_details(tpl)),
        "abstract": tpl.get("abstract", ""),
        "template": tpl.get("standard_template_content", ""),
    }


ELSE_RANK_SYSTEM_PROMPT = """你是一个合同模板检索专家。当前用户需求**未命中已有的合同类型分类**，需要你从全部候选模板中，
根据语义相似度选出最匹配的 TopK 个模板。

判断标准（按优先级排序）：
1. **场景语义匹配**：模板的核心业务场景（从摘要判断）是否与用户需求描述的场景相似？
2. **条款结构覆盖**：模板的条款结构是否可能覆盖用户提到的特殊要求？
3. **主体/地区匹配**：模板的签约主体类型、适用地区是否与用户一致？
4. **复杂度匹配**：模板的复杂度（简单/标准/复杂）是否与用户期望匹配？

注意：
- 即使没有完全匹配的类型，也要选出场景最接近的模板（如"电竞经纪约"→可匹配"艺人经纪约""委托代理合同"等）
- 优先选结构通用、灵活度高的模板（如通用服务协议），避免选结构过于特殊的模板
- 如果候选模板与用户需求确实差异较大，可在 reason 中说明

严格输出 JSON 格式，不要输出其他内容，不要用 markdown 代码块包裹：
{
    "indices": [2, 0, 1],
    "reason": "简要说明排序理由"
}"""


def load_all_templates() -> list:
    """加载模板库中全部模板

    Returns:
        所有模板字典的列表
    """
    all_templates = []
    if not TEMPLATE_LIBRARY_DIR.exists():
        return all_templates
    for file_path in TEMPLATE_LIBRARY_DIR.glob("*.json"):
        if file_path.name == "all_classify.json":
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict):
            if all(key.isdigit() for key in data.keys()):
                for key, value in data.items():
                    if isinstance(value, dict) and "template_name" in value:
                        value["_file"] = str(file_path)
                        value["_key"] = key
                        all_templates.append(value)
            elif "template_name" in data:
                data["_file"] = str(file_path)
                data["_key"] = "0"
                all_templates.append(data)
    return all_templates


def stage1_bm25_coarse(user_need: dict, candidates: list, topN: int = 30) -> list:
    """BM25 全库粗筛：用用户 summary + scene 对模板 abstract 打分，取 Top-N"""
    if not candidates:
        return []
    summary = user_need.get("summary", "")
    scene = user_need.get("collected", {}).get("scene", "")
    extra = "; ".join(user_need.get("extra_need", []))
    query = f"{summary} {scene} {extra}".strip()
    if not query:
        query = str(user_need.get("collected", {}))
    corpus = [tpl.get("abstract", "") for tpl in candidates]
    tokenized_corpus = [list(jieba.cut(doc)) for doc in corpus]
    tokenized_query = list(jieba.cut(query))
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(tokenized_query).tolist()
    ranked = [candidates[i] for i in sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)]
    return ranked[:topN]


def stage2_else_llm_rank(user_need: dict, candidates: list, topK: int, event_bus=None) -> list:
    """ELSE 分支 LLM 精排：独立 prompt，重点按场景语义匹配"""
    if not candidates:
        return []

    actual_topK = min(topK, len(candidates))
    if actual_topK <= 1:
        return candidates[:actual_topK]

    summary = user_need.get("summary", "")
    collected = user_need.get("collected", {})
    extra_need = user_need.get("extra_need", [])
    extra_text = "; ".join(extra_need) if extra_need else "无"

    user_text = (
        f"【用户需求】\n"
        f"- 合同类型：{collected.get('contract_type', '未知')}（未命中已有分类）\n"
        f"- 使用场景：{collected.get('scene', '')}\n"
        f"- 签约主体：{collected.get('party_type', '')}\n"
        f"- 适用地区：{collected.get('region', '')}\n"
        f"- 复杂程度：{collected.get('complexity', '')}\n"
        f"- 特殊需求：{extra_text}\n"
        f"- 需求摘要：{summary}"
    )

    template_lines = []
    for i, tpl in enumerate(candidates):
        abstract = tpl.get("abstract", "")
        party = tpl.get("party_type", "")
        scene = tpl.get("scene", "")
        complexity = tpl.get("complexity", "")
        region = tpl.get("region", "")
        name = tpl.get("template_name", "")

        line = (
            f"[{i}] {name}\n"
            f"    主体: {party} | 场景: {scene} | 复杂度: {complexity} | 地区: {region}\n"
            f"    摘要: {abstract}"
        )
        template_lines.append(line)

    templates_text = "\n\n".join(template_lines)

    user_prompt = (
        f"{user_text}\n\n"
        f"【候选模板】（共 {len(candidates)} 个）\n"
        f"{templates_text}\n\n"
        f"请选出最匹配的 {actual_topK} 个模板，按相关度从高到低输出索引 JSON。"
    )

    try:
        response = sync_llm_call_with_events(
            ELSE_RANK_SYSTEM_PROMPT, user_prompt,
            event_bus=event_bus,
            agent_name="TemplateMatcher",
            step=2,
            stream_event_map=RANK_STREAM_EVENT_MAP,
            temperature=0.3,
        )
        match = re.search(r'\{[^{}]*"indices"\s*:\s*\[[^\]]*\][^{}]*\}', response, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            indices = parsed.get("indices", [])
            return [candidates[i] for i in indices if 0 <= i < len(candidates)]
    except Exception as e:
        print(f"[ELSE-LLM排名] 失败，降级为 BM25: {e}", flush=True)

    return candidates[:actual_topK]


def match_templates(topK: int = 3, user_need: dict = None, event_bus=None):
    """主函数：前缀召回 → LLM 精排（常规）/ 全库语义搜索（兜底）

    Args:
        topK: 返回前 K 个匹配结果
        user_need: 用户需求字典（内存模式，优先于文件读取）
        event_bus: EventBus 实例（用于流式输出）
    """
    if user_need is None:
        user_need = load_user_need()

    classify_data = load_classify_data()
    contract_type = user_need.get("collected", {}).get("contract_type")
    if not contract_type:
        print("用户需求中缺少合同类型")
        return []

    template_id = get_contract_id(contract_type, classify_data)
    if not template_id:
        print(f"未找到合同类型 '{contract_type}' 对应的 ID → 全库兜底")
        return _full_library_fallback(user_need, topK, event_bus=event_bus)

    candidates = stage1_prefix_recall(template_id)
    if not candidates:
        return []
        print(f"前缀召回无结果: {template_id} → 全库兜底")
        return _full_library_fallback(user_need, topK, event_bus=event_bus)

    if len(candidates) <= 1:
        return [build_result_item(candidates[0])] if candidates else []

    ranked = stage2_llm_rank(user_need, candidates, topK, event_bus=event_bus)
    return [build_result_item(tpl) for tpl in ranked]


def _full_library_fallback(user_need: dict, topK: int, event_bus=None) -> list:
    """全库兜底：BM25 粗筛 + LLM 语义精排

    当前缀召回无结果时触发（contract_type 虽在列表中但无对应模板文件）
    """
    all_templates = load_all_templates()
    if not all_templates:
        print("[兜底] 模板库为空，无法匹配")
        return []
    print(f"[兜底] 全库共 {len(all_templates)} 个模板，BM25 粗筛中...")
    topN = min(30, len(all_templates))
    candidates = stage1_bm25_coarse(user_need, all_templates, topN)
    print(f"[兜底] BM25 粗筛完成，保留 {len(candidates)} 个候选，LLM 精排中...")
    ranked = stage2_else_llm_rank(user_need, candidates, topK, event_bus=event_bus)
    print(f"[兜底] LLM 精排完成，最终返回 {len(ranked)} 个模板")
    return [build_result_item(tpl) for tpl in ranked]


if __name__ == "__main__":
    match_result = match_templates(topK=5)
    json.dump(match_result, open(SAVE_MATCH_RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
    for tpl in match_result:
        print(f"template_name: {tpl['metadata'].get('template_name')}")
        print(f"abstract: {tpl['abstract'][:200]}")
        print("=" * 50)
