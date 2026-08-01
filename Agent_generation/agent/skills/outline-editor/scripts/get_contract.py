#!/usr/bin/env python3
"""
获取合同大纲

description: 获取当前合同的完整结构
params: 无
returns: 合同的完整结构
"""
import sys
import os

class GetOutlineTool:
    def __init__(self, outline_manager):
        """初始化获取合同工具
        
        Args:
            outline_manager: OutlineManager实例
        """
        self.outline_manager = outline_manager
    
    def execute(self) -> str:
        """执行获取合同操作
        
        Returns:
            合同的完整结构
        """
        outline = self.outline_manager._format_outline()
        return outline
    