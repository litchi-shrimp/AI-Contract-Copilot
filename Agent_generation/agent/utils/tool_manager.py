#!/usr/bin/env python3
"""
工具管理器
description: 从JSON注册工具，自动从skills/*/scripts加载实现
"""
import os
import sys
import importlib
import json
from typing import Dict, Any


class ToolManager:
    def __init__(self, outline_manager, tools_json: str | list=None, skills_root: str = None):
        """
        初始化工具管理器
        Args:
            outline_manager: Outline 管理器实例
            tools_json: 工具定义JSON文件路径 或 工具列表
            skills_root: skills 根目录，默认 agent/skills
        """
        self.outline_manager = outline_manager
        self.skills_root = skills_root or os.path.join(os.path.dirname(__file__), "..", "skills")

        # 最终只保存【JSON里定义的工具】
        self.tools: Dict[str, dict] = {}
        # 缓存已加载的工具实例
        self.tool_instances: Dict[str, Any] = {}

        # 1. 先加载 JSON 工具列表
        if tools_json:
            self._load_tools_from_json(tools_json)
        # 2. 自动扫描 skills 目录，找到对应实现
        self._scan_and_load_tool_implementations()

    def _load_tools_from_json(self, tools_json):
        """只加载JSON里声明的工具（核心：只认JSON）"""
        tools_data = []
        if isinstance(tools_json, str) and os.path.exists(tools_json):
            with open(tools_json, 'r', encoding='utf-8') as f:
                tools_data = json.load(f)
        elif isinstance(tools_json, list):
            tools_data = tools_json

        for tool in tools_data:
            name = tool.get("name")
            if name:
                self.tools[name] = tool

    def _scan_and_load_tool_implementations(self):
        """扫描 skills/*/scripts，只加载JSON里有的工具"""
        if not os.path.isdir(self.skills_root):
            return

        # 遍历所有技能目录
        for skill_dir_name in os.listdir(self.skills_root):
            skill_dir = os.path.join(self.skills_root, skill_dir_name)
            scripts_dir = os.path.join(skill_dir, "scripts")

            if not os.path.isdir(scripts_dir):
                continue

            # 添加到导入路径
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)

            # 遍历脚本文件
            for filename in os.listdir(scripts_dir):
                if not filename.endswith(".py") or filename.startswith("_"):
                    continue

                tool_name = filename[:-3]
                # 关键：只加载JSON里注册过的工具
                if tool_name not in self.tools:
                    continue

                try:
                    # 真正导入工具
                    module = importlib.import_module(tool_name)
                    # 查找带 execute 的工具类
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and hasattr(attr, "execute"):
                            instance = attr(self.outline_manager)
                            self.tool_instances[tool_name] = instance
                            print(f"[工具加载] {tool_name}", file=sys.stderr)
                except Exception as e:
                    print(f"[工具加载失败] {tool_name}: {str(e)}", file=sys.stderr)

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> str:
        """执行工具（只执行JSON里有的）"""
        if tool_name not in self.tools:
            return f"错误：未在JSON中注册工具 {tool_name}"

        tool = self.tool_instances.get(tool_name)
        if not tool:
            return f"错误：找不到工具实现 {tool_name}"

        try:
            return tool.execute(**params)
        except Exception as e:
            return f"执行失败：{str(e)}"

    def get_tool_list(self):
        """获取JSON里注册的所有工具"""
        if not self.tools:
            return "无已注册工具"
        lines = ["已注册工具："]
        for name, info in self.tools.items():
            desc = info.get("description", "")
            lines.append(f"- {name}：{desc}")
        return "\n".join(lines)
    
    def add_tool(self, tool_def):
        """动态添加工具（幂等：已存在则跳过）
        
        Args:
            tool_def: 工具定义字典
        """
        name = tool_def.get("name")
        if not name:
            return
        if name in self.tools:
            return
        self.tools[name] = tool_def
        # 尝试加载工具实现
        self._scan_and_load_tool_implementations()