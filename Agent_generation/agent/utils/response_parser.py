#!/usr/bin/env python3
"""
响应解析模块

description: 解析 LLM 响应
"""
import json
from typing import Dict, Any

class ResponseParser:
    @staticmethod
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

    @staticmethod
    def parse_agent_response(response: str) -> Dict[str, Any]:
        """
        解析 Agent 响应
        
        Args:
            response: LLM 响应文本
            
        Returns:
            解析后的响应字典
        """
        try:
            response = ResponseParser.extract_json_from_response(response)
            parsed = json.loads(response)
            return parsed
        except Exception as e:
            return {'error': f'解析响应失败：{str(e)}'}
