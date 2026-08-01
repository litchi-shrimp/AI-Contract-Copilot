#!/usr/bin/env python3
"""
模板匹配调试脚本：从 agent/data/user_need_summary.json 加载用户需求，执行完整匹配流程并打印结果。
"""
import json
import time
from pathlib import Path

from agent.agents.TemplateMatcher import match_templates

BASE_DIR = Path(__file__).resolve().parent
USER_NEED_PATH = BASE_DIR / "agent" / "data" / "user_need_summary.json"
MATCH_RESULT_PATH = BASE_DIR / "agent" / "data" / "match_result.json"

if __name__ == "__main__":
    if not USER_NEED_PATH.exists():
        print(f"用户需求文件不存在: {USER_NEED_PATH}")
        print("请先运行 UserFieldExtractionAgent 生成用户需求")
        exit(1)

    user_need = json.load(open(USER_NEED_PATH, "r", encoding="utf-8"))

    print("=" * 60)
    print("用户需求摘要")
    print("=" * 60)
    print(f"collected: {json.dumps(user_need.get('collected', {}), ensure_ascii=False, indent=2)}")
    print(f"extra_need: {user_need.get('extra_need', [])}")
    print(f"summary: {user_need.get('summary', '')}")
    print()

    topK = 5
    print(f"模板匹配开始（topK={topK}）...")
    start = time.time()

    result = match_templates(topK=topK, user_need=user_need)

    elapsed = time.time() - start
    print(f"完成，耗时 {elapsed:.2f}s，匹配到 {len(result)} 个模板\n")

    json.dump(result, open(MATCH_RESULT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
    print(f"结果已保存到: {MATCH_RESULT_PATH}\n")

    for i, tpl in enumerate(result, 1):
        md = tpl.get("metadata", {})
        print(f"{'=' * 60}")
        print(f"模板 {i}")
        print(f"{'=' * 60}")
        print(f"  template_name: {md.get('template_name', 'N/A')}")
        print(f"  metadata_match_score: {tpl.get('metadata_match_score', 0)}")
        print(f"  bm25_score: {tpl.get('bm25_score', 0)}")
        print(f"  contract_type: {md.get('contract_type', 'N/A')}")
        print(f"  party_type: {md.get('party_type', 'N/A')}")
        print(f"  scene: {md.get('scene', 'N/A')}")
        print(f"  complexity: {md.get('complexity', 'N/A')}")
        print(f"  region: {md.get('region', 'N/A')}")
        abst = tpl.get("abstract", "")
        if abst:
            print(f"  abstract: {abst[:200]}...")
        chaps = tpl.get("chapter_information", [])
        if chaps:
            print(f"  章节: {chaps}")
        print()
