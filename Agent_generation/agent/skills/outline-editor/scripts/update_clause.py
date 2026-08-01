#!/usr/bin/env python3
"""
更新条款内容

description: 更新指定条款内容，支持多个同级别locator
params:
  locator: 节点定位标识，可传入单个字符串或同级别locator列表
  content: 新内容，与locator一一对应，可传入单个字符串或列表
returns: 操作结果
"""
import sys
import os
import re

class UpdateClauseTool:
    def __init__(self, outline_manager):
        """初始化更新条款工具
        
        Args:
            outline_manager: OutlineManager实例
        """
        self.outline_manager = outline_manager
    
    def execute(self, locator, content) -> str:
        """执行更新条款操作
        
        Args:
            locator: 节点定位标识，可传入单个字符串或同级别locator列表
            content: 新内容，与locator一一对应，可传入单个字符串或列表
            
        Returns:
            操作结果
        """
        try:
            # 处理单个locator的情况
            if isinstance(locator, str):
                info = self._update_clause(locator, content)
                return info
            
            # 处理多个locator的情况
            elif isinstance(locator, list):
                if not isinstance(content, list) or len(locator) != len(content):
                    return "locator列表与content列表长度不一致"
                
                # 检查所有locator是否同级别
                # levels = [len(l.split('.')) for l in locator]
                # if len(set(levels)) > 1:
                #     return "所有locator必须是同级别"
                
                results = []
                for l, c in zip(locator, content):
                    result = self._update_clause(l, c)
                    results.append(result)
            
                return "\n".join(results)\
                    # + "\n修改后的合同：\n" + self.outline_manager._format_outline()
        except Exception as e:
            return f"更新条款内容时出错: {str(e)}"

    import re

    def clean_clause_content(self,content: str) -> str:
        """
        安全清洗条款内容：
        1. 只删除【行首】的层级编号（如 8.1 / 8.2.1 / 8.2.1.1）
        2. 句子中间的编号（详见8.2.1、第1.1条）完全不碰
        3. 删除行首缩进
        4. 保留正常换行与格式
        例如：
            8.1 甲方违约责任
              8.1.1 若甲方违约，详见8.2.1条款
              8.1.2 具体按照第1.2条执行
        会变成
            甲方违约责任；若甲方违约，详见8.2.1条款；具体按照第1.2条执行
        """
        lines = content.splitlines()
        cleaned = []
        for line in lines:
            # 1. 去掉行首空白（缩进）
            stripped = line.lstrip()
            # 2. 【只删除行首的层级编号】，句子中间的编号完全不处理
            # 正则含义：
            # ^          行首
            # (\d+\.)+    数字+点 的组合（1. / 1.2. / 8.1.2.）
            # \d+        最后一段数字
            # \s*        后面可能的空格
            cleaned_line = re.sub(r'^(\d+\.)+\d+\s*', '', stripped)
            cleaned.append(cleaned_line)
        # 清理空行并返回
        return '；'.join([line for line in cleaned if line.strip()])

    def _update_clause(self, locator: str, new_content: str) -> str:
        """修改条款内容
        
        Args:
            locator: 节点定位标识
            new_content: 新内容
            
        Returns:
            操作结果
        """
        new_content = self.clean_clause_content(new_content)

        node = self.outline_manager.find_node(locator)
        if not node:
            return f"未找到定位标识为 {locator} 的节点"
        
        # 保存旧内容
        old_content = node.get('条款大致说明') or node.get('子条款大致说明') or node.get('子子条款大致说明')
        
        # 更新内容
        if '条款大致说明' in node:
            node['条款大致说明'] = new_content
        elif '子条款大致说明' in node:
            node['子条款大致说明'] = new_content
        elif '子子条款大致说明' in node:
            node['子子条款大致说明'] = new_content
        else:
            return "无法更新该节点内容"
        
        # 记录操作
        self.outline_manager.log_operation('update_clause', locator=locator, old_content=old_content, new_content=new_content)
        return f"成功修改 {locator} 条款内容，旧内容：{old_content}，新内容：{new_content}\n"

if __name__ == '__main__':
    from pathlib import Path
    # from ....utils.outline_manager import OutlineManager

    _AGENT_DIR = Path(__file__).resolve().parent.parent.parent.parent  # scripts/ → outline-editor/ → skills/ → agent/
    if str(_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(_AGENT_DIR))

    from utils.outline_manager import OutlineManager
    outline_manager = OutlineManager(Path("C:/Users/HUAWEI/Desktop/明鉴智律公司/ContractGeneration/contract_generation/Agent_generation/agent/data/outline_chunk_2.json"),Path("C:/Users/HUAWEI/Desktop/明鉴智律公司/ContractGeneration/contract_generation/Agent_generation/agent/data/match_result.json"))
    update_clause_tool = UpdateClauseTool(outline_manager)
    locator = ['5.2.1','5.2.2','5.2.3','6.1']
    content = ['更新后的条款内容1','更新后的条款内容2','更新后的条款内容3','更新后的条款内容4']
    result = update_clause_tool.execute(locator, content)
    print(result)