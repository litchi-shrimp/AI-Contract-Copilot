#!/usr/bin/env python3
"""
插入条款内容

description: 插入新条款内容，支持单个或多个批量操作。批量操作时自动排序避免 locator 偏移冲突。
params:
  locators: 参考节点定位标识列表，每个元素为数字层级定位字符串
  contents: 新条款内容列表，与 locators 一一对应
  positions: 插入位置列表，支持：before/after/child，与 locators 一一对应
  （也支持 locator/content/position 单个参数格式，兼容原有调用）
returns: 操作结果
"""
import sys
import os
import cn2an
import re
from collections import defaultdict


class InsertClauseTool:
    def __init__(self, outline_manager):
        """初始化插入条款工具
        
        Args:
            outline_manager: OutlineManager实例
        """
        self.outline_manager = outline_manager

    def clean_clause_content(self, content: str) -> str:
        """
        安全清洗条款内容：
        1. 只删除【行首】的层级编号（如 8.1 / 8.2.1 / 8.2.1.1）
        2. 句子中间的编号（详见8.2.1、第1.1条）完全不碰
        3. 删除行首缩进
        4. 保留正常换行与格式
        """
        lines = content.splitlines()
        cleaned = []
        for line in lines:
            stripped = line.lstrip()
            cleaned_line = re.sub(r'^(\d+\.)+\d+\s*', '', stripped)
            cleaned.append(cleaned_line)
        return '；'.join([line for line in cleaned if line.strip()])

    def execute(self, locator=None, content=None, position=None,
                locators=None, contents=None, positions=None) -> str:
        """执行插入条款操作

        Args:
            支持两种调用方式：
            1. 单个：locator/content/position 为字符串
            2. 批量：locators/contents/positions 为列表
        Returns:
            操作结果
        """
        try:
            # 归一化为批量格式
            if locators is not None:
                batch_locators = locators
                batch_contents = contents if contents is not None else []
                batch_positions = positions if positions is not None else []
            else:
                batch_locators = [locator] if isinstance(locator, str) else []
                batch_contents = [content] if isinstance(content, str) else []
                batch_positions = [position] if isinstance(position, str) else []

            if not batch_locators:
                return "未提供任何条款"

            # 清洗所有内容
            cleaned_contents = [self.clean_clause_content(c) for c in batch_contents]

            # 构建操作列表
            ops = []
            for i, loc in enumerate(batch_locators):
                c = cleaned_contents[i] if i < len(cleaned_contents) else ""
                p = batch_positions[i] if i < len(batch_positions) else "after"
                ops.append({"locator": loc, "content": c, "position": p, "seq": i})

            if len(ops) == 1:
                return self._insert_clause(ops[0]["locator"], ops[0]["content"], ops[0]["position"])
            else:
                return self._batch_insert(ops)
        except Exception as e:
            return f"插入条款内容时出错: {str(e)}"

    def _batch_insert(self, ops: list) -> str:
        """批量插入条款

        策略：
        1. 所有操作按"有效深度"分组排序后执行：
           - after/before：有效深度 = locator 点数（引用节点所在层级）
           - child：有效深度 = locator 点数 + 1（在子列表追加，更深一层）
           浅层优先，确保父节点先被创建。
        2. 同层级内：
           - depth=1（section 级别）：正序，数字小的先创建。
           - depth≥2（clause 级别）：按 locator 数值降序，避免同一父列表索引偏移。
           - child 操作：保持原始顺序（append 追加，无索引冲突）。
        3. section 级 after 操作若参考节点不存在，自动降级追加到末尾。"""
        results = []

        # 计算每个操作的有效深度
        depth_groups = defaultdict(list)
        for op in ops:
            base_depth = len(op["locator"].split('.'))
            effective_depth = base_depth if op["position"] != "child" else base_depth + 1
            depth_groups[effective_depth].append(op)

        # 按有效深度升序处理（浅层优先）
        for depth in sorted(depth_groups.keys()):
            group_ops = depth_groups[depth]

            child_ops = [op for op in group_ops if op["position"] == "child"]
            sibling_ops = [op for op in group_ops if op["position"] != "child"]

            # after/before：分情况排序
            # depth=1（section 级别）→ 正序（创建新 section，无索引冲突，正序保证前置 section 先创建）
            # depth≥2（clause/sub-clause 级别）→ 倒序（同一父列表内避免索引偏移）
            if depth == 1:
                pending = sorted(sibling_ops, key=lambda x: (
                    tuple(int(p) for p in x["locator"].split('.')),
                    x["seq"]
                ))
                for op in pending:
                    result = self._insert_clause(op["locator"], op["content"], op["position"])
                    results.append(result)
            else:
                pending = sorted(sibling_ops, key=lambda x: (
                    tuple(-int(p) for p in x["locator"].split('.')),
                    0 if x["position"] == "after" else 1,
                    x["seq"]
                ))
                for op in pending:
                    result = self._insert_clause(op["locator"], op["content"], op["position"])
                    results.append(result)

            # child：保持原始顺序（append 追加，无索引冲突）
            child_ops.sort(key=lambda x: x["seq"])
            for op in child_ops:
                result = self._insert_clause(op["locator"], op["content"], op["position"])
                results.append(result)

        return "\n".join(results)

    def _insert_at(self, parent, insert_idx, content, parts, node_type, position) -> str:
        """在预解析的父列表和索引处插入节点，并重编号后续节点
        
        Args:
            parent: 父列表引用
            insert_idx: 插入位置索引
            content: 新节点内容
            parts: 参考节点的 locator tuple
            node_type: 节点类型
            position: 插入位置
        Returns:
            操作结果
        """
        try:
            if node_type == 'section':
                new_section = {
                    '章节编号': f"第{cn2an.an2cn(insert_idx + 1)}条",
                    '章节标题': content,
                    'locator': str(insert_idx + 1),
                    '条款列表': []
                }
                parent.insert(insert_idx, new_section)
                for i in range(insert_idx + 1, len(parent)):
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
                section_idx = parts[0]
                new_clause = {
                    '条款编号': f"{section_idx}.{insert_idx + 1}",
                    '条款大致说明': content,
                    'locator': f"{section_idx}.{insert_idx + 1}",
                    '子条款列表': []
                }
                parent.insert(insert_idx, new_clause)
                for i in range(insert_idx + 1, len(parent)):
                    parent[i]['条款编号'] = f"{section_idx}.{i + 1}"
                    parent[i]['locator'] = f"{section_idx}.{i + 1}"
                    for j, sub_clause in enumerate(parent[i].get('子条款列表', [])):
                        sub_clause['子条款编号'] = f"{section_idx}.{i + 1}.{j + 1}"
                        sub_clause['locator'] = f"{section_idx}.{i + 1}.{j + 1}"
                        for k, sub_sub_clause in enumerate(sub_clause.get('子子条款列表', [])):
                            sub_sub_clause['子子条款编号'] = f"{section_idx}.{i + 1}.{j + 1}.{k + 1}"
                            sub_sub_clause['locator'] = f"{section_idx}.{i + 1}.{j + 1}.{k + 1}"

            elif node_type == 'sub_clause':
                section_idx, clause_idx = parts[0], parts[1]
                new_sub_clause = {
                    '子条款编号': f"{section_idx}.{clause_idx}.{insert_idx + 1}",
                    '子条款大致说明': content,
                    'locator': f"{section_idx}.{clause_idx}.{insert_idx + 1}",
                    '子子条款列表': []
                }
                parent.insert(insert_idx, new_sub_clause)
                for i in range(insert_idx + 1, len(parent)):
                    parent[i]['子条款编号'] = f"{section_idx}.{clause_idx}.{i + 1}"
                    parent[i]['locator'] = f"{section_idx}.{clause_idx}.{i + 1}"
                    for j, sub_sub_clause in enumerate(parent[i].get('子子条款列表', [])):
                        sub_sub_clause['子子条款编号'] = f"{section_idx}.{clause_idx}.{i + 1}.{j + 1}"
                        sub_sub_clause['locator'] = f"{section_idx}.{clause_idx}.{i + 1}.{j + 1}"

            elif node_type == 'sub_sub_clause':
                section_idx, clause_idx, sub_clause_idx = parts[0], parts[1], parts[2]
                new_sub_sub_clause = {
                    '子子条款编号': f"{section_idx}.{clause_idx}.{sub_clause_idx}.{insert_idx + 1}",
                    '子子条款大致说明': content,
                    'locator': f"{section_idx}.{clause_idx}.{sub_clause_idx}.{insert_idx + 1}"
                }
                parent.insert(insert_idx, new_sub_sub_clause)
                for i in range(insert_idx + 1, len(parent)):
                    parent[i]['子子条款编号'] = f"{section_idx}.{clause_idx}.{sub_clause_idx}.{i + 1}"
                    parent[i]['locator'] = f"{section_idx}.{clause_idx}.{sub_clause_idx}.{i + 1}"

            else:
                return f"无法处理节点类型: {node_type}"

            locator_str = ".".join(str(p) for p in parts)
            self.outline_manager.log_operation('add_clause', locator=locator_str,
                                                content=content, position=position)
            return f"成功在 {locator_str} {position} 位置插入新条款"

        except Exception as e:
            return f"插入节点失败: {str(e)}"

    def _insert_clause(self, locator: str, content: str, position: str) -> str:
        """执行单个条款插入（保持原有逻辑）

        特殊处理：当 section 级 after 操作的参考节点不存在时，
        自动降级为"追加到末尾"，方便批量创建新 section。

        Args:
            locator: 参考节点定位标识
            content: 新条款内容
            position: 插入位置
        Returns:
            操作结果
        """
        ref_node = self.outline_manager.find_node(locator)
        if not ref_node:
            # 降级处理：section 级 after 操作找不到参考节点 → 追加到末尾
            if position == "after" and len(locator.split('.')) == 1:
                sections = self.outline_manager.get_outline()['标准化模板文本']['正文章节']
                insert_idx = len(sections)
                new_section = {
                    '章节编号': f"第{cn2an.an2cn(insert_idx + 1)}条",
                    '章节标题': content,
                    'locator': str(insert_idx + 1),
                    '条款列表': []
                }
                sections.append(new_section)
                self.outline_manager.log_operation('add_clause', locator=locator,
                                                    content=content, position=position)
                return f"成功在 {locator} after 位置插入新条款"
            return f"未找到定位标识为 {locator} 的节点"

        parts = list(map(int, locator.split('.')))
        level = len(parts)

        if position == 'child':
            if level == 1:
                new_clause = {
                    '条款编号': f"{parts[0]}.{len(ref_node['条款列表']) + 1}",
                    '条款大致说明': content,
                    'locator': f"{parts[0]}.{len(ref_node['条款列表']) + 1}",
                    '子条款列表': []
                }
                ref_node['条款列表'].append(new_clause)
            elif level == 2:
                if '子条款列表' not in ref_node:
                    ref_node['子条款列表'] = []
                new_sub_clause = {
                    '子条款编号': f"{parts[0]}.{parts[1]}.{len(ref_node['子条款列表']) + 1}",
                    '子条款大致说明': content,
                    'locator': f"{parts[0]}.{parts[1]}.{len(ref_node['子条款列表']) + 1}",
                    '子子条款列表': []
                }
                ref_node['子条款列表'].append(new_sub_clause)
            elif level == 3:
                if '子子条款列表' not in ref_node:
                    ref_node['子子条款列表'] = []
                new_sub_sub_clause = {
                    '子子条款编号': f"{parts[0]}.{parts[1]}.{parts[2]}.{len(ref_node['子子条款列表']) + 1}",
                    '子子条款大致说明': content,
                    'locator': f"{parts[0]}.{parts[1]}.{parts[2]}.{len(ref_node['子子条款列表']) + 1}"
                }
                ref_node['子子条款列表'].append(new_sub_sub_clause)
            else:
                return "无法为该层级节点添加子节点"
        else:
            parent, index, node_type = self.outline_manager.find_parent(locator)
            if not parent or index is None:
                return "无法找到父节点"

            insert_idx = index if position == 'before' else index + 1
            result = self._insert_at(parent, insert_idx, content,
                                     tuple(parts), node_type, position)
            return result

        self.outline_manager.log_operation('add_clause', locator=locator,
                                            content=content, position=position)
        return f"成功在 {locator} {position} 位置插入新条款"
