"""
额外函数
主系统中使用的工具函数汇总
"""
import json
import time
import os
from typing import List, Dict, Any, Optional
from openai import OpenAI
import concurrent.futures
from Prompts import *
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
import re

def extract_json_from_response(response_text: str) -> str:
    """
    从 LLM 响应中提取 JSON 内容
    处理可能的 markdown 代码块标记和其他多余文本，进一步防止LLM输出中包含非 JSON 内容。
    """
    text = response_text.strip()
    
    # 尝试提取 markdown 代码块中的 JSON
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()
    
    # 找到所有可能的 JSON 开始和结束位置
    json_start = -1
    json_end = -1
    
    # 优先寻找 JSON 对象
    if "{" in text:
        json_start = text.find("{")
        # 找到匹配的结束括号
        brace_count = 1
        for i in range(json_start + 1, len(text)):
            if text[i] == "{":
                brace_count += 1
            elif text[i] == "}":
                brace_count -= 1
                if brace_count == 0:
                    json_end = i + 1
                    break

    # 如果没找到对象，尝试寻找 JSON 数组
    if json_start == -1 and "[" in text:
        json_start = text.find("[")
        # 找到匹配的结束括号
        bracket_count = 1
        for i in range(json_start + 1, len(text)):
            if text[i] == "[":
                bracket_count += 1
            elif text[i] == "]":
                bracket_count -= 1
                if bracket_count == 0:
                    json_end = i + 1
                    break

    # 提取找到的 JSON
    if json_start != -1 and json_end != -1:
        text = text[json_start:json_end].strip()

    return text.strip()


def render_contract_to_plain_text(standard_template_text: dict) -> str:
    """
    将结构化的标准化模板文本（合同首部+章节+三级条款）
    转换为连续的纯文本字符串，带合理换行，还原合同阅读格式。
    """
    lines = []

    # ----------------------
    # 1. 添加合同首部
    # ----------------------
    header = standard_template_text.get("合同首部", "").strip()
    if header:
        lines.append(header)
        lines.append("")  # 空行分隔

    # ----------------------
    # 2. 遍历所有章节
    # ----------------------
    chapters = standard_template_text.get("正文章节", [])
    for chap in chapters:
        chap_no = chap.get("章节编号", "").strip()
        chap_title = chap.get("章节标题", "").strip()
        lines.append(f"{chap_no} {chap_title}".strip())
        lines.append("")

        # 遍历条款（第1级：1.1 / 1.2）
        clauses = chap.get("条款列表", [])
        for clause in clauses:
            cl_no = clause.get("条款编号", "").strip()
            cl_content = clause.get("条款内容", "").strip()
            lines.append(f"{cl_no} {cl_content}".strip())

            # 遍历子条款（第2级：1.1.1 / 1.1.2）
            sub_clauses = clause.get("子条款列表", [])
            for sub_cl in sub_clauses:
                sub_no = sub_cl.get("子条款编号", "").strip()
                sub_content = sub_cl.get("子条款内容", "").strip()
                lines.append(f"{sub_no} {sub_content}".strip())

                # 遍历子子条款（第3级：1.1.1.1 / 最多三级）
                sub_sub_clauses = sub_cl.get("子子条款列表", [])
                for sub_sub_cl in sub_sub_clauses:
                    sub_sub_no = sub_sub_cl.get("子子条款编号", "").strip()
                    sub_sub_content = sub_sub_cl.get("子子条款内容", "").strip()
                    lines.append(f"{sub_sub_no} {sub_sub_content}".strip())

            lines.append("")  # 条款结束空行

    # 拼接成最终文本（自动处理换行）
    return "\n".join(lines)


class ReviewTool():
    """
    审查工具，用于还原占位符并检查文本中是否还有未处理的占位符
    """
    
    def __init__(self):
        pass

    def _execute(self, template_text: str, placeholder_list: List[Dict[str, Any]]) -> str:
        """
        执行审查操作
        Args:
            input_data: 输入数据
        Returns:
            审查结果
        """
        
        # 还原占位符
        restored_text = template_text
        for item in placeholder_list:
            variable_name = item.get("变量名", "")
            original_text = item.get("原始文本", "")
            
            if variable_name:
                placeholder = f"{{{variable_name}}}"
                restored_text = restored_text.replace(placeholder, original_text)
        
        # 检查是否还有未处理的占位符
        pattern = r"\{([^}]+)\}"
        remaining_placeholders = re.findall(pattern, restored_text)
        if remaining_placeholders:
            return "拒绝"+f"还有未处理的占位符：{remaining_placeholders}"
        return "接受"


