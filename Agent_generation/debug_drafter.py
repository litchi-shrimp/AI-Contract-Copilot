#!/usr/bin/env python3
"""
调试 大纲补全Agent
"""
import json
import sys

# 解决 Windows 下 GBK 编码无法输出 emoji 的问题
# https://docs.python.org/3/library/sys.html#sys.stdout
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


class _StderrFilter:
    """可静音的 stderr 包装流。silent=True 时丢弃所有 print(..., file=sys.stderr)"""

    def __init__(self, silent: bool = False):
        self._orig = sys.stderr
        self.silent = silent

    def write(self, text):
        if not self.silent:
            self._orig.write(text)

    def flush(self):
        self._orig.flush()

    def isatty(self):
        return self._orig.isatty()

    @property
    def encoding(self):
        return self._orig.encoding


# 全局开关：True=静音所有 stderr 输出（Agent调试日志、LLM耗时等）
sys.stderr = _StderrFilter(silent=False)

from agent.agents.UserFieldExtractionAgent import UserFieldExtractionAgent
from agent.agents.TemplateMatcher import match_templates
from agent.agents.OutlineGenerationAgent import OutlineGenerationAgent
from agent.agents.OutlineGenerationWOTem import generate_outline_adversarial
from agent.agents.OutlineModificationAgent import OutlineModificationAgent
from agent.agents.DrafterAgent import DrafterAgent
from agent.agents.ReviewAgents import ReviewConsistencyAgent, ReviewUsageAgent, ReviewLegalAgent, \
    ReviewCompletenessAgent
from agent.agents.LeaderAgent import LeaderAgent
from agent.agents.ContractModificationAgent import ContractModificationAgent
from agent.utils.deal_chunk import split_contract_chunks, merge_contract_chunks
import threading
from typing import List, Dict
import time
import json
import math
import os
import re
from pathlib import Path


def process_chunk(chunk_data: dict, match_data: list = None, reference_outline: dict = None, user_need: dict = None):
    """单个块的处理逻辑（内存模式）

    Args:
        chunk_data: 块数据字典（内存传递，Agent 原地修改）
        match_data: 匹配数据列表（内存传递，Agent 原地修改）
        reference_outline: 参考大纲（完整未切分的版本）
    """
    chunk_name = f"chunk_{id(chunk_data)}"
    print(f"\n🚀 开始处理：{chunk_name}")
    try:
        draft_agent = DrafterAgent("DrafterAgent", chunk_data=chunk_data, match_data=match_data,
                                   reference_outline=reference_outline, user_need=user_need)
        draft_agent.run()
        print(f"✅ 处理完成：{chunk_name}")
    except Exception as e:
        print(f"❌ 处理失败 {chunk_name}：{str(e)}")


def parallel_process_chunks(chunks: List[dict], match_data: list = None, reference_outline: dict = None, user_need: dict = None):
    """多线程并行处理所有块（内存模式）"""
    threads = []
    for chunk_data in chunks:
        t = threading.Thread(target=process_chunk, args=(chunk_data, match_data, reference_outline, user_need))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    print("\n🎉 所有块处理完成！")


def run_single_review_agent(agent_class, agent_name, contract_data: dict, user_need: dict = None):
    """单个审查 Agent 运行逻辑（内存模式）

    Args:
        agent_class: Agent 类
        agent_name: Agent 名称
        contract_data: 合同数据字典
        user_need: 用户需求字典（仅 ReviewUsageAgent 需要）
    """
    CURRENT_FILE = Path(__file__).resolve()
    output_dir = CURRENT_FILE.parent / "outputs"
    cnt = 0
    while cnt < 3:
        try:
            print(f"[启动] {agent_name}")
            if agent_class is ReviewUsageAgent:
                agent = agent_class(agent_name, data=contract_data, user_need=user_need)
            else:
                agent = agent_class(agent_name, data=contract_data)
            response = agent.run()
            output_path = output_dir / f"{agent_name}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False, indent=4)
            print(f"[完成] {agent_name} → {output_path.name}")
            break
        except Exception as e:
            print(f"[失败] {agent_name} 异常：{str(e)}，开始重试{cnt + 1}/3次...")
            cnt += 1


def parallel_run_review_agents(contract_data: dict, user_need: dict = None):
    """并行运行多个审查 Agent（内存模式）

    Returns:
        reviews: {agent_name: review_result} 字典
    """
    agents_to_run = [
        (ReviewConsistencyAgent, "ReviewConsistencyAgent"),
        (ReviewLegalAgent, "ReviewLegalAgent"),
        (ReviewUsageAgent, "ReviewUsageAgent"),
        (ReviewCompletenessAgent, "ReviewCompletenessAgent"),
    ]
    threads = []
    for agent_class, agent_name in agents_to_run:
        t = threading.Thread(target=run_single_review_agent, args=(agent_class, agent_name, contract_data, user_need))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    print("\n✅ 所有审查 Agent 已全部执行完成！")
    # 从文件读回结果（并行线程已将结果写入文件）
    reviews = {}
    for _, agent_name in agents_to_run:
        path = Path(__file__).resolve().parent / "outputs" / f"{agent_name}.json"
        if path.exists():
            reviews[agent_name] = json.load(open(path, "r", encoding="utf-8"))
    return reviews


def run_leader_agent(contract_data: dict, reviews: dict):
    """运行 LeaderAgent，汇总审查并执行修订（内存模式）

    Returns:
        modified_contract: 修订后的合同数据
    """
    try:
        print("\n🚀 开始运行 LeaderAgent...")
        agent = LeaderAgent("LeaderAgent", contract_data=contract_data, reviews=reviews)
        result = agent.run()
        print(f"[完成] LeaderAgent：{json.dumps(result, ensure_ascii=False)}")
        return agent.outline_manager.get_outline()
    except Exception as e:
        print(f"[失败] LeaderAgent 异常：{str(e)}")
        return contract_data


def main():
    CURRENT_FILE = Path(__file__).resolve()  # /Agent_generation/main.py
    BASE_DIR = CURRENT_FILE.parent  # /Agent_generation
    user_need = json.load(open(BASE_DIR / "agent" / "data" / "user_need_summary.json", "r", encoding="utf-8"))
    outline = json.load(open(BASE_DIR / "agent" / "data" / "initial_outline.json", "r", encoding="utf-8"))
    match_result = json.load(open(BASE_DIR / "agent" / "data" / "match_result.json", "r", encoding="utf-8"))
    # Step5: 合同补全形成初始完整合同（分Chunk并行处理，内存模式）
    start_time5 = time.time()
    try:
        print("🤖：接下来为您起草初始完整合同...")
        CHUNK_COUNT = 3
        chunks = split_contract_chunks(
            data=outline,
            chunk_count=CHUNK_COUNT
        )
        print(f"\n⚡ 启动多线程，共 {len(chunks)} 块并行处理...")
        parallel_process_chunks(chunks=chunks, match_data=match_result, reference_outline=outline, user_need=user_need)
        print("\n🔗 开始合并所有块...")
        contract = merge_contract_chunks(
            original_outline=outline,
            chunk_dicts=chunks,
            output_json=BASE_DIR / "outputs" / "initial_contract.json"
        )
        print(f"完整合同起草完成，耗时：{time.time() - start_time5:.2f} 秒")
    except Exception as e:
        return f"❌ 合同补全运行异常：{str(e)}"
    end_time5 = time.time()
    print(f"合同生成全部流程耗时：{end_time5 - start_time5:.2f} 秒")
    return "合同生成全部流程完成"


if __name__ == '__main__':
    ans = main()
    print(ans)
