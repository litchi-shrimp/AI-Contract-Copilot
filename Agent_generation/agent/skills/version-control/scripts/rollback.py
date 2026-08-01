#!/usr/bin/env python3
"""
回退工具

description: 回退到指定版本的大纲，支持回退到最近版本或指定版本
params:
  version: 版本标识，可选值：'latest'（最近版本）或具体的时间戳
  reason: 回退原因
returns: 回退结果

目前这个工具有点累赘，且效果并没有想象中的好，后续需要使用git树的方式实现版本的管理。
"""
from typing import Dict, Any

class RollbackTool:
    def __init__(self, outline_manager):
        """初始化回退工具
        
        Args:
            outline_manager: OutlineManager实例
        """
        self.outline_manager = outline_manager
    
    def execute(self, version: str = "latest", reason: str = "") -> str:
        """执行回退操作
        
        Args:
            version: 版本标识
            reason: 回退原因
            
        Returns:
            回退结果
        """
        if version == "latest":
            result = self.outline_manager.restore_from_backup()
        else:
            # 查找指定时间戳的备份
            backups = self.outline_manager.list_backups()
            target_backup = None
            for backup in backups:
                if version in backup['timestamp']:
                    target_backup = backup['path']
                    break
            if target_backup:
                result = self.outline_manager.restore_from_backup(target_backup)
            else:
                return f"未找到版本 {version} 的备份"
        
        if result:
            return f"✅ 已成功回退到版本 {version}。原因：{reason}"
        else:
            return "❌ 回退失败，请检查备份文件"
