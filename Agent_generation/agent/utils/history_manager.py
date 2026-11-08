#!/usr/bin/env python3
"""
历史管理模块

description: 管理交互历史
"""
from typing import List, Dict
import json

class HistoryManager:
    def __init__(self, max_items: int = 60):
        """初始化历史管理器（max_items：超过后自动压缩历史，防止上下文爆炸）"""
        self.history = []
        self.max_items = max_items
    
    def add_user_input(self, user_input: str):
        """添加用户输入
        
        Args:
            user_input: 用户输入文本
        """
        self.history.append({"role": "user", "content": user_input})
    
    def add_agent_response(self, response: str):
        """添加Agent响应
        
        Args:
            response: Agent响应文本
        """
        self.history.append({"role": "assistant", "content": response})
    
    def add_observation(self, observation: str):
        """添加观察结果
        
        Args:
            observation: 观察结果文本
        """
        self.history.append({"role": "user", "content": f"Observation：{observation}"})

    def add_skill_observation(self,observation:str):
        """添加技能观察结果

        Args:
            observation: 技能观察结果文本
        """
        self.history.append({"role": "skill", "content": f"Skill Observation：{observation}"})

    def add_tool_observation(self,observation:str):
        """添加工具观察结果

        Args:
            observation: 工具观察结果文本
        """
        self.history.append({"role": "tool", "content": f"Tool Observation：{observation}"})

    def add_contract_info(self, contract_info: str):
        """添加合同信息
        
        Args:
            contract_info: 合同信息文本
        """
        self.history.append({"role": "contract", "content": contract_info})

    def add_retrieval_info(self, retrieval_info: str):
        """添加检索信息

        Args:
            retrieval_info: 检索信息文本
        """
        self.history.append({"role": "retrieval", "content": retrieval_info})

    def add_adversarial_review(self, adversarial: str):
        """添加对抗性审查

        Args:
            adversarial: 对抗性审查文本
        """
        self.history.append({"role": "adversarial_review", "content": adversarial})

    def del_typeall_history(self, role: str):
        """删除指定类型的所有历史记录
        
        Args:
            role: 角色类型
        """
        self.history = [item for item in self.history if item["role"] != role]

    def get_history(self) -> List[Dict[str, str]]:
        """获取历史记录
        
        Returns:
            历史记录列表
        """
        if len(self.history) > self.max_items:
            head = self.history[:1]
            tail = self.history[-(self.max_items - 2):]
            dropped = len(self.history) - len(head) - len(tail)
            self.history = head + [{"role": "user", "content": f"（为控制上下文长度，已省略中间 {dropped} 条历史记录）"}] + tail
        return self.history
    
    def clear(self):
        """清空历史记录"""
        self.history = []

    def snapshot(self):
        import copy
        return copy.deepcopy(self.history)
    
    def restore(self, snapshot):
        """从快照恢复"""
        self.history = snapshot

    def save_history(self, path: str):
        """保存历史记录到文件
        
        Args:
            path: 文件路径
        """
        try:
            from ..runtime.paths import atomic_write_json
            atomic_write_json(path, self.history)
        except Exception as e:  # 记忆落盘是辅助功能，失败不影响主流程
            import sys
            print(f"[HistoryManager] 保存历史失败 {path}: {e}", file=sys.stderr)