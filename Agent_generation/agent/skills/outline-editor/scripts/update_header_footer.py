#!/usr/bin/env python3
"""
合同首部/尾部编辑工具

description: 整体替换合同首部或尾部内容。合同首部和尾部是整体文本段落，不通过数字locator定位，而是通过工具名称区分操作目标。
params:
  target: 操作目标，可选值 "header"（合同首部）或 "footer"（合同尾部）
  content: 替换后的完整内容
  operation: 操作类型，可选值 "update"（更新）、"get"（获取）
returns: 操作结果
"""

class UpdateHeaderFooterTool:
    def __init__(self, outline_manager):
        self.outline_manager = outline_manager

    def execute(self, target: str, content: str = "", operation: str = "update") -> str:
        try:
            if target not in ("header", "footer"):
                return "错误：target 参数必须为 'header' 或 'footer'"

            if operation == "get":
                if target == "header":
                    current = self.outline_manager.get_header()
                else:
                    current = self.outline_manager.get_footer()
                target_name = "合同首部" if target == "header" else "合同尾部"
                return f"当前{target_name}内容：\n{current}"

            if operation == "update":
                if not content:
                    return "错误：operation 为 'update' 时 content 不能为空"
                if target == "header":
                    return self.outline_manager.update_header(content)
                else:
                    return self.outline_manager.update_footer(content)

            return f"错误：不支持的 operation '{operation}'，仅支持 'update' 和 'get'"
        except Exception as e:
            return f"操作合同首部/尾部时出错：{str(e)}"
