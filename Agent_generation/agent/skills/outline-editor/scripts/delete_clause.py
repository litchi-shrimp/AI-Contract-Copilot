#!/usr/bin/env python3
"""
删除条款内容

description: 删除指定条款内容，支持单个或批量删除。批量删除时自动按 locator 降序执行，避免偏移冲突。
params:
  locator: 节点定位标识，可传入单个字符串或 locator 列表（支持跨级别批量删除）
returns: 操作结果
"""
import sys
import os

class DeleteClauseTool:
    def __init__(self, outline_manager):
        """初始化删除条款工具
        
        Args:
            outline_manager: OutlineManager实例
        """
        self.outline_manager = outline_manager
    
    def execute(self, locator) -> str:
        """执行删除条款操作
        
        Args:
            locator: 节点定位标识，可传入单个字符串或 locator 列表（支持跨级别批量删除）
            
        Returns:
            操作结果
        """
        if isinstance(locator, str):
            return self._delete_clause(locator)

        elif isinstance(locator, list):
            if not locator:
                return "locator列表不能为空"

            # 全局排序：深层优先 → 数值降序 → 原始顺序保序
            sorted_locators = sorted(
                enumerate(locator),
                key=lambda x: (
                    -len(x[1].split('.')),
                    tuple(-int(p) for p in x[1].split('.')),
                    x[0]
                )
            )

            results = []
            for _, l in sorted_locators:
                result = self._delete_clause(l)
                results.append(result)
            return "\n".join(results)

        else:
            return "locator参数必须是字符串或列表"
    
    def _delete_clause(self, locator: str) -> str:
        """删除条款
        
        Args:
            locator: 节点定位标识
            
        Returns:
            操作结果
        """
        # 查找父节点和索引
        parent, index, node_type = self.outline_manager.find_parent(locator)
        if not parent or index is None:
            return f"无法找到节点或其父节点: {locator}"
        
        # 保存删除的节点信息
        deleted_node = parent[index]
        
        # 删除节点
        del parent[index]
        
        # 更新后续节点的编号和locator
        parts = list(map(int, locator.split('.')))
        if node_type == 'section':
            for i in range(index, len(parent)):
                parent[i]['章节编号'] = f"第{i + 1}条"
                parent[i]['locator'] = str(i + 1)
                for j, clause in enumerate(parent[i]['条款列表']):
                    clause['条款编号'] = f"{i + 1}.{j + 1}"
                    clause['locator'] = f"{i + 1}.{j + 1}"
                    for k, sub_clause in enumerate(clause.get('子条款列表', [])):
                        sub_clause['子条款编号'] = f"{i + 1}.{j + 1}.{k + 1}"
                        sub_clause['locator'] = f"{i + 1}.{j + 1}.{k + 1}"
                        for l, sub_sub_clause in enumerate(sub_clause.get('子子条款列表', [])):
                            sub_sub_clause['子子条款编号'] = f"{i + 1}.{j + 1}.{k + 1}.{l + 1}"
                            sub_sub_clause['locator'] = f"{i + 1}.{j + 1}.{k + 1}.{l + 1}"
        elif node_type == 'clause':
            for i in range(index, len(parent)):
                parent[i]['条款编号'] = f"{parts[0]}.{i + 1}"
                parent[i]['locator'] = f"{parts[0]}.{i + 1}"
                for j, sub_clause in enumerate(parent[i].get('子条款列表', [])):
                    sub_clause['子条款编号'] = f"{parts[0]}.{i + 1}.{j + 1}"
                    sub_clause['locator'] = f"{parts[0]}.{i + 1}.{j + 1}"
                    for k, sub_sub_clause in enumerate(sub_clause.get('子子条款列表', [])):
                        sub_sub_clause['子子条款编号'] = f"{parts[0]}.{i + 1}.{j + 1}.{k + 1}"
                        sub_sub_clause['locator'] = f"{parts[0]}.{i + 1}.{j + 1}.{k + 1}"
        elif node_type == 'sub_clause':
            for i in range(index, len(parent)):
                parent[i]['子条款编号'] = f"{parts[0]}.{parts[1]}.{i + 1}"
                parent[i]['locator'] = f"{parts[0]}.{parts[1]}.{i + 1}"
                for j, sub_sub_clause in enumerate(parent[i].get('子子条款列表', [])):
                    sub_sub_clause['子子条款编号'] = f"{parts[0]}.{parts[1]}.{i + 1}.{j + 1}"
                    sub_sub_clause['locator'] = f"{parts[0]}.{parts[1]}.{i + 1}.{j + 1}"
        elif node_type == 'sub_sub_clause':
            for i in range(index, len(parent)):
                parent[i]['子子条款编号'] = f"{parts[0]}.{parts[1]}.{parts[2]}.{i + 1}"
                parent[i]['locator'] = f"{parts[0]}.{parts[1]}.{parts[2]}.{i + 1}"
        
        self.outline_manager.log_operation('delete_clause', locator=locator, deleted_node=deleted_node)
        return f"成功删除 {locator} 条款"
