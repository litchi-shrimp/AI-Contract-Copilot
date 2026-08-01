import textwrap
import json
import sys
import threading
from pathlib import Path
from ..core.base_agent import BaseAgent, AGENTS_DIR, AGENT_DIR, BASE_DIR
from ..utils.tool_manager import ToolManager
from ..utils.outline_manager import OutlineManager
from ..llm.prompt_builder import (
    OutlineGeneratorWithoutTemplatePrompt,
    AdversarialOutlineAgentPrompt,
)


class OutlineGeneratorWithoutTemplate(BaseAgent):
    """零模板大纲生成器（ModelA）

    在模板库无匹配时，通过 WebSearch + LLM 自身知识生成合同大纲骨架。
    WebSearch 结果直接注入上下文，同时后台异步抽取结构化模板存入模板库。
    """

    def __init__(self, agent_name: str, user_need: dict):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_outline_adversarial.json")
        self.user_need = user_need
        self.web_search_results = None
        self.current_outline = {}

    def _initial_llm_call(self, system_prompt: str, user_prompt: str) -> str:
        from ..agents.config import AgentConfig
        cfg = AgentConfig(BASE_DIR / "configs" / "config_outline_adversarial_initial.json")
        return cfg.llm_call(system_prompt, user_prompt)

    def _web_search(self) -> str:
        """WebSearch(Query:xxx合同) → Top8 搜索结果，返回格式化的上下文文本"""

        # Step 1: 加载 WebSearch 工具
        import importlib.util
        web_search_path = AGENT_DIR / "skills" / "search-reference" / "scripts" / "web_search.py"
        spec = importlib.util.spec_from_file_location("web_search", web_search_path)
        ws_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ws_module)
        WebSearchTool = ws_module.WebSearchTool

        # Step 2: 获取用户需求中的合同类型，并拼接设计Web查询Query
        collected = self.user_need.get("collected", {})
        contract_type = collected.get("contract_type", "合同")
        query = f"{contract_type}合同"

        # Step 3: 启动 WebSearch 工具，获取 Top8 搜索结果
        tool = WebSearchTool()
        results = tool.get_search_results(query, num_results=8)
        if not results:
            return "网络搜索结果为空"
        self.web_search_results = results
        # Step 4: 格式化 WebSearch 的输出结果（用于后续prompt拼接）
        formatted_parts = []
        for i, r in enumerate(results[: 5], 1):
            title = r.get("title", "")
            content = r.get("content", "")
            summary = r.get("summary", "")
            formatted_parts.append(f"[参考{i}] {title}\ncontent:{content}\nsummary:{summary}")
        return "\n\n".join(formatted_parts)

    def generate_outline(self) -> dict:
        print("[ModelA] 启动 WebSearch 获取参考信息...", file=sys.stderr)
        web_context = self._web_search()
        print(f"[ModelA] WebSearch 完成，获取 {len(self.web_search_results or [])} 条结果", file=sys.stderr)

        # self._background_extract_to_library()     # 后台异步抽取模板（现在暂时不开启）
        # 构建大纲初始生成prompt
        prompt_builder = OutlineGeneratorWithoutTemplatePrompt(self.user_need, web_context)
        system_prompt = prompt_builder.build_system_prompt()
        user_prompt = prompt_builder.build_user_prompt()
        # 调用LLM生成初始大纲骨架
        response = self._initial_llm_call(system_prompt, user_prompt)
        json_data = self.response_parser.parse_agent_response(response)
        if json_data and "error" not in json_data:
            self.current_outline = json_data
        # 返回初始大纲JSON（注意：后续还要经过对刚Agent润色）
        return json_data

    def _background_extract_to_library(self):
        """后台异步：从 WebSearch 结果中提取结构化模板，存入模板库"""
        if not self.web_search_results:
            return

        def _extract_and_save():
            try:
                _extract_templates_from_web_results(self.web_search_results)
            except Exception as e:
                print(f"[后台建库] 提取失败: {e}", file=sys.stderr)

        thread = threading.Thread(target=_extract_and_save, daemon=True)
        thread.start()


