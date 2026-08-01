#!/usr/bin/env python3
"""
调试 LeaderAgent
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
    CURRENT_FILE = Path(__file__).resolve()  # /Agent_generation/main.py
    BASE_DIR = CURRENT_FILE.parent  # /Agent_generation
    contract_path = BASE_DIR / "outputs" / "initial_contract.json"
    with open(contract_path, "r", encoding="utf-8") as f:
        contract = json.load(f)

    review_files = {
        "ReviewCompletenessAgent": BASE_DIR / "outputs" / "ReviewCompletenessAgent.json",
        "ReviewConsistencyAgent": BASE_DIR / "outputs" / "ReviewConsistencyAgent.json",
        "ReviewLegalAgent": BASE_DIR / "outputs" / "ReviewLegalAgent.json",
        "ReviewUsageAgent": BASE_DIR / "outputs" / "ReviewUsageAgent.json",
    }
    reviews = {}
    for name, path in review_files.items():
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                reviews[name] = json.load(f)
        else:
            reviews[name] = {"review_agent_name": name, "problems": [], "load_error": f"文件不存在: {path.name}"}
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

    print(f"合同润色耗时：{end_time7 - start_time7:.2f} 秒")
    return "合同生成全部流程完成"


if __name__ == '__main__':
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
    我们是一家私募基金管理人，计划发起设立一只股权投资基金，需要与一家商业银行签订一份基金募集及托管合同。主要安排是通过银行向合格投资者募集资金，并由银行对基金财产进行托管。合作期限与基金存续期一致，大约为5年。我们比较关注募集资金的归集与划转流程、托管人的职责边界（特别是投资监督与划款指令审核）、托管费的计算与支付方式、资金安全保障措施，以及如果托管人未按约定履职导致基金财产损失时的赔偿责任。由于基金涉及非公开的商业信息，还需要增加保密条款。整体上希望合同条款严谨、符合中基协的监管要求，能覆盖募、投、管、退各阶段的托管需求。
    """