#!/usr/bin/env python3
"""
主入口文件，用于运行合同生成流程
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
from agent.agents.ReviewAgents import ReviewConsistencyAgent, ReviewUsageAgent, ReviewLegalAgent, ReviewCompletenessAgent
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
        draft_agent = DrafterAgent("DrafterAgent", chunk_data=chunk_data, match_data=match_data, reference_outline=reference_outline)
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
            print(f"[失败] {agent_name} 异常：{str(e)}，开始重试{cnt+1}/3次...")
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



def workflow():
    CURRENT_FILE = Path(__file__).resolve() # /Agent_generation/main.py
    BASE_DIR = CURRENT_FILE.parent   # /Agent_generation

    # Step1: 用户需求提取
    start_time1 = time.time()
    try:
        agent = UserFieldExtractionAgent("UserFieldExtractionAgent")
        user_need = agent.start_conversation()
        # 保存结果到文件
        output_path = BASE_DIR / "agent" / "data" / "user_need_summary.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(user_need, f, ensure_ascii=False, indent=2)
        print(f"结果已保存到: {output_path}")
    except Exception as e:
        return f"❌ UserFieldExtractionAgent运行异常：{str(e)}"
    end_time1 = time.time()


    # Step2: 模板匹配（内存传递 user_need）
    start_time2 = time.time()
    try:
        TOPK = 5
        SAVE_MATCH_RESULT = BASE_DIR / "agent" / "data" / "match_result.json"
        match_result = match_templates(topK=TOPK, user_need=user_need)
        json.dump(match_result, open(SAVE_MATCH_RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
        print(f"模板匹配完成，耗时：{time.time() - start_time2:.2f} 秒")
        print(f"🤖：已为您匹配到最相似的{min(TOPK, len(match_result))}个合同模板")
    except Exception as e:
        return f"❌ match_templates运行异常：{str(e)}"
    end_time2 = time.time()


    # Step3: 初始合同大纲生成（内存传递 match_result + user_need）
    start_time3 = time.time()
    try:
        print("🤖：正在为您生成初始合同大纲...")
        if len(match_result) == 0:
            print("🤖：未匹配到现有模板，启动对抗式大纲生成（WebSearch + 审查循环）...")
            outline = generate_outline_adversarial(user_need=user_need)
        else:
            agent = OutlineGenerationAgent(
                agent_name="OutlineGenerationAgent",
                retrieved_templates=match_result,
                user_need=user_need
            )
            outline = agent.generate_outline()
        output_path = BASE_DIR / "agent" / "data" / "initial_outline.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(outline, f, ensure_ascii=False, indent=4)
        print(f"初始合同大纲已保存到: {output_path}")
        print(f"初始合同大纲生成完成，耗时：{time.time() - start_time3:.2f} 秒")
    except Exception as e:
        return f"❌ OutlineGenerationAgent运行异常：{str(e)}"
    end_time3 = time.time()


    # Step4: 合同大纲修改（内存传递 outline）
    start_time4 = time.time()
    try:
        outline_modification_agent = OutlineModificationAgent("modification_agent", data=outline, match_data=match_result)
        user_confirmed, outline = outline_modification_agent.interactive_run()
        if not user_confirmed:
            return "❌ 用户未确认大纲，流程终止"
        # 保存修改后的大纲
        with open(BASE_DIR / "agent" / "data" / "initial_outline.json", "w", encoding="utf-8") as f:
            json.dump(outline, f, ensure_ascii=False, indent=4)
    except Exception as e:
        return f"❌ OutlineModificationAgent运行异常：{str(e)}"
    end_time4 = time.time()


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


    # Step6: 合同审查（内存传递 contract + user_need）
    start_time6 = time.time()
    try:
        print("\n🚀 开始并行运行所有审查 Agent...")
        reviews = parallel_run_review_agents(contract_data=contract, user_need=user_need)
        # 保存审查结果
        for name, r in reviews.items():
            with open(BASE_DIR / "outputs" / f"{name}.json", "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=4)
        print(f"合同审查完成，耗时：{time.time() - start_time6:.2f} 秒")
    except Exception as e:
        return f"❌ 合同审查运行异常：{str(e)}"
    end_time6 = time.time()


    # Step7: 合同润色（内存传递 contract + reviews）
    start_time7 = time.time()
    try:
        contract = run_leader_agent(contract_data=contract, reviews=reviews)
        # 保存修订后的合同
        with open(BASE_DIR / "outputs" / "initial_contract.json", "w", encoding="utf-8") as f:
            json.dump(contract, f, ensure_ascii=False, indent=4)
        print(f"合同润色完成，耗时：{time.time() - start_time7:.2f} 秒")
    except Exception as e:
        return f"❌ 合同润色运行异常：{str(e)}"
    end_time7 = time.time()


    # Step8: 合同修改（交互式，内存传递 contract）
    start_time8 = time.time()
    try:
        contract_modification_agent = ContractModificationAgent("modification_agent", data=contract)
        user_confirmed, contract = contract_modification_agent.interactive_run()
        if not user_confirmed:
            return "❌ 用户未确认合同，流程终止"
        # 保存最终合同
        with open(BASE_DIR / "outputs" / "initial_contract.json", "w", encoding="utf-8") as f:
            json.dump(contract, f, ensure_ascii=False, indent=4)
    except Exception as e:
        return f"❌ ContractModificationAgent运行异常：{str(e)}"
    end_time8 = time.time()

    print(f"用户需求提取耗时：{end_time1 - start_time1:.2f} 秒")
    print(f"模板匹配耗时：{end_time2 - start_time2:.2f} 秒")
    print(f"初始合同大纲生成耗时：{end_time3 - start_time3:.2f} 秒")
    print(f"合同大纲修改耗时：{end_time4 - start_time4:.2f} 秒")
    print(f"合同补全形成初始完整合同耗时：{end_time5 - start_time5:.2f} 秒")
    print(f"合同审查耗时：{end_time6 - start_time6:.2f} 秒")
    print(f"合同润色耗时：{end_time7 - start_time7:.2f} 秒")
    print(f"合同修改耗时：{end_time8 - start_time8:.2f} 秒")
    return "合同生成全部流程完成"




def start_server():
    """启动 SSE 服务器"""
    from sse_server.main import main
    main()


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        start_server()
    else:
        start_time = time.time()
        info = workflow()
        print(info)
        end_time = time.time()
        print(f"合同生成全部流程完成，总耗时：{end_time - start_time:.2f} 秒")

    """
    我们是一家游戏开发公司，想和一家渠道推广公司签一份数字内容授权协议。我们需要把我们的一款手游的角色形象、宣传视频、游戏截图这些素材授权给对方，让他们在江苏地区做市场推广用。合作期限大概是一年，授权范围只限推广用途，不能转授权。我们比较关注授权内容的范围界定、使用限制、费用结算（可能按推广效果分成）以及如果对方用了我们的素材做了违法或损害我们品牌的事情要承担的责任。另外，由于我们的素材涉及一些未公开的新版本内容，还需要加上保密条款。整体上希望合同稍微详细、规范一些，能应对比较复杂的商业场景。请帮我生成这样一份合同。
    """
    """
    我是房东，需要起草一份房屋租赁合同，租给一位打工者。签约双方是个人对个人。房屋位于上海市松江区（地区通用就行），合同复杂程度为标准型，使用场景为居住。租赁期限1年，月租金2500元，押一付三。需要包含允许养猫的宠物条款（一只），并明确宠物损坏墙面、家具的赔偿责任。违约金设置为2个月租金。损坏方面，要求明确非正常磨损（如地板划痕、电器损坏）的修复责任和赔偿标准，退租时需恢复原状。另外，希望加入提前退租违约金（提前30天通知，扣1个月租金）和逾期支付租金的宽限期（5天）。其他需求：房屋内家具家电清单作为附件，物业费由房东承担，水电燃气网费由租客承担。
    """

    """
    （未知类）
    我们是一家私募基金管理人，计划发起设立一只股权投资基金，需要与一家商业银行签订一份基金募集及托管合同。主要安排是通过银行向合格投资者募集资金，并由银行对基金财产进行托管。合作期限与基金存续期一致，大约为5年。我们比较关注募集资金的归集与划转流程、托管人的职责边界（特别是投资监督与划款指令审核）、托管费的计算与支付方式、资金安全保障措施，以及如果托管人未按约定履职导致基金财产损失时的赔偿责任。由于基金涉及非公开的商业信息，还需要增加保密条款。整体上希望合同条款严谨、符合中基协的监管要求，能覆盖募、投、管、退各阶段的托管需求。地区通用，复杂度标准即可。使用场景为股权融资。
    """