def _extract_templates_from_web_results(web_results: list):
    """
    从 WebSearch 结果中提取结构化模板，存入模板库。
    流程：
    1. 从 WebSearch 结果中提取所有文本内容
    2. 调用轻量 LLM 模型判断每个文本是否为完整合同/协议正文（判断标准很严苛）
    3. 如果是满足条件（即原网页中包含一个完整的且可作为模板参考的合同），将其分类并存入模板库（按照原有模板抽取的逻辑，只是source字段统一标注为"web_search"）
    4. 如果不是，跳过
       """
    from ..agents.config import AgentConfig
    from datetime import datetime

    TEMPLATE_LIBRARY_DIR = BASE_DIR.parent / "template_library"
    TEMPLATE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    all_classify_path = TEMPLATE_LIBRARY_DIR / "all_classify.json"
    # 读取模板库目录下的classify_data，获取所有合同类型（100多类），用于后续传给LLM让其从这里抽类型
    classify_data = {}
    try:
        with open(all_classify_path, "r", encoding="utf-8") as f:
            classify_data = json.load(f)
    except Exception:
        pass

    bg = AgentConfig(BASE_DIR / "configs" / "config_outline_adversarial_background.json")
    bg_llm = bg.llm_call
    sys.path.insert(0, str(BASE_DIR.parent))
    try:
        from Prompts import build_contract_system_prompt, build_contract_user_prompt
    finally:
        sys.path.pop(0)

    QUALIFY_PROMPT = textwrap.dedent("""
    你是一个**严苛**的法律文档审核专家。判断以下**网页抓取文本**中是否包含一份**值得作为模板参考的完整合同/协议正文**。
    注意：网页抓取文本常含大量噪音（导航栏、广告、推荐列表、版权声明等），请忽略这些噪音，仅判断核心正文区域是否有完整合同。
    
    【判断规则（需同时满足）】
        1. **完整性**：包含合同/协议的核心要素，如双方主体、标的/服务内容、权利义务、价款/支付、违约责任、争议解决等，且内容连贯、无明显截断。
        2. **可参考性**：条款表述清晰、逻辑通顺，具备一般商业或法律合同的常见结构（如分条、标题或段落分节），而非单纯摘要、部分摘录或碎片化对话。
        3. **模板价值**：内容足够具体，可被直接复用或少量修改后用于类似场景，而非仅单次交易的简陋便条或极简声明。
    
    【否定情形（满足任意1条即判定为非完整合同，false）】
        1. 仅为目录/大纲，无任何条款正文。
        2. 合同不完整，缺失首部、正文或尾部中的任意一部分。
        3. 仅为Q&A问答、咨询解答、普法文章。
        4. 仅为合同审查要点清单、风险提示（不是模板本身）。
        5. 仅为空白表单、登记表、申请表（无权利义务约定）。
        6. 仅为商业广告、律所宣传页、招聘信息。
        7. 每个条款实质性描述少于30字，且无具体权利义务指向。
    
    【特殊情况】
        - 若网页包含多份合同，只要其中至少一份完整，即判定为 true。
        - 仅展示条款示例但无首尾部的，判定为 false。
    
    【输出格式】
        严格输出 JSON，不要有任何额外文本：
            ```json
            {
              "type": "object",
              "properties": {
                "is_contract": {
                  "type": "boolean",
                  "description": "是否包含可作为模板参考的完整合同/协议正文"
                },
                "reason": {
                  "type": "string",
                  "maxLength": 50,
                  "description": "简短理由，说明满足或违反哪条核心规则"
                }
              },
              "required": ["is_contract", "reason"]
            }
    """).strip()

    METADATA_SYSTEM_PROMPT = textwrap.dedent("""
    你是专业的合同元数据自动分析专家。
    你只做一件事：从合同正文里自动识别并输出合同的元数据标签，严格按指定JSON输出，不输出任何多余内容。
    contract_type需要从我给你的"合同类型清单"中选择最符合的合同类型，只能选择一个，禁止自创类别。
    """).strip()

    METADATA_USER_PROMPT_TEMPLATE = textwrap.dedent("""
    请分析以下合同文本，自动抽取元数据：

    要求识别：
    1. contract_type：从我给你的"合同类型清单"中选择最符合的合同类型，只能选择一个，禁止自创类别。
       注意：请递归寻找到最细粒度的类别(级别依次用.隔开)，例如"建设工程合同": {"勘察合同": {"ID": "Construction_Survey"}}
       请输出"建设工程合同.勘察合同"，而不是"建设工程合同"或"勘察合同"。
    2. party_type：示例：个人-个人 / 个人-企业 / 企业-企业 / 企业-个人...当然如果有其他更合适的类型选择，也请输出类型。
    3. scene：示例：居住 / 商铺 / 办公 / 厂房 / 民间借贷 / 金融借贷 / 货物买卖 / 服务提供 / 运输...当然如果有其他更合适的场景，也请输出类型。
    4. complexity：示例：简易 / 标准 / 复杂 / 律所版...当然如果有其他更合适的类型选择，也请输出类型。
    5. region：示例：通用 / 北京 / 上海 / 广东 / 江苏 / 浙江 / 深圳 / 其他...当然如果有其他更合适的地区，也请输出类型。

    输出严格JSON格式，不要解释。
    JSON格式：{"contract_type": "...", "party_type": "...", "scene": "...", "complexity": "...", "region": "..."}
    """).strip()

    ABSTRACT_PROMPT = textwrap.dedent("""
    你是专业合同律师，请为以下合同模板生成一段**高度概括、结构统一、适合向量检索**的核心摘要。
    摘要必须包含：合同类型、适用场景与目的、签约双方角色、核心业务约定、重要特殊条款、关键法律约定、合同复杂度、适用地区。
    长度控制在 100～200 字，只输出一段连贯通顺的摘要文本，不输出任何格式。
    """).strip()

    # 遍历所有WebSearch结果，如果判断是完整合同（值得作为模板），就开始抽取放到模板库中。
    for i, result in enumerate(web_results):
        content = f"content:{result.get('content', '')}\nsummary:{result.get('summary', '')}"
        if not content or len(content) < 200:  # 如果字数太少，肯定不是合同，跳过
            continue
        truncated = content
        # 质量检查：是否为有效合同（一个轻量LLM判断）
        try:
            q_response = bg_llm(QUALIFY_PROMPT, f"判断以下**网页抓取文本**是否为合同：\n\n{truncated}...")
            q_data = json.loads(extract_json_from_response(q_response))
            if not q_data.get("is_contract", False):
                print(f"[后台建库] 跳过（非合同）: {result.get('title', '')[:40]}", file=sys.stderr)
                continue
        except Exception:
            continue

        # 如果到这一步，说明判断为有效合同，开始抽取模板正文
        print(f"[后台建库] 合格，开始提取: {result.get('title', '')[:40]}", file=sys.stderr)
        # 在提示词中说明需要过滤无关信息，只保留合同正文区域
        FILTER_INFO = textwrap.dedent("""
            【需要忽略的信息(必须丢弃（这些不是合同内容）)】
                - 网站路径/导航（如"您所在位置：人力资源/企业管理"）
                - 文档推荐列表（如"公司红酒采购合同范本.docx""您可能关注的文档"）
                - 数字平台元数据（"约xx千字""发布于xx""VIP精品文档"）
                - 版权/备案/许可证信息（"公安局备案号""蜀ICP备""出版物经营许可证"）
                - 平台介绍文字（"原创力文档从xxx年开站以来""知识共享平台"）
                - 联系方式/举报电话/QQ群号
                - 任何不属于合同正文的段落
            注意：仅将属于合同的内容提取模板，忽略所有无关信息（见【需要忽略的信息】）。如果忽略后合同结构为空，自然保留空列表或空字符串即可（如首部和尾部留空）。
            """).strip()
        # Step1：抽取结构化模板正文
        system_prompt = build_contract_system_prompt()+FILTER_INFO
        user_prompt = build_contract_user_prompt(truncated)
        try:
            response = bg_llm(system_prompt, user_prompt)
            json_str = extract_json_from_response(response)
            json_data = json.loads(json_str)
            if "标准化模板文本" not in json_data:
                continue
        except Exception:
            continue

        # Step2：提取元数据（完全遵循 MetadataContractExtractionAgent 的逻辑）
        metadata_user_prompt = METADATA_USER_PROMPT_TEMPLATE + f"\n合同文本：\n{truncated}\n合同类型清单：\n{classify_data}"
        try:
            m_response = bg_llm(METADATA_SYSTEM_PROMPT, metadata_user_prompt)
            m_data = json.loads(extract_json_from_response(m_response))
        except Exception:
            m_data = {}
        # 从 classify_data 中获取当前类型的ID，比如：“投资与股权类合同.股东协议”对应“Invest_Shareholder”（这里需要考虑类型层级关系）
        contract_type = m_data.get("contract_type", "") or "网络搜索模板"  # 如“投资与股权类合同.股东协议”
        template_id = _generate_template_id(contract_type, classify_data)  # 如“Invest_Shareholder”，后续就存在模板库的Invest_Shareholder.json中（增量添加）

        # 生成 template_name（遵循 TemplateMerger.generate_template_name）
        pt = m_data.get("party_type", "通用")
        cp = m_data.get("complexity", "标准")
        rg = "地区" + (m_data.get("region", "通用") or "通用")
        template_name = f"{contract_type}（{pt}・{cp}・{rg}）"   # 如“投资与股权类合同.股东协议（通用・标准・地区）”

        # 生成摘要
        plain_text = _render_simple_plain_text(json_data.get("标准化模板文本", {}))
        a_response = bg_llm(ABSTRACT_PROMPT, f"合同模板：\n{plain_text}")
        abstract = a_response.strip()

        # 构建入库记录（字段与 make_template_library.py TemplateMerger.merge 完全对齐）
        title = result.get("title", f"网络搜索结果_{i}")
        template_record = {
            "template_id": template_id,
            "template_name": template_name,
            "contract_type": contract_type,
            "template_version": "v1.0",
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_file": result.get("url", ""),
            "party_type": m_data.get("party_type", "通用"),
            "scene": m_data.get("scene", "通用"),
            "complexity": m_data.get("complexity", "标准"),
            "region": m_data.get("region", "通用"),
            "is_recommended": 0,
            "source": "websearch",
            "source_url": result.get("url", ""),
            "abstract": abstract,
            "standard_template_content": json_data.get("标准化模板文本", {}),
        }

        # 增量写入（完全遵循 process_one_contract 的写入逻辑）（先读取出来，再添加，最后写回）
        output_file = TEMPLATE_LIBRARY_DIR / f"{template_id}.json"  # 如“Invest_Shareholder.json”
        existing_data = {}
        if output_file.exists():
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except Exception:
                existing_data = {}
        next_index = str(len(existing_data))
        existing_data[next_index] = template_record
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=4)
        print(f"[后台建库] 已入库: {template_id}.json[{next_index}] ← {title[:40]}", file=sys.stderr)


