"""
制作合同模板库
结合合同结构抽取Agent 和 元数据抽取Agent，将结果合并，生成最终的合同模板库
均按照类别存放在./template_library/目录下，按照类别命名，每个JSON中包含多个同类别的模板
"""
from unittest import result
import json
import time
import os
import textwrap
from typing import List, Dict, Any, Optional
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, wait
from Prompts import *
from datetime import datetime, timedelta
from docx import Document
from Agents import ExtractContractStructureAgent, MetadataContractExtractionAgent
from check_missing_files import get_source_files_from_json

class TemplateMerger:
    @staticmethod
    def merge(structure_result: dict, metadata_result: dict, source_file: str = "") -> dict:
        """
        structure_result: ExtractContractStructureAgent.extract() 的输出
        metadata_result: MetadataExtractionAgent.extract_metadata() 的输出
        return: 最终可直接入库的完整模板JSON
        """
        contract_type = metadata_result["contract_type"]
        template_id = TemplateMerger.generate_template_id(contract_type)
        
        full_template = {
            # === 基础标识（自动生成）===
            "template_id": template_id,
            "template_name": TemplateMerger.generate_template_name(metadata_result),
            "contract_type": contract_type,
            "template_version": "v1.0",
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_file": source_file,

            # === 业务标签（来自元数据抽取）===
            "party_type": metadata_result["party_type"],
            "scene": metadata_result["scene"],
            "complexity": metadata_result["complexity"],
            "region": metadata_result["region"],
            "is_recommended": 0,  # 默认0，人工可后期改1

            # === 来自结构抽取的内容 ===
            "standard_template_content": structure_result.get("标准化模板文本", ""),
            "placeholder_list": structure_result.get("占位符清单", []),
        }
        return full_template

    @staticmethod
    def generate_template_id(contract_type: str) -> str:
        """从all_classify.json中获取唯一ID"""
        with open("./template_library/all_classify.json", "r", encoding="utf-8") as f:
            classify = json.load(f)
        
        # 处理最细粒度的合同类型，如"建设工程合同.勘察合同"
        parts = contract_type.split('.')
        
        # 递归查找ID
        def find_id(data, path):
            if not path:
                return data.get("ID", "TPL-OTHER-AUTO")
            current = path[0]
            if current in data and isinstance(data[current], dict):
                return find_id(data[current], path[1:])
            return "TPL-OTHER-AUTO"
        return find_id(classify, parts)

    @staticmethod
    def generate_template_name(metadata: dict) -> str:
        """自动生成模板名，如：房屋租赁合同（个人-个人·标准版·通用）"""
        ct = metadata["contract_type"]
        pt = metadata["party_type"]
        cp = metadata["complexity"]
        rg = "地区"+metadata["region"]
        return f"{ct}（{pt}・{cp}・{rg}）"
    


def read_docx(file_path):
    """读取docx文件内容"""
    if not Document:
        return ""
    try:
        doc = Document(file_path)
        text = []
        for paragraph in doc.paragraphs:
            text.append(paragraph.text)
        return '\n'.join(text)
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return ""

def collect_docx_files(folder_path):
    """收集文件夹下所有docx文件"""
    files = []
    for root, dirs, filenames in os.walk(folder_path):
        for filename in filenames:
            if filename.lower().endswith(".docx"):
                # 获取绝对路径
                path = os.path.abspath(os.path.join(root, filename))
                # if "买卖" in path: continue
                # 统计字数
                word_count = len(read_docx(path))
                # if word_count > 50000:
                #     # print(f"文件 {path} 字数: {word_count}")
                #     continue
                files.append(os.path.join(root, filename))

    # 找到已经处理过的文件，避免重复处理
    existing_files = list(get_source_files_from_json())
    # 统一转换为使用正斜杠的标准化路径
    existing_files_normalized = set(os.path.normpath(f).replace("\\", "/") for f in existing_files)
    # 过滤时也统一转换
    files = [f for f in files if os.path.normpath(f).replace("\\", "/") not in existing_files_normalized]
    return files

def process_one_contract(file_path):
    try:
        print(f"Processing {file_path}")
        # 读取文件内容
        contract_text = read_docx(file_path)
        if not contract_text:
            # print(f"Skipping {file_path} - empty content")
            return
        # 提取元数据
        metadata_agent = MetadataContractExtractionAgent(contract_text)
        metadata = metadata_agent.extract_metadata()
        # 提取结构
        extract_agent = ExtractContractStructureAgent(contract_text)
        structure_result = extract_agent.extract()
        # 合并结果
        merged_template = TemplateMerger.merge(structure_result, metadata, file_path)
        template_id = merged_template["template_id"]

        # 增量保存到对应的JSON文件
        output_file = f"./template_library/{template_id}.json"
        # Step1：读取现有内容（如果文件存在）(为了增量保存）
        existing_data = {}
        if os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except Exception as e:
                print(f"Warning: Could not read existing file {output_file}: {e}")
                existing_data = {}
        # Step2：找到下一个可用的索引
        next_index = str(len(existing_data))
        existing_data[next_index] = merged_template
        # Step3：保存
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error processing {file_path}: {e}")

def process_all_contracts():
    """批量处理所有合同 —— 10线程并行，确保全部完成"""
    docx_files = collect_docx_files("./data/合同模板")
    # docx_files = ["./data/合同模板/承揽合同/OEM采购协议_20260129下载.docx"]
    max_workers = 1  # 固定开10个线程
    if not docx_files:
        print("没有找到任何docx文件")
    print(f"共 {len(docx_files)} 个合同需要处理")
    for file_path in docx_files:
        process_one_contract(file_path)
    # 创建线程池
    # with ThreadPoolExecutor(max_workers=max_workers) as executor:
    #     # 提交所有任务
    #     futures = [executor.submit(process_one_contract, file_path) for file_path in docx_files]
    #     # 等待**所有任务完成**才会继续往下走
    #     wait(futures)
    # 只有全部处理完才会执行到这里
    print(f"\n✅ 全部 {len(docx_files)} 个合同处理完成！")


if __name__ == "__main__":
    # 批量处理所有合同
    print("\n=== 批量处理所有合同 ===")
    process_all_contracts()