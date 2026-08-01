#!/usr/bin/env python3
"""
网络搜索工具（博查API + 自动抓取网页正文完整版）
description: 当模板检索无法找到相关内容时，使用博查搜索API获取参考信息
params:
  query: 用户的需求描述或问题
returns: 网络搜索结果，返回JSON格式的 content 和 summary 字段
"""
import sys
import os
import json
import re
import requests
from typing import List, Dict, Any
from bs4 import BeautifulSoup

class WebSearchTool:
    def __init__(self,outline_manager=None):
        """初始化网络搜索工具"""
        self.api_key = os.environ.get("BOCHA_API_KEY", "")
        self.api_url = "https://api.bocha.cn/v1/web-search"

    def _extract_legal_keywords(self, query: str) -> List[str]:
        """从查询中提取法律相关的关键词"""
        stopwords = {'的', '了', '和', '是', '在', '有', '与', '或', '等', '及', '为', '以', '对', '将', '把', '被', '请', '如', '若', '以及', '及其', '且', '并', '但', '或者', '如果', '当', '时', '应当', '可以', '必须', '不得', '这个', '那个', '什么', '如何', '怎么', '为什么', '因为', '所以', '但是', '而且'}
        words = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', query)
        keywords = [w for w in words if len(w) >= 2 and w not in stopwords]
        legal_keywords = []
        for kw in keywords:
            if any(char in kw for char in '合同协议条款义务权利责任违约金赔偿损失变更解除终止履行'):
                legal_keywords.append(kw)
        if not legal_keywords and len(keywords) >= 2:
            legal_keywords = keywords[:3]
        return legal_keywords

    def _format_legal_query(self, query: str) -> str:
        """格式化查询为法律相关查询"""
        keywords = self._extract_legal_keywords(query)
        if keywords:
            return ' '.join(keywords) + ' 合同条款'
        return query + ' 合同条款'

    def _get_webpage_content(self, url: str, timeout: int = 20) -> str:
        """【新增】自动抓取URL网页正文内容"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding
            soup = BeautifulSoup(resp.text, "html.parser")

            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
                tag.decompose()

            text = soup.get_text(separator="\n")
            lines = [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) > 5]
            clean_text = "\n".join(lines)

            if len(clean_text) > 12000:
                clean_text = clean_text[:12000] + "……（内容已截断）"
            return clean_text

        except Exception as e:
            return f"抓取网页失败：{str(e)}"

    def _call_bocha_api(self, query: str,num_results: int = 8) -> Dict[str, Any]:
        """调用博查搜索API"""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "query": query,
                "num_results": num_results,
                "summary": True
            }
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=20)
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"API调用失败: {response.status_code}"}
        except Exception as e:
            return {"error": f"API调用异常: {str(e)}"}

    def _parse_api_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """【升级】解析API响应 + 自动抓取网页正文"""
        if "error" in response:
            return [{
                'content': f"API调用失败: {response['error']}",
                'summary': f"API调用失败: {response['error']}"
            }]

        search_results = []

        # 博查API标准格式：data → webPages → value
        if "data" in response and "webPages" in response["data"] and "value" in response["data"]["webPages"]:
            items = response["data"]["webPages"]["value"]
            for item in items:
                title = item.get("name", "")
                url = item.get("url", "")
                snippet = item.get("snippet", "")
                summary = item.get("summary", snippet)

                # 自动抓取正文
                content = self._get_webpage_content(url) if url else snippet

                search_results.append({
                    "title": title,
                    "url": url,
                    "content": content,
                    "summary": summary
                })

        elif "webPages" in response and "value" in response["webPages"]:
            items = response["webPages"]["value"]
            for item in items:
                title = item.get("name", "")
                url = item.get("url", "")
                snippet = item.get("snippet", "")
                summary = item.get("summary", snippet)
                content = self._get_webpage_content(url) if url else snippet
                search_results.append({"title": title, "url": url, "content": content, "summary": summary})

        elif "summary" in response:
            summary = response["summary"]
            search_results.append({"title": "总结", "url": "", "content": summary, "summary": summary})

        else:
            search_results.append({
                "title": "未知格式",
                "url": "",
                "content": str(response),
                "summary": "无法解析响应"
            })

        return search_results

    def execute(self, query: str,num_results: int = 8) -> str:
        """执行网络搜索操作（返回JSON）"""
        formatted_query = self._format_legal_query(query)
        response = self._call_bocha_api(formatted_query,num_results=num_results)
        search_results = self._parse_api_response(response)

        if not search_results:
            search_results = [{
                'content': '网络搜索暂无结果',
                'summary': '网络搜索暂无结果'
            }]

        return json.dumps(search_results, ensure_ascii=False, indent=2)

    def get_search_results(self, query: str, num_results: int = 8) -> List[Dict[str, Any]]:
        """获取搜索结果（供外部调用）"""
        formatted_query = self._format_legal_query(query)
        response = self._call_bocha_api(formatted_query)
        return self._parse_api_response(response)[:num_results]


# ====================== 测试代码 ======================
if __name__ == "__main__":
    tool = WebSearchTool()
    result = tool.execute("股东协议合同")
    print(result)