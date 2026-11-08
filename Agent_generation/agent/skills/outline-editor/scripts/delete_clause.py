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
                    -len(str(x[1]).split('.')),
                    tuple(-int(p) if p.isdigit() else 0 for p in str(x[1]).split('.')),
                    x[0]
                )
            )

            # 先解析节点对象，执行时按最新 locator 删除（删除会触发重编号）
            refs = [(l, self.outline_manager.find_node(l)) for _, l in sorted_locators]
            results = []
            for l, ref in refs:
                current_loc = ref.get("locator", l) if ref else l
                result = self._delete_clause(current_loc)
                results.append(result.replace(current_loc, l) if current_loc != l else result)
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
        
        # 统一重编号（兼容 chunk 全局 locator，避免按位置重算导致编号错乱）
        self.outline_manager.renumber()

        self.outline_manager.log_operation('delete_clause', locator=locator, deleted_node=deleted_node)
        return f"成功删除 {locator} 条款"
