import json
import math
import os
import re
from typing import List, Dict
from pathlib import Path
from .outline_manager import OutlineManager
CURRENT_DIR = Path(__file__).parent  # Agent_generation/utils/
BASE_DIR = CURRENT_DIR.parent.parent  # 项目根目录


def _resolve_version_conflict(part_name: str, original: str, versions: Dict[str, str], body_context: str = "") -> str:
    """裁决多个 chunk 对首部/尾部的修改冲突

    策略：
    1. 无人修改 → 返回原始版本
    2. 只有一个唯一修改版本 → 返回该版本
    3. 多个不同修改版本 → 用 LLM 评估哪个最好
    """
    modified_versions = {k: v for k, v in versions.items() if v.strip() != original.strip()}
    unique_versions = list({v for v in modified_versions.values()})

    if not unique_versions:
        return original

    if len(unique_versions) == 1:
        print(f"[合并] {part_name}: 仅一个chunk有修改，自动采用")
        return unique_versions[0]

    print(f"[合并] {part_name}: {len(unique_versions)} 个chunk产生不同修改版本，启动LLM裁决")
    from ..agents.config import AgentConfig
    config = AgentConfig(str(BASE_DIR / "configs" / "config_review.json"))
    llm_call = config.llm_call

    versions_text = ""
    for i, v in enumerate(unique_versions, 1):
        versions_text += f"【版本{i}】\n{v}\n\n"

    body_section = ""
    if body_context:
        body_section = f"\n请结合合同正文章节的内容，评估哪个版本与正文更一致、更匹配。\n【合同正文章节内容】\n{body_context[:1500]}\n"

    system_prompt = "你是一个合同质量评估专家，负责从多个并行起草的版本中选出最优版本。"
    user_prompt = f"""
        合同的【{part_name}】在并行起草时产生了 {len(unique_versions)} 个不同版本。
        请从法律严谨性、信息完整性、格式规范性三个角度评估，选出最优版本。{body_section}

        【原始版本】
        {original}

        {versions_text}
        请输出 JSON：{{"best_version": 选中的版本号（整数）, "reason": "简要选择原因（30字以内）"}}
"""
    response = llm_call(system_prompt, user_prompt)
    try:
        result = json.loads(response)
        version_idx = result.get("best_version", 1)
        if 1 <= version_idx <= len(unique_versions):
            print(f"[合并] LLM 裁决结果：选中版本{version_idx}，原因：{result.get('reason', '')}")
            return unique_versions[version_idx - 1]
    except (json.JSONDecodeError, TypeError):
        print(f"[合并] LLM 裁决解析失败，使用原始版本")

    return original