def _generate_template_id(contract_type: str, classify_data: dict) -> str:
    """从 all_classify.json 中获取唯一 ID（完全遵循 TemplateMerger.generate_template_id 逻辑）"""
    parts = contract_type.split(".")  # 如“投资与股权类合同.股东协议”拆为“投资与股权类合同”、“股东协议”
    def find_id(data, path):
        if not path:
            return data.get("ID", "TPL-OTHER-AUTO")
        current = path[0]
        if current in data and isinstance(data[current], dict):
            return find_id(data[current], path[1:])
        return "TPL-OTHER-AUTO"
    return find_id(classify_data, parts)


def _render_simple_plain_text(template_text: dict) -> str:
    # 将结构化JSON渲染为简单的文本格式（不包含格式），变成纯合同文本，用于输入LLM生成摘要
    lines = []
    header = template_text.get("合同首部", "").strip()
    if header:
        lines.append(header)
    for chapter in template_text.get("正文章节", []):
        lines.append(f"{chapter.get('章节编号', '')} {chapter.get('章节标题', '')}".strip())
        for clause in chapter.get("条款列表", []):
            lines.append(f"  {clause.get('条款编号', '')} {clause.get('条款内容', '')}".strip())
            for sub in clause.get("子条款列表", []):
                lines.append(f"    {sub.get('子条款编号', '')} {sub.get('子条款内容', '')}".strip())
    footer = template_text.get("合同尾部", "").strip()
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def extract_json_from_response(text: str) -> str:
    text = text.strip()
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()
    if "{" in text:
        start = text.find("{")
        count = 1
        for i in range(start + 1, len(text)):
            if text[i] == "{":
                count += 1
            elif text[i] == "}":
                count -= 1
                if count == 0:
                    return text[start:i + 1].strip()
    return text.strip()