def get_one_template_context(template_item):
    """
    递归获取模板的所有standard_template_content，按大章节分元素
    
    Args:
        template_item: 模板JSON中的一个item（完整的模板对象）
    
    Returns:
        list: 按章节分元素的数组，每个元素是该章节所有条款的拼接
    """
    result = []
    
    def process_clause(clause):
        """递归处理条款及其子条款"""
        content_parts = []
        
        # 添加条款编号和内容
        clause_no = clause.get("条款编号", "").strip()
        clause_content = clause.get("条款内容", "").strip()
        if clause_no and clause_content:
            content_parts.append(f"{clause_no} {clause_content}")
        elif clause_content:
            content_parts.append(clause_content)
        
        # 递归处理子条款
        sub_clauses = clause.get("子条款列表", [])
        for sub_cl in sub_clauses:
            sub_no = sub_cl.get("子条款编号", "").strip()
            sub_content = sub_cl.get("子条款内容", "").strip()
            if sub_no and sub_content:
                content_parts.append(f"{sub_no} {sub_content}")
            elif sub_content:
                content_parts.append(sub_content)
            
            # 递归处理子子条款
            sub_sub_clauses = sub_cl.get("子子条款列表", [])
            for sub_sub_cl in sub_sub_clauses:
                sub_sub_no = sub_sub_cl.get("子子条款编号", "").strip()
                sub_sub_content = sub_sub_cl.get("子子条款内容", "").strip()
                if sub_sub_no and sub_sub_content:
                    content_parts.append(f"{sub_sub_no} {sub_sub_content}")
                elif sub_sub_content:
                    content_parts.append(sub_sub_content)
        
        return "\n".join(content_parts)
    
    # 获取standard_template_content
    standard_content = template_item.get("standard_template_content", {})
    
    # 1. 处理合同首部
    header = standard_content.get("合同首部", "").strip()
    if header:
        result.append(header)
    
    # 2. 处理正文章节
    chapters = standard_content.get("正文章节", [])
    for chapter in chapters:
        chapter_parts = []
        
        # 添加章节编号和标题
        chap_no = chapter.get("章节编号", "").strip()
        chap_title = chapter.get("章节标题", "").strip()
        if chap_no and chap_title:
            chapter_parts.append(f"{chap_no} {chap_title}")
        elif chap_title:
            chapter_parts.append(chap_title)
        
        # 处理该章节下的所有条款
        clauses = chapter.get("条款列表", [])
        for clause in clauses:
            clause_text = process_clause(clause)
            if clause_text:
                chapter_parts.append(clause_text)
        
        # 将该章节的所有内容拼接成一个元素
        if chapter_parts:
            result.append("\n".join(chapter_parts))
    
    # 3. 处理合同尾部
    footer = standard_content.get("合同尾部", "").strip()
    if footer:
        result.append(footer)
    
    return result


def get_source_files_from_json():
    """从template_library目录的JSON文件中提取所有source_file字段"""
    source_files = set()
    template_library_dir = './template_library'
    
    for filename in os.listdir(template_library_dir):
        if filename.endswith('.json'):
            file_path = os.path.join(template_library_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, template in data.items():
                        if 'source_file' in template:
                            # 标准化路径分隔符
                            source_file = template['source_file'].replace('\\', '/')
                            source_files.add(source_file)
            except Exception as e:
                print(f"处理文件 {filename} 时出错: {e}")
    
    return source_files

def get_all_contract_files():
    """递归获取合同模板目录下的所有文件"""
    contract_files = set()
    contract_dir = './data/合同模板'
    
    for root, _, files in os.walk(contract_dir):
        for file in files:
            if file.endswith('.docx'):
                # 计算相对路径
                rel_path = os.path.relpath(os.path.join(root, file), '.').replace('\\', '/')
                # 标准化为与source_file相同的格式
                rel_path = './' + rel_path
                contract_files.add(rel_path)
    
    return contract_files

def check_missing_files():
    print("正在检查缺失的文件...")
    
    # 获取JSON中的source_file
    json_files = get_source_files_from_json()
    print(f"从JSON文件中提取到 {len(json_files)} 个source_file")
    
    # 获取合同模板目录中的所有文件
    contract_files = get_all_contract_files()
    print(f"合同模板目录中共有 {len(contract_files)} 个文件")
    
    # 找出合同模板中存在但JSON中没有引用的文件
    missing_files = contract_files - json_files
    
    if missing_files:
        print("\n以下文件在合同模板目录中存在，但在JSON文件中没有被引用：")
        for file in sorted(missing_files):
            print(f"- {file}")
        print(f"\n总计 {len(missing_files)} 个文件漏选")
    else:
        print("\n所有合同模板文件都已在JSON中被引用，没有漏选的文件。")

if __name__ == "__main__":
    # template_data = json.loads(open("template_library/Accept.json", "r", encoding="utf-8").read())
    # # 处理模板数据（一个JSON文件可能包含多个模板）
    # templates = []
    # if isinstance(template_data, dict):
    #     # 检查是否是包含多个模板的字典
    #     if all(key.isdigit() for key in template_data.keys()):
    #         # 多个模板的情况
    #         for key, value in template_data.items():
    #             if isinstance(value, dict):
    #                 templates.append(value)
    #     else:
    #         # 单个模板的情况
    #         templates.append(template_data)
    # for item in templates:
    #     plain_text = get_one_template_context(item)
    #     print(plain_text[3])
    #     print("="*50)

    # 检查还有哪些合同没有抽取。
    check_missing_files()