def split_contract_chunks(input_json: Path = None, output_dir : Path = CURRENT_DIR.parent / "data", chunk_count: int = 3, data: Dict = None):
    """
    将合同大纲按正文章节平均切分为 N 块，每块保留完整结构

    Args:
        input_json: 大纲文件路径（文件模式）
        output_dir: 输出目录（文件模式）
        chunk_count: 切分块数
        data: 大纲数据字典（内存模式，优先于 input_json）

    Returns:
        文件模式返回文件名列表，内存模式返回块字典列表
    """
    if data is not None:
        outline = data
    else:
        with open(input_json, "r", encoding="utf-8") as f:
            outline = json.load(f)

    full_template = outline["标准化模板文本"]

    chapters = full_template["正文章节"]

    # 确保 locator 存在（基于在原始大纲中的全局位置编号）
    if chapters and 'locator' not in chapters[0]:
        for i, section in enumerate(chapters):
            section['locator'] = str(i + 1)
            for j, clause in enumerate(section.get('条款列表', [])):
                clause['locator'] = f"{i+1}.{j+1}"
                for k, sub_clause in enumerate(clause.get('子条款列表', [])):
                    sub_clause['locator'] = f"{i+1}.{j+1}.{k+1}"
                    for l, sub_sub_clause in enumerate(sub_clause.get('子子条款列表', [])):
                        sub_sub_clause['locator'] = f"{i+1}.{j+1}.{k+1}.{l+1}"
        if input_json and data is None:
            with open(input_json, "w", encoding="utf-8") as f:
                json.dump(outline, f, ensure_ascii=False, indent=2)
        print(f"[split] 已为 {len(chapters)} 个章节添加全局 locator")

    total = len(chapters)

    if total == 0:
        print("没有章节可切分")
        return [] if data is not None else None

    chunk_size = math.ceil(total / chunk_count)
    chunks = []
    for i in range(chunk_count):
        start = i * chunk_size
        end = start + chunk_size
        chunk_chapters = chapters[start:end]
        if not chunk_chapters:
            break
        chunks.append(chunk_chapters)

    if data is not None:
        result_dicts = []
        for idx, chunk in enumerate(chunks, 1):
            chunk_dict = {
                "标准化模板文本": {
                    "合同首部": full_template["合同首部"],
                    "正文章节": chunk,
                    "合同尾部": full_template["合同尾部"]
                },
            }
            result_dicts.append(chunk_dict)
        print(f"切分完成，共生成 {len(chunks)} 块（内存模式）")
        return result_dicts

    result_files = []
    for idx, chunk in enumerate(chunks, 1):
        chunk_data = {
            "标准化模板文本": {
                "合同首部": full_template["合同首部"],
                "正文章节": chunk,
                "合同尾部": full_template["合同尾部"]
            },
        }
        out_file = os.path.join(output_dir, f"outline_chunk_{idx}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, ensure_ascii=False, indent=2)
        result_files.append(f"outline_chunk_{idx}.json")

    print(f"切分完成，共生成 {len(chunks)} 块：")
    for f in result_files:
        print(f" → {f}")
    return result_files



def merge_contract_chunks(input_json: Path = None, chunk_files: List[str] = None,
                          chunk_dicts: List[Dict] = None, original_outline: Dict = None,
                          output_json: Path = CURRENT_DIR.parent.parent/ "outputs" / "initial_contract.json"):
    if original_outline is not None:
        original = original_outline
    else:
        with open(input_json, "r", encoding="utf-8") as f:
            original = json.load(f)
    think = original.get("思考", "")
    original_header = original["标准化模板文本"]["合同首部"]
    original_footer = original["标准化模板文本"]["合同尾部"]

    """合并所有块的结果"""
    merged_chapters = []
    header_versions = {}
    footer_versions = {}

    if chunk_dicts is not None:
        for idx, chunk_dict in enumerate(chunk_dicts, 1):
            chunk_key = f"chunk_{idx}"
            header_versions[chunk_key] = chunk_dict["标准化模板文本"]["合同首部"]
            footer_versions[chunk_key] = chunk_dict["标准化模板文本"]["合同尾部"]
            chapters = chunk_dict["标准化模板文本"]["正文章节"]
            for chapter in chapters:
                for clause in chapter.get("条款列表", []):
                    desc = clause.get("条款大致说明", "")
                    clause["条款大致说明"] = re.sub(r'^\d+(\.\d+)*\s+', '', desc).strip()
                    for sub_clause in clause.get("子条款列表", []):
                        sub_desc = sub_clause.get("子条款大致说明", "")
                        sub_clause["子条款大致说明"] = re.sub(r'^\d+(\.\d+)*\s+', '', sub_desc).strip()
            merged_chapters.extend(chapters)
    else:
        for file in chunk_files:
            with open("./agent/data/" + file, "r", encoding="utf-8") as f:
                data = json.load(f)

            header_versions[file] = data["标准化模板文本"]["合同首部"]
            footer_versions[file] = data["标准化模板文本"]["合同尾部"]

            chapters = data["标准化模板文本"]["正文章节"]
            for chapter in chapters:
                for clause in chapter.get("条款列表", []):
                    desc = clause.get("条款大致说明", "")
                    clause["条款大致说明"] = re.sub(r'^\d+(\.\d+)*\s+', '', desc).strip()
                    for sub_clause in clause.get("子条款列表", []):
                        sub_desc = sub_clause.get("子条款大致说明", "")
                        sub_clause["子条款大致说明"] = re.sub(r'^\d+(\.\d+)*\s+', '', sub_desc).strip()
            merged_chapters.extend(chapters)

    # 将合并后的正文格式化为可读文本，供裁决 LLM 做一致性参考
    body_lines = []
    for ch in merged_chapters:
        num = ch.get("章节编号", "")
        title = ch.get("章节标题", "")
        body_lines.append(f"{num} {title}")
        for cl in ch.get("条款列表", []):
            desc = cl.get("条款大致说明", "")
            if desc:
                body_lines.append(f"  - {desc}")
    body_context = "\n".join(body_lines)

    # 首尾冲突检测与裁决
    final_header = _resolve_version_conflict("合同首部", original_header, header_versions, body_context)
    final_footer = _resolve_version_conflict("合同尾部", original_footer, footer_versions, body_context)

    # 构建最终完整大纲
    final_data = {
        "标准化模板文本": {
            "合同首部": final_header,
            "正文章节": merged_chapters,
            "合同尾部": final_footer,
            "思考": think
        }
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)


    #  这里将合并后的结构化合同，转为人类可读非结构的合同文本，并保存再outputs目录下
    CURRENT_FILE = Path(__file__).resolve()
    AGENTS_DIR = CURRENT_FILE.parent  # agent/utils
    AGENT_DIR = AGENTS_DIR.parent  # agent/
    BASE_DIR = AGENT_DIR.parent  # 项目根目录
    outline_path = BASE_DIR / "outputs" / "initial_contract.json"
    match_result_path = AGENT_DIR / "data" / "match_result.json"
    outline_manager = OutlineManager(outline_path, match_result_path)
    modified_text = outline_manager._format_outline()
    # 保存
    with open(BASE_DIR / "outputs" / "modified_contract_text_review_before.txt", "w", encoding="utf-8") as f:
        f.write(modified_text)

    print(f"\n✅ 合并完成！输出文件：{output_json}")
    return final_data