# ==================== 对抗式大纲生成 Agent ====================

class AdversarialOutlineAgent(BaseAgent):
    """对抗式大纲生成 Agent（双模型对抗 + 双层循环）

    Round 0: ModelA1 (deepseek-v3.2, T=0.3) → WebSearch → 生成初始大纲
    Round 1-N: 对抗循环
        ModelB (deepseek-v3.2, T=0.8) 审查完整性（不输出JSON）
        ModelA2 (deepseek-v4-flash, T=0.3) 针对审查输出调用工具方案（快速）
        若"完备" → 结束
        若有遗漏 → ModelA2 ReAct 内循环（insert_clause / update_clause / update_header_footer）
        修复完成后进入下一轮外循环 ModelB 再审查
    
    N 最大值为 3
    """

    # 这里定义了 Agent 的流事件映射，用于处理不同类型的事件，便于后续实时解析JSON实现同时流式输出不同类型的内容
    STREAM_EVENT_MAP = {
        ("think",): "agent.think.stream",
        ("act",): "agent.act.stream",
        ("parameter", "answer_text"): "agent.answer.stream",
    }

    def __init__(self, agent_name: str, user_need: dict):
        super().__init__(agent_name, BASE_DIR / "configs" / "config_outline_adversarial.json")

        self.user_need = user_need
        self.tool_list_path = AGENTS_DIR / "outline_adversarial_agent_tools.json"
        # 预注册工具（insert_clause / update_clause / update_header_footer），与 DrafterAgent 模式一致，不给delete
        self.tools = json.load(open(self.tool_list_path, "r", encoding="utf-8"))

        self._anti_round = 0
        # 累积所有外循环 ModelB 的审查结果，供后续轮次参考
        self.review_history = []

    def _reviewer_llm_call(self, system_prompt: str, user_prompt: str) -> str:
        """ModelB 专用审查 LLM 调用"""
        from ..agents.config import AgentConfig

        cfg = AgentConfig(BASE_DIR / "configs" / "config_outline_adversarial.json")
        return cfg.llm_call(system_prompt, user_prompt)

    def _react_llm_call(self, system_prompt: str, user_prompt: str) -> str:
        """ModelA ReAct 内循环专用 LLM 调用"""
        from ..agents.config import AgentConfig

        cfg = AgentConfig(BASE_DIR / "configs" / "config_outline_adversarial_react.json")
        return cfg.llm_call(system_prompt, user_prompt)

    def _build_react_system_prompt(self) -> str:
        prompt_builder = AdversarialOutlineAgentPrompt(self.user_need, self.tools)
        return prompt_builder.build_react_system_prompt()

    def _build_review_system_prompt(self) -> str:
        prompt_builder = AdversarialOutlineAgentPrompt(self.user_need, self.tools)
        return prompt_builder.build_review_system_prompt()

    def _review_outline(self) -> tuple:
        """ModelB 审查当前大纲 → (is_complete: bool, feedback: str)

        构造审查 prompt 时注入三类历史信息：
        - 【前面轮次审查结果】：ModelB 之前几轮发现的漏洞，避免重复提已修复项
        - 【前面轮次修复漏洞过程】：history_manager 中 ModelA 的 think 记录，告知 ModelB 最近做了什么修复
        """
        outline_text = self.outline_manager._format_outline(show_locator=True)
        summary = self.user_need.get("summary", "")
        collected = self.user_need.get("collected", {})
        # 构造前面轮次审查结果块，作为【前面轮次审查结果】注入 prompt 中
        history_block = ""
        if self.review_history:
            history_lines = []
            for i, h in enumerate(self.review_history, 1):
                history_lines.append(f"Round {i}漏洞：\n{h}")
            history_block = "\n".join(history_lines)
        # 构造前面轮次修复漏洞过程块，作为【前面轮次修复漏洞过程】注入 prompt 中
        assistant_entries = self.history_manager.get_history()
        fix_block = ""
        if assistant_entries:
            fix_lines = []
            idx= 1
            for i, m in enumerate(assistant_entries, 1):
                if m["role"] != "assistant":
                    continue
                content = m["content"]
                # 如果 assistant 响应是 JSON（含 think 字段），仅提取 think 作为语义化修复记录
                try:
                    parsed = json.loads(content)
                    think = parsed.get("think", "")
                    if think:
                        content = think
                except (json.JSONDecodeError, TypeError):
                    pass
                fix_lines.append(f"({idx}) {content}")
                idx += 1
            fix_block = "\n".join(fix_lines)
        # 开始构建 user prompt
        review_user_prompt = textwrap.dedent(f"""
            【用户需求】
            - 合同类型：{collected.get('contract_type', '')}
            - 场景：{collected.get('scene', '')}
            - 复杂度：{collected.get('complexity', '')}
            - 摘要：{summary}
            - 特殊需求：{'; '.join(self.user_need.get('extra_need', [])) or '无'}

            【当前合同大纲（含 locator）】
            {outline_text}
        """).strip()

        if history_block:
            review_user_prompt += f"\n\n【前面轮次审查结果】\n{history_block}"
        if fix_block:
            review_user_prompt += f"\n\n【前面轮次修复漏洞过程】\n{fix_block}"
        # 构建 system prompt
        review_system_prompt = self._build_review_system_prompt()
        # 调用 LLM 进行审查
        review = self._reviewer_llm_call(review_system_prompt, review_user_prompt)
        review = review.strip()

        is_complete = review == "完备"
        return (is_complete, review)

    def generate(self) -> dict:
        """主入口：ModelA 初始生成 → 对抗循环 → 返回最终大纲"""
        # 先初次根据 WebSearch 结果生成大纲
        print("[对抗Agent] Round 0: ModelA 初始生成大纲 + WebSearch...", file=sys.stderr)
        generator = OutlineGeneratorWithoutTemplate("ModelA_Generator", self.user_need)
        outline = generator.generate_outline()

        # 开始进行ModelA/B对抗
        self.outline_manager = OutlineManager(data=outline)
        # 注册工具（insert_clause / update_clause / update_header_footer），注入 outline_manager
        self.tool_manager = ToolManager(self.outline_manager, tools_json=self.tools)
        system_prompt = self._build_react_system_prompt()

        # === 外循环：ModelB 审查 → ModelA 修复 → 再审查 ===
        for loop in range(1, self.config.max_loop + 1):
            self._anti_round = loop
            print(f"[对抗Agent] Round {loop}: ModelB 审查中...", file=sys.stderr)
            self.history_manager.del_typeall_history("adversarial_review") # 这里删除审查记录，是为了避免重复提已修复项干扰ModelB的审查
            # 执行ModelB审查
            is_complete, review = self._review_outline()
            if not is_complete:
                self.review_history.append(review)

            if is_complete:
                print(f"[对抗Agent] Round {loop}: ModelB 判定「完备」，对抗结束", file=sys.stderr)
                break

            print(f"[对抗Agent] Round {loop}: 发现漏洞 -\n{review}", file=sys.stderr)
            self.history_manager.add_user_input("【当前合同大纲】"+self.outline_manager._format_outline(show_locator=True))
            self.history_manager.add_adversarial_review(review)
            # === 内循环：ModelA 连续调用工具修复，直到 completed=True ===
            for a_round in range(1, self.config.inner_max_loop + 1):
                response = self._react_llm_call(system_prompt, str(self.history_manager.get_history()))
                parsed = self.response_parser.parse_agent_response(response)
                self.history_manager.del_typeall_history("contract")  # 删除上一轮中observation中的合同信息
                if "error" in parsed:
                    self.history_manager.add_agent_response(response)
                    self.history_manager.add_user_input("输出格式错误，请返回合法JSON")
                    continue

                think = parsed.get("think", "")
                act = parsed.get("act")
                param = parsed.get("parameter", {})
                completed = parsed.get("completed", False)

                print(f"\n{'=' * 50}", file=sys.stderr)
                print(f"【对抗思考】{think}", file=sys.stderr)
                self.history_manager.add_agent_response(think)

                if act == "final_answer":
                    answer_text = param.get("answer_text", "")
                    print(f"【对抗完成】{answer_text}", file=sys.stderr)
                    self.history_manager.add_agent_response(answer_text)
                    break

                elif act == "use_tool":
                    tool_name = param.get("tool_name")
                    tool_params = param.get("tool_params", {})
                    print(
                        f"【调用工具】use_tool(tool_name=\"{tool_name}\", tool_params={json.dumps(tool_params, ensure_ascii=False)})",
                        file=sys.stderr
                    )
                    # 示例：use_tool(tool_name="insert_clause", tool_params={"locator": ["1"], "content": ["违约责任"], "position": "child"})
                    result = self.tool_manager.execute_tool(tool_name, tool_params)
                    print(f"【工具结果】{result}", file=sys.stderr)
                    self.history_manager.add_tool_observation(f"工具执行结果：{result}")
                    # 这里添加更新后的大纲放在observation中，让ModelA的ReAct机制确认是否成功修改，会动态清理记忆
                    self.history_manager.add_contract_info(
                        f"【更新后，当前合同大纲】：\n{self.outline_manager._format_outline(show_locator=True)}"
                    )
                    print(f"{'=' * 50}\n", file=sys.stderr)
                else:
                    print(f"【未知动作】{act}", file=sys.stderr)

                if completed:
                    break

            # 清理记忆，下一轮外循环从干净上下文开始
            self.history_manager.del_typeall_history("contract")
            self.history_manager.del_typeall_history("user")

        # 此时已经全部修复漏洞，保存大纲并退出即可。
        self.outline_manager.save_outline()
        initial_contract_text = self.outline_manager._format_outline()
        with open(AGENT_DIR / "data" / "initial_contract_text.txt", "w", encoding="utf-8") as f:
            f.write(initial_contract_text)

        return self.outline_manager.get_outline()


def generate_outline_adversarial(user_need: dict) -> dict:
    """零模板 + 对抗式大纲生成（入口函数）

    Args:
        user_need: 用户需求字典

    Returns:
        大纲 dict（与 OutlineGenerationAgent.generate_outline() 同格式）
    """
    agent = AdversarialOutlineAgent("AdversarialOutlineAgent", user_need)
    return agent.generate()
