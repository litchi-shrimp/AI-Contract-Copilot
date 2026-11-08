#!/usr/bin/env python3
"""
网络搜索工具（无模板场景）
实现见 agent/utils/web_search_core.py（超时/重试/熔断/并行抓取/缓存）
"""
from agent.utils.web_search_core import WebSearchTool  # noqa: F401


if __name__ == "__main__":
    print(WebSearchTool().execute("股东协议合同"))
