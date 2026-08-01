#!/usr/bin/env python3
"""
调试 大纲生成 Agent（无匹配模板）
"""
import json
from agent.agents.OutlineGenerationWOTem import generate_outline_adversarial

if __name__ == "__main__":
    user_need = {
          "collected": {
            "contract_type": "股权投资协议",
            "party_type": "企业-个人",
            "region": "通用",
            "complexity": "标准",
            "scene": "企业融资"
          },
          "extra_need": [
            "反稀释条款",
            "优先购买权",
            "一票否决权",
            "拖售权"
          ],
          "summary": "目标公司拟新增注册资本，由投资人以货币形式认购。投后估值1亿元，投资金额2000万元，占股20%。需明确股东权利保护、公司治理、退出机制等。"
        }

    result = generate_outline_adversarial(user_need)
    print(json.dumps(result, ensure_ascii=False, indent=2))