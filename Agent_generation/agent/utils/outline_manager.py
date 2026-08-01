#!/usr/bin/env python3
"""
大纲管理器

description: 加载和管理合同大纲文件
"""
import json
import os
import shutil
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

class OutlineManager:
    def __init__(self, outline_path: Path = None, match_result_path: Path = None,
                 data: Dict[str, Any] = None, match_data: List[Dict[str, Any]] = None):
        """初始化大纲管理器

        Args:
            outline_path: 大纲文件路径（文件模式）
            match_result_path: 匹配结果文件路径（文件模式）
            data: 大纲数据字典（内存模式，优先于 outline_path）
            match_data: 匹配结果列表（内存模式，优先于 match_result_path）
        """
        # 备份相关配置（需要在 _load_outline 之前设置，因为某些操作需要 backup_dir）
        self.outline_path = Path(outline_path) if outline_path else Path(".")
        self.match_result_path = Path(match_result_path) if match_result_path else Path(".")
        self._locator_index: Dict[str, Any] = {}
        self.operation_logs = []
        self._locators_were_added = False

        # 内存模式优先
        if data is not None:
            self.outline = data
            self._add_locators(self.outline)
            self._build_locator_index(self.outline)
        else:
            self.outline = self._load_outline()
            if self._locators_were_added:
                self._save_outline()

        if match_data is not None:
            self.match_result = match_data
        elif match_result_path:
            self.match_result = self._load_match_result()
        else:
            self.match_result = []

        self.backup_dir = os.path.join(
            os.path.dirname(os.path.abspath(str(self.outline_path))),
            "backups"
        ) if self.outline_path != Path(".") else os.path.join(os.getcwd(), "backups")
        self.max_backups = 10

    def _load_outline(self) -> Dict[str, Any]:
        """加载大纲文件

        Returns:
            大纲数据结构
        """
        with open(self.outline_path, 'r', encoding='utf-8') as f:
            outline = json.load(f)
        # 为所有节点添加locator
        # 先检查大纲中是否已经存在locator
        need_locator = (
            outline['标准化模板文本']['正文章节']
            and 'locator' not in outline['标准化模板文本']['正文章节'][0]
        )
        if need_locator:
            self._add_locators(outline)
        # 记录是否新增了locator，供 __init__ 决定是否需要写回文件
        self._locators_were_added = need_locator
        # 重建 locator 索引
        self._build_locator_index(outline)
        return outline

    def _build_locator_index(self, outline: Dict[str, Any]) -> None:
        """遍历大纲，建立 locator → node 的哈希索引"""
        self._locator_index.clear()
        for section in outline['标准化模板文本']['正文章节']:
            loc = section.get('locator')
            if loc:
                self._locator_index[loc] = section
            for clause in section.get('条款列表', []):
                loc = clause.get('locator')
                if loc:
                    self._locator_index[loc] = clause
                for sub_clause in clause.get('子条款列表', []):
                    loc = sub_clause.get('locator')
                    if loc:
                        self._locator_index[loc] = sub_clause
                    for sub_sub_clause in sub_clause.get('子子条款列表', []):
                        loc = sub_sub_clause.get('locator')
                        if loc:
                            self._locator_index[loc] = sub_sub_clause

    def _load_match_result(self) -> List[Dict[str, Any]]:
        """加载匹配结果文件

        Returns:
            匹配结果列表
        """
        try:
            with open(self.match_result_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载匹配结果文件失败：{e}")
            return []

    def _add_locators(self, outline: Dict[str, Any]) -> None:
        """为大纲节点添加locator

        Args:
            outline: 大纲数据结构
        """
        # 处理正文章节
        for i, section in enumerate(outline['标准化模板文本']['正文章节']):
            if 'locator' in section: continue
            section['locator'] = str(i + 1)
            # 处理条款
            for j, clause in enumerate(section['条款列表']):
                if 'locator' in clause: continue
                clause['locator'] = f"{i+1}.{j+1}"
                # 处理子条款
                for k, sub_clause in enumerate(clause.get('子条款列表', [])):
                    if 'locator' in sub_clause: continue
                    sub_clause['locator'] = f"{i+1}.{j+1}.{k+1}"
                    # 处理子子条款
                    for l, sub_sub_clause in enumerate(sub_clause.get('子子条款列表', [])):
                        if 'locator' in sub_sub_clause: continue
                        sub_sub_clause['locator'] = f"{i+1}.{j+1}.{k+1}.{l+1}"

    def _save_outline(self) -> None:
        """原子写入：先写临时文件，再重命名替换，防止写入崩溃导致文件损坏
        
        注意：内存模式下 outline_path 可能为无效路径（如 Path('.')），此时跳过保存。
        调用方应通过 workflow 或 API 自行保存数据。
        """
        import time
        if not self.outline_path or self.outline_path == Path("."):
            return
        temp_path = self.outline_path.with_suffix(".tmp")
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self.outline, f, ensure_ascii=False, indent=2)
            # Windows 上多线程并发时 os.replace 可能被锁，重试 3 次后降级为直接写入
            for attempt in range(3):
                try:
                    os.replace(temp_path, self.outline_path)
                    break
                except OSError:
                    if attempt < 2:
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    # 降级：直接覆盖写入原始文件
                    with open(self.outline_path, 'w', encoding='utf-8') as f:
                        json.dump(self.outline, f, ensure_ascii=False, indent=2)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        else:
            if temp_path.exists():
                temp_path.unlink()
        # 保存后同步更新 locator 索引
        self._build_locator_index(self.outline)

    def _log_operation(self, operation: str, **kwargs) -> None:
        """记录操作日志

        Args:
            operation: 操作类型
            **kwargs: 操作参数
        """
        import time
        log = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'operation': operation,
            **kwargs
        }
        self.operation_logs.append(log)

    def _find_node_by_locator(self, locator: str) -> Optional[Dict[str, Any]]:
        """
        根据 locator 查找节点（O(1) 哈希索引查找）
        支持格式：1, 1.1, 1.1.1, 4.4.1, 5.2.1 等
        """
        target = locator.strip()
        node = self._locator_index.get(target)
        if node is not None:
            return node

        # 容错：某些场景下大纲结构已变，索引未更新，走遍历兜底
        for section in self.outline['标准化模板文本']['正文章节']:
            if section.get('locator') == target:
                return section
            for clause in section.get('条款列表', []):
                if clause.get('locator') == target:
                    return clause
                for sub_clause in clause.get('子条款列表', []):
                    if sub_clause.get('locator') == target:
                        return sub_clause
                    for sub_sub_clause in sub_clause.get('子子条款列表', []):
                        if sub_sub_clause.get('locator') == target:
                            return sub_sub_clause
        return None

    def _find_parent_and_index(self, locator: str) -> tuple:
        """查找节点的父节点和索引

        Args:
            locator: 节点定位标识

        Returns:
            (父节点, 节点索引, 节点类型)
        """
        parts = list(map(int, locator.split('.')))

        if len(parts) == 1:
            # 章节，父节点是正文章节列表
            return self.outline['标准化模板文本']['正文章节'], parts[0] - 1, 'section'
        elif len(parts) == 2:
            # 条款，父节点是章节的条款列表
            section = self.outline['标准化模板文本']['正文章节'][parts[0] - 1]
            return section['条款列表'], parts[1] - 1, 'clause'
        elif len(parts) == 3:
            # 子条款，父节点是条款的子条款列表
            section = self.outline['标准化模板文本']['正文章节'][parts[0] - 1]
            clause = section['条款列表'][parts[1] - 1]
            return clause.get('子条款列表', []), parts[2] - 1, 'sub_clause'
        elif len(parts) == 4:
            # 子子条款，父节点是子条款的子子条款列表
            section = self.outline['标准化模板文本']['正文章节'][parts[0] - 1]
            clause = section['条款列表'][parts[1] - 1]
            sub_clause = clause.get('子条款列表', [])[parts[2] - 1]
            return sub_clause.get('子子条款列表', []), parts[3] - 1, 'sub_sub_clause'
        return None, None, None

    def get_outline(self) -> Dict[str, Any]:
        """获取大纲数据

        Returns:
            大纲数据结构
        """
        return self.outline

    def get_match_result(self) -> List[Dict[str, Any]]:
        """获取匹配结果

        Returns:
            匹配结果列表
        """
        return self.match_result

    def get_operation_logs(self) -> List[Dict[str, Any]]:
        """获取操作日志

        Returns:
            操作日志列表
        """
        return self.operation_logs

    def save_outline(self) -> None:
        """保存大纲到文件"""
        self._save_outline()

    def find_node(self, locator: str) -> Optional[Dict[str, Any]]:
        """查找节点

        Args:
            locator: 节点定位标识

        Returns:
            找到的节点，若未找到返回None
        """
        return self._find_node_by_locator(locator)

    def find_parent(self, locator: str) -> tuple:
        """查找父节点和索引

        Args:
            locator: 节点定位标识

        Returns:
            (父节点, 节点索引, 节点类型)
        """
        return self._find_parent_and_index(locator)

    # ==================== 合同首部/尾部操作方法（整体替换，不经过 locator 系统） ====================

    def get_header(self) -> str:
        """获取合同首部内容

        Returns:
            合同首部文本
        """
        return self.outline.get('标准化模板文本', {}).get('合同首部', '')

    def get_footer(self) -> str:
        """获取合同尾部内容

        Returns:
            合同尾部文本
        """
        return self.outline.get('标准化模板文本', {}).get('合同尾部', '')

    def update_header(self, content: str) -> str:
        """整体替换合同首部内容

        Args:
            content: 新的首部内容

        Returns:
            操作结果
        """
        if '标准化模板文本' not in self.outline:
            return "错误：大纲中不存在'标准化模板文本'"
        old_content = self.outline['标准化模板文本'].get('合同首部', '')
        self.outline['标准化模板文本']['合同首部'] = content
        self._log_operation('update_header', old_content=old_content, new_content=content)
        return f"成功更新合同首部，旧内容：{old_content}，新内容：{content}\n\n"

    def update_footer(self, content: str) -> str:
        """整体替换合同尾部内容

        Args:
            content: 新的尾部内容

        Returns:
            操作结果
        """
        if '标准化模板文本' not in self.outline:
            return "错误：大纲中不存在'标准化模板文本'"
        old_content = self.outline['标准化模板文本'].get('合同尾部', '')
        self.outline['标准化模板文本']['合同尾部'] = content
        self._log_operation('update_footer', old_content=old_content, new_content=content)
        return f"成功更新合同尾部，旧内容：{old_content}，新内容：{content}\n\n"

    def log_operation(self, operation: str, **kwargs) -> None:
        """记录操作日志

        Args:
            operation: 操作类型
            **kwargs: 操作参数
        """
        self._log_operation(operation, **kwargs)

    # ==================== 备份和版本控制相关方法 ====================

    def create_backup(self, description: str = "") -> str:
        """创建当前大纲的备份

        Args:
            description: 备份描述

        Returns:
            备份文件路径
        """
        # 创建备份目录
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"outline_backup_{timestamp}.json"
        backup_path = os.path.join(self.backup_dir, backup_name)

        # 保存大纲数据
        with open(self.outline_path, 'r', encoding='utf-8') as src:
            outline_data = json.load(src)

        # 添加元数据
        backup_data = {
            "metadata": {
                "timestamp": timestamp,
                "description": description,
                "outline_path": self.outline_path
            },
            "outline": outline_data
        }

        with open(backup_path, 'w', encoding='utf-8') as dst:
            json.dump(backup_data, dst, ensure_ascii=False, indent=2)

        # 清理旧备份
        self._cleanup_old_backups()

        return backup_path

    def restore_from_backup(self, backup_path: str = None, backup_index: int = None) -> bool:
        """从备份恢复大纲

        Args:
            backup_path: 备份文件路径（如果为None，使用最近的备份）
            backup_index: 备份索引（如果为None，使用最近的备份）

        Returns:
            是否恢复成功
        """
        if backup_path is None and backup_index is not None:
            backups = self.list_backups()
            if 0 <= backup_index < len(backups):
                backup_path = backups[backup_index]['path']

        if backup_path is None:
            # 使用最近的备份
            backups = self.list_backups()
            if backups:
                backup_path = backups[0]['path']
            else:
                return False

        try:
            with open(backup_path, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)

            # 恢复到大纲
            self.outline = backup_data['outline']
            self._save_outline()

            # 重新加载locators
            self._add_locators(self.outline)

            # 记录恢复操作
            self._log_operation('restore', backup_path=backup_path)

            return True
        except Exception as e:
            print(f"恢复备份失败: {e}")
            return False

    def list_backups(self) -> List[Dict[str, Any]]:
        """列出所有可用的备份

        Returns:
            备份列表（按时间倒序）
        """
        backups = []
        if not os.path.exists(self.backup_dir):
            return backups

        for file_name in os.listdir(self.backup_dir):
            if file_name.startswith("outline_backup_") and file_name.endswith(".json"):
                file_path = os.path.join(self.backup_dir, file_name)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        backup_data = json.load(f)
                    backups.append({
                        'path': file_path,
                        'timestamp': backup_data['metadata']['timestamp'],
                        'description': backup_data['metadata'].get('description', ''),
                        'size': os.path.getsize(file_path)
                    })
                except Exception:
                    continue

        # 按时间倒序排列
        backups.sort(key=lambda x: x['timestamp'], reverse=True)
        return backups

    def _cleanup_old_backups(self):
        """清理旧的备份文件"""
        backups = self.list_backups()
        if len(backups) > self.max_backups:
            # 删除最旧的备份
            for backup in backups[self.max_backups:]:
                try:
                    os.remove(backup['path'])
                except Exception:
                    pass

    def get_backup_info(self) -> str:
        """获取备份信息

        Returns:
            备份信息文本
        """
        backups = self.list_backups()
        if not backups:
            return "暂无备份"

        info_parts = [f"共 {len(backups)} 个备份："]
        for i, backup in enumerate(backups[:5]):  # 只显示最近5个
            info_parts.append(f"{i+1}. {backup['timestamp']} - {backup['description'] or '无描述'}")

        if len(backups) > 5:
            info_parts.append(f"... 还有 {len(backups) - 5} 个更旧的备份")

        return '\n'.join(info_parts)

    def _format_outline(self, show_locator: bool = True) -> str:
        """格式化大纲结构为可读字符串
        
        Args:
            outline: 大纲对象
            show_locator: 是否显示 locator 信息
            
        Returns:
            格式化后的大纲字符串
        """
        outline = self.get_outline()
        result = []
        
        # 处理标准化模板文本
        if '标准化模板文本' in outline:
            standard_template = outline['标准化模板文本']
            if "合同首部" in standard_template:
                result.append(standard_template["合同首部"])
            # 处理正文章节
            if '正文章节' in standard_template:
                for section in standard_template['正文章节']:
                    section_num = section.get('章节编号', '')
                    section_title = section.get('章节标题', '')
                    section_locator = section.get('locator', '')
                    result.append(f"{section_num} {section_title}{f' (locator: {section_locator})' if show_locator else ''}")
                    
                    # 处理条款
                    if '条款列表' in section:
                        for clause in section['条款列表']:
                            clause_num = clause.get('条款编号', '')
                            clause_desc = clause.get('条款大致说明', '')
                            clause_locator = clause.get('locator', '')
                            result.append(f"  {clause_num} {clause_desc}{f' (locator: {clause_locator})' if show_locator else ''}")
                            
                            # 处理子条款
                            if '子条款列表' in clause:
                                for sub_clause in clause['子条款列表']:
                                    sub_clause_num = sub_clause.get('子条款编号', '')
                                    sub_clause_desc = sub_clause.get('子条款大致说明', '')
                                    sub_clause_locator = sub_clause.get('locator', '')
                                    result.append(f"    {sub_clause_num} {sub_clause_desc}{f' (locator: {sub_clause_locator})' if show_locator else ''}")
                                    
                                    # 处理子子条款
                                    if '子子条款列表' in sub_clause:
                                        for sub_sub_clause in sub_clause['子子条款列表']:
                                            sub_sub_clause_num = sub_sub_clause.get('子子条款编号', '')
                                            sub_sub_clause_desc = sub_sub_clause.get('子子条款大致说明', '')
                                            sub_sub_clause_locator = sub_sub_clause.get('locator', '')
                                            result.append(f"      {sub_sub_clause_num} {sub_sub_clause_desc}{f' (locator: {sub_sub_clause_locator})' if show_locator else ''}")
            if "合同尾部" in standard_template:
                result.append(standard_template["合同尾部"])
        return '\n'.join(result)
    

    def snapshot(self):
        """返回可恢复的状态（深拷贝当前内部数据）"""
        import copy
        return copy.deepcopy({
            'outline': self.outline,          # 你的大纲数据结构
        })
    
    def restore(self, snapshot):
        """从快照恢复"""
        self.outline = snapshot['outline']
    
if __name__ == "__main__":
    # 测试OutlineManager
    outline_manager = OutlineManager(Path("C:/Users/HUAWEI/Desktop/明鉴智律公司/ContractGeneration/contract_generation/Agent_generation/agent/data/merged_outline.json"),Path("C:/Users/HUAWEI/Desktop/明鉴智律公司/ContractGeneration/contract_generation/Agent_generation/agent/data/match_result.json"))
    # print(json.dumps(outline_manager.get_outline(), ensure_ascii=False, indent=2))
    # print(json.dumps(outline_manager.find_node("5.1.1"), ensure_ascii=False, indent=2))
    
    
    tetx = outline_manager._format_outline()
    print(tetx)