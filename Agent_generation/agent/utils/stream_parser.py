#!/usr/bin/env python3
"""
流式 JSON KeyPath 追踪器

在 LLM 流式输出 JSON 的过程中，逐字符追踪每个字符所属的 key path，
并发射 field-specific token 事件。不依赖任何 schema 信息，
只做纯语法分析。
"""


class KeyPathTracker:
    """追踪流式 JSON 中每个字符所属的 key path

    零配置，无需知道字段名。输出 (key_path, char) 对，
    由外部根据 STREAM_EVENT_MAP 决定如何路由。
    """

    def __init__(self):
        self.stack = []              # key 路径栈，如 ["parameter"]
        self.current_key = ""        # 当前正在积累的 key 名
        self.in_string = False       # 是否在字符串内
        self.expecting_key = True    # 刚遇到 { 或 ,，期待 key
        self.after_colon = False     # 刚遇到 :，后面的字符串是 value
        self.escaped = False         # 转义模式

    def feed(self, text: str):
        """喂入一段 token 文本

        Yields:
            (key_path_tuple, char): (("parameter", "answer_text"), "好")
        """
        for ch in text:
            yield from self._feed_char(ch)

    def _feed_char(self, ch: str):
        if self.escaped:
            self.escaped = False
            if self.after_colon:
                yield (tuple(self.stack + [self.current_key]) if self.current_key else tuple(self.stack), ch)
            return

        if ch == '\\' and self.in_string:
            self.escaped = True
            if self.after_colon:
                yield (tuple(self.stack + [self.current_key]) if self.current_key else tuple(self.stack), ch)
            return

        if ch == '"':
            if not self.in_string:
                self.in_string = True
                self.current_key = ""
                # 刚遇到 "，开始积累 key
            else:
                # 字符串结束
                self.in_string = False
                if self.expecting_key and self.current_key:
                    # 积累完成一个 key
                    self.stack.append(self.current_key)
                    self.expecting_key = False
                    self.current_key = ""
                elif self.after_colon:
                    # 值字符串结束
                    self.after_colon = False
            return

        if self.in_string:
            if self.expecting_key:
                self.current_key += ch
            else:
                # 在 value 字符串中
                key_path = tuple(self.stack)
                self.current_key += ch
                yield (key_path, ch)
            return

        if ch == ':':
            self.after_colon = True
            # 此时 stack 顶部就是当前 key
            return

        if ch == '{':
            if self.after_colon:
                # 嵌套对象，保持当前 key 在栈顶
                self.after_colon = False
            self.expecting_key = True
            return

        if ch == '}':
            if self.stack:
                self.stack.pop()
            self.expecting_key = False
            self.after_colon = False
            return

        if ch == ',':
            self.expecting_key = True
            self.after_colon = False
            if self.stack:
                self.stack.pop()
            return

        if ch == '[':
            return

        if ch == ']':
            return

        # 非字符串值（true/false/null/数字）
        if self.after_colon:
            key_path = tuple(self.stack)
            yield (key_path, ch)

    def reset(self):
        self.stack.clear()
        self.current_key = ""
        self.in_string = False
        self.expecting_key = True
        self.after_colon = False
        self.escaped = False
