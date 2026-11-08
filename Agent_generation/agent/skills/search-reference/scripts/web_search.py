#!/usr/bin/env python3
"""
网络搜索工具（博查API + 自动抓取网页正文）
description: 当模板检索无法找到相关内容时，使用博查搜索API获取参考信息
params:
  query: 用户的需求描述或问题
returns: 网络搜索结果，JSON 格式（title/url/summary/content）
实现见 agent/utils/web_search_core.py（超时/重试/熔断/并行抓取/缓存）
"""
from agent.utils.web_search_core import WebSearchTool  # noqa: F401


if __name__ == "__main__":
    print(WebSearchTool().execute("股东协议合同"))
