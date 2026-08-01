#!/usr/bin/env python3
"""
列出备份版本工具

description: 列出所有可用的备份版本
params:
  无
returns: 备份列表
"""
from typing import Dict, Any

class ListBackupsTool:
    def __init__(self, outline_manager):
        """初始化列出备份工具
        
        Args:
            outline_manager: OutlineManager实例
        """
        self.outline_manager = outline_manager
    
    def execute(self) -> str:
        """执行列出备份操作
        
        Returns:
            备份列表
        """
        backups = self.outline_manager.list_backups()
        if not backups:
            return "暂无备份"
        
        backup_list = ["可用备份版本："]
        for i, backup in enumerate(backups, 1):
            backup_list.append(f"{i}. {backup['timestamp']} - {backup['description'] or '无描述'}")
        
        return "\n".join(backup_list)
