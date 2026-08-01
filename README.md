# AI Contract Copilot — Multi-Agent AI Contract Copilot

<p align="center">
  <strong>企业级多智能体协作的 AI 合同生成系统 · 从需求到终稿全流程自动化</strong><br/>
  <sub>Multi-Agent · ReAct · SSE Streaming · Plan-Execute · Adversarial Generation · Agentic RAG</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg">
  <img src="https://img.shields.io/badge/FastAPI-0.100+-green.svg">
  <img src="https://img.shields.io/badge/DeepSeek-v4_Pro_|_v4_Flash-purple.svg">
  <img src="https://img.shields.io/badge/Agent-8_Multi_Agent-orange.svg">
  <img src="https://img.shields.io/badge/SSE-Real_Time_Streaming-ef4444.svg">
  <img src="https://img.shields.io/badge/License-MIT-lightgrey.svg">
</p>

***

## What is this?

一个基于 **8 个专业 AI Agent 协作** 的合同智能生成系统。用户只需要描述需求，系统自动完成：需求提取 → 模板匹配 → 大纲生成 → 起草 → 多维度审查 → 修订 → 终稿确认，输出一份结构完整、条款严谨、经严格审查后的合同。

**核心亮点：** 多Agent Workflow，深度结合ReAct/PlanExecutor多种框架，且Agent携带不同Skill与不同边界的Tool Use，有工业落地全面的harness约束与兜底降级策略保证稳定性。基于私有合同模板数据，并充分发挥LLM能力（如对抗，Loop Engineering等）智能生成合规合同。

**在线演示：👉 [点我体验](https://litchi-shrimp.github.io/contract_generation/test_frontend.html)**
（注意：该平台仅粗略演示Agent全流程功能，系统开发完备性也有待完善）

***

## Quick Preview

```
用户: "我要一份北京商业办公用房租赁合同，承租方是个人，需要租金递增条款和装修免租期..."

  ┌──────────────────────────────────────────────────────────────┐
  │  Step 1  用户需求提取         →  结构化需求摘要 (对话交互)     │
  │  Step 2  模板匹配             →  TopK 匹配 or 空结果          │
  │  Step 3  合同大纲生成         →  层级化大纲                   │
  │           ├─ 匹配到模板 → OutlineGenerationAgent             │
  │           └─ 零模板降级 → AdversarialOutlineAgent             │
  │             (WebSearch + 双模型对抗 + 后台静默建库)            │
  │  Step 4  大纲修改 (交互)      →  用户确认大纲                 │
  │  Step 5  合同起草 (Chunk并行) →  初始完整合同                 │
  │  Step 6  多维度审查 (4并行)   →  完整性/一致性/合法性/需求     │
  │  Step 7  Leader 修订          →  汇总审查结果执行修订          │
  │  Step 8  终稿确认 (交互)      →  用户确认最终合同             │
  └──────────────────────────────────────────────────────────────┘

  ✅ 输出: 一份结构完整、条款严谨的《北京市商业办公用房租赁合同》
```

***

## ✨ Highlights

| 亮点 | 业务价值 | 技术实现 |
|------|----------|----------|
| **Prompt / Context Engineering** | LLM 输出结构化、决策可解释、上下文精简高效，Agent 交互质量持续迭代优化 | • Schema Output 约束 JSON 格式；<br>• CoT + FewShot 引导推理链；<br>• 决策时携带 confidence + evidence 显式思考；<br>• Skill 渐进披露按需加载工具描述；<br>• Loop Engineering 迭代优化 Prompt（自制 Eval 指标驱动）； |
| **Multi-Agent 协作 × Workflow 编排** | Agent 既保持自主决策能力，又被 Workflow 流程化约束，分工明确、协作高效 | • 8 Agent：ReAct（交互式 Agent）+ Plan-Execute（LeaderAgent）+ Critic-Preview（审查-修订）混合编排；<br>• 每个 Agent 携带不同 Skill 与 Tool Use 边界；<br>• 对抗式 Agent（生成者/审查者双模型）充分发挥 LLM 能力； |
| **多类 Memory 与动态上下文管理** | 上下文始终控制在 4K tokens 以内，Agent 间记忆传递高效不丢失关键信息 | • 多类记忆分角色管理（原文 / Search / Agent / User / Observation / Tool）；<br>• 动态滚动策略自动清理过期记忆段；<br>• Agent 协作间通过文件 + 内存双通道传递结构化数据； |
| **结构化合同 × 定位增量修改** | Agent 按 locator 精准定位修改目标，Diff 增量而非全文重写，效率高且精确可控 | • 合同按大纲层级结构化存储；<br>• locator 定位体系（1 → 1.1 → 1.1.1）支持精确节点寻址；<br>• Agent 自主选择 update / insert / delete 在指定位置执行增量修改； |
| **Harness Engineering 工业级稳定** | 不同 Agent 工具权限精确隔离，长链路不崩溃，异常自动恢复不丢数据 | • 每个 Agent 独立 Skill × Tool 白名单，Prompt + 规则双重约束 Tool Use 边界（如 Drafter 禁止 delete、Leader 禁止 delete）；<br>• LLM 重试 + 模型降级 + 多级兜底（LLM → BM25）；<br>• 超时熔断 + Session 过期 + 失败节点快照回滚； |
| **Agentic RAG 嵌入式检索** | Agent 在多个环节自主检索优质参考上下文，提升生成与决策质量 | • 模板匹配：前缀 glob 召回 + LLM 语义精排，失败降级 BM25；<br>• 大纲生成 / 起草阶段：template_retrieve × web_search 按需调用；<br>• 对抗式大纲：WebSearch Top5 注入上下文；<br>• Embedding 召回方案预留； |

***

## Core Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        前端 (test_frontend.html)                 │
│        EventSource SSE · 3 列面板 · Timeline 流式渲染            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ SSE text/event-stream
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SSE Server (FastAPI + Uvicorn)                 │
│  ┌──────────┐  ┌──────────────────┐  ┌───────────────────────┐  │
│  │ REST API │  │ Session Manager  │  │ Orchestrator          │  │
│  │ SSE端点  │  │ 生命周期/超时管理 │  │ 8步工作流异步编排      │  │
│  └──────────┘  └──────────────────┘  └───────────┬───────────┘  │
└──────────────────────────────────────────────────┬──────────────┘
                                                   │ EventBus
                                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Agent 层 (8 Agents, Thread Pool)              │
│                                                                   │
│  ┌─────────────────────┐  ┌──────────────────────┐               │
│  │ UserFieldExtraction │  │ OutlineGeneration    │               │
│  │ (交互式需求提取)     │  │ (大纲生成/对抗式降级) │               │
│  └─────────────────────┘  └──────────────────────┘               │
│  ┌─────────────────────┐  ┌──────────────────────┐               │
│  │ OutlineModification │  │ DrafterAgent ×4      │               │
│  │ (交互式大纲修改)     │  │ (并行分块起草)        │               │
│  └─────────────────────┘  └──────────────────────┘               │
│  ┌──────────────────────────────────────────────┐                │
│  │ ReviewAgents ×4 (完整性/一致性/合法/需求)     │                │
│  └──────────────────────────────────────────────┘                │
│  ┌─────────────────────┐  ┌──────────────────────┐               │
│  │ LeaderAgent         │  │ ContractModification │               │
│  │ (Plan-Execute修订)  │  │ (交互式终稿确认)      │               │
│  └─────────────────────┘  └──────────────────────┘               │
└──────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
Agent_generation/
├── agent/
│   ├── agents/            # 智能体实现
│   │   ├── UserFieldExtractionAgent.py  # 用户需求提取智能体（交互式）
│   │   ├── TemplateMatcher.py          # 模板匹配器
│   │   ├── OutlineGenerationAgent.py   # 大纲生成智能体（有模板）
│   │   ├── OutlineGenerationWOTem.py   # 零模板对抗式大纲生成（WebSearch + 双模型）
│   │   ├── OutlineModificationAgent.py # 大纲修改智能体（交互式）
│   │   ├── ContractModificationAgent.py# 合同修改智能体（交互式）
│   │   ├── DrafterAgent.py            # 合同起草智能体（并行分块）
│   │   ├── ReviewAgents.py            # 合同审查智能体（4并行）
│   │   ├── LeaderAgent.py             # 领导智能体（汇总修订）
│   │   ├── config.py                  # 智能体配置 + 原始 LLM 调用
│   │   ├── ..._tools.json / ..._skills.json  # 各类Agent所能使用的工具/技能配置
│   ├── core/
│   │   ├── base_agent.py              # Agent 基类（有patch_llm_call_for_events）
│   ├── data/              # 输入数据存储
│   │   ├── initial_contract_text.txt  # 初始合同文本
│   │   ├── initial_outline.json       # 初始合同大纲
│   │   ├── match_result.json          # 模板匹配结果
│   │   ├── user_need_summary.json     # 用户需求摘要
│   ├── llm/               # 语言模型模块
│   │   ├── async_llm.py               # 异步流式 LLM 调用（stream + EventBus）
│   │   ├── llm.py                     # 同步 LLM 调用
│   │   ├── prompt_builder.py          # 提示词构建器（含各 Agent Schema 定义）
│   ├── memory/            # 记忆管理
│   ├── skills/            # 技能库
│   │   ├── outline-editor/            # 大纲编辑技能（增删改）
│   │   ├── review-* /                 # 合同审查技能（完整性/一致性/合法/需求）
│   │   ├── search-reference/          # 合同模板检索技能
│   │   ├── search-reference-web/      # Web 搜索技能
│   │   ├── version-control/           # 版本控制技能
│   ├── utils/             # 工具函数
│   │   ├── event_bus.py               # 异步事件总线（SSE 驱动的核心，有不同的事件类型）
│   │   ├── stream_parser.py           # 流式 JSON KeyPath 追踪器
│   │   ├── tool_manager.py            # 工具管理器
│   │   ├── outline_manager.py         # 大纲管理器
│   │   ├── response_parser.py         # 响应解析器
│   │   ├── history_manager.py         # 历史管理器
│   │   ├── deal_chunk.py              # 分块处理工具（Step 2 模板匹配）
│   │   ├── skill_manager.py           # 技能管理器
│   │   ├── logger.py                  # 日志工具
│   │   ├── Session.py                 # Session 超时管理
├── sse_server/            # SSE 实时服务（FastAPI）
│   ├── main.py                       # API 入口（SSE / start / user-input / status）
│   ├── orchestrator.py               # 8 步工作流编排（Agent 调度/取消/超时）
│   ├── session_manager.py            # Session 管理器（创建/销毁/清理）
├── configs/               # Agent 配置文件
│   ├── config_user_need_extraction.json
│   ├── config_outline_generation.json
│   ├── config_outline_adversarial.json        # 对抗评审 + 循环控制（仅未命中模板时使用）
│   ├── config_outline_adversarial_initial.json # ModelA 初始生成（仅未命中模板时使用）
│   ├── config_outline_adversarial_react.json   # ModelA ReAct 内循环（仅未命中模板时使用）
│   ├── config_outline_adversarial_background.json # WebSearch有价值的合同，后台默默抽取模板（仅未命中模板时使用）
│   ├── config_outline_modification.json
│   ├── config_initial_contract.json
│   ├── config_contract_modification.json
│   ├── config_review.json
│   ├── config_leader.json
├── outputs/               # 输出结果
│   ├── LeaderExecutionReport.json      # Leader执行报告
│   ├── LeaderPlan.json                 # Leader计划
│   ├── ReviewCompletenessAgent.json    # 完整性审查报告
│   ├── ReviewConsistencyAgent.json     # 一致性审查报告
│   ├── ReviewLegalAgent.json           # 合法性审查报告
│   ├── ReviewUsageAgent.json           # 用户需求审查报告
│   ├── initial_contract.json           # 初始完整合同
│   ├── modified_contract_text.txt      # 修改后的合同文本
│   ├── modified_contract_text_review_after.txt # 审查后的合同文本
│   ├── modified_contract_text_review_before.txt # 审查前的合同文本
├── main.py                # 主入口（python main.py server）
├── test_frontend.html     # SSE 测试前端（3 列可拖动面板）

```

***

## Running

### prepare

1. 安装依赖：`pip install -r requirements.txt`
2. 设置环境变量：`DeepSeekKey`（LLM API Key）
3. 模板库已预生成（`template_library/` 目录），无需重复运行 `make_template_llibrary.py`和`AddAbstractToTemplates.py`
原模板库共有200多个完整合同，共40余类，这里仅放置3个示例。

### Sync Mode (CLI)

```bash
cd Agent_generation
pip install -r requirements.txt
# Set DeepSeekKey environment variable
python main.py
```

### Async Mode (SSE Server + Web Frontend)

```bash
cd Agent_generation
python main.py server
# Uvicorn running on http://0.0.0.0:8000
```

Open `test_frontend.html` in browser, click "New Session".

***


## Skills System

渐进式 Skill 加载 — Agent 按需动态注册工具：

| Skill              | 工具                               | 用途    |
| ------------------ | -------------------------------- | ----- |
| `outline-editor`   | update/insert/delete\_clause     | 条款增删改 |
| `search-reference` | template\_retrieve + web\_search | 检索参考  |
| `review-*` ×4      | 各维度审查函数                          | 合同审查  |
| `version-control`  | snapshot/restore/diff            | 版本管理  |

***

***

## 分步详解（以下是项目说明书细节，可不细看）

### Step 1：用户需求提取

**功能**：通过多轮对话与用户交互，提取合同相关的核心字段，生成结构化需求摘要。

**使用 Agent**：UserFieldExtractionAgent

**输出示例**（`user_need_summary.json`）：

```json
{
  "collected": {
    "contract_type": "房屋租赁合同",
    "party_type": "企业-个人",
    "region": "北京市",
    "complexity": "标准",
    "scene": "商业办公用房租赁"
  },
  "extra_need": ["租金递增条款", "提前解约条件", "装修免租期"],
  "summary": "用户需要一份北京市商业办公用房的房屋租赁合同，承租方为个人...",
  "history": []
}
```

**技术要点**：
- 模糊匹配：用户输入不精确时自动匹配最接近的枚举值
- 二次反问：关键字段缺失时主动追问用户补充
- 动态加载合同类型枚举数据，引导用户选择
- 模型尽可能一次性收集用户所有字段。

**约束与边界**：
- contract_type 必须从合同类型列表中匹配，不可接受列表外的类型；其他 4 个字段可参考枚举但允许用户自定义（需确认）
- 收集完成前，response 只输出一个反问问题，无多余文字；收集完成后输出标准化总结并询问用户确认
- 答非所问时不跳转字段，重新提问并补充提示

---

### Step 2：模板匹配

**功能**：根据用户需求，从模板库中召回同类模板并用 LLM 语义排序，返回 TopK 最匹配的模板。

**使用模块**：TemplateMatcher

**输出示例**（`match_result.json`）：

```json
[
  {
    "metadata": { "template_name": "房屋租赁合同（标准版）", "party_type": "企业-个人", "scene": "商业租赁" },
    "chapter_information": ["1:租赁标的", "2:租赁期限", "3:租金及支付方式", ...],
    "abstract": "本合同适用于企业将商业办公用房出租给个人使用..."
  },
  {
    "metadata": { "template_name": "房屋租赁合同（简版）", "party_type": "企业-个人", "scene": "住宅租赁" },
    "chapter_information": ["1:房屋基本情况", "2:租金", "3:押金", ...],
    "abstract": "本合同适用于企业将住宅用房出租给个人..."
  }
]
```

**匹配策略（V2）**：
- **Stage 1 — 前缀召回**：取 template_id 中 `_` 前部分（如 `Engineering_EPC` → `Engineering`），glob 召回所有同类模板
- **Stage 2 — LLM 语义精排**：将候选模板的摘要 + 元数据组装结构化提示词，LLM 输出排序索引
- **降级策略**：LLM 解析失败（JSON 格式错误等）时自动降级 BM25（jieba 分词 + BM25Okapi），以用户原始对话历史为查询、模板全文为语料计算相似度

**兜底与边界**：
- 若用户需求中缺少合同类型字段，或映射不到任何 template_id，直接返回空列表（上游 Step 3 将降级兜底处理）
- 若前缀召回结果为空，直接返回空列表
- 若候选模板 ≤ 1 个，跳过 LLM 精排，直接返回
- Stage 2 的 LLM 调用通过 `re.search` + `json.loads` 提取 JSON，异常时降级 BM25，保证始终有可用输出

**V1 → V2 改进**：
| 维度 | V1（规则+BM25） | V2（前缀+LLM） |
|------|----------------|----------------|
| 召回方式 | 元数据规则打分，依赖字段规则匹配（无语义） | 前缀 glob，依赖 ID 命名规范 |
| 排序方式 | BM25 词频共现，无法理解语义 | LLM 语义理解，能关联近义概念 |
| 维护成本 | 规则分值需人工调优 | 新增维度只需改提示词 |
| 容错性 | 规则/BM25 一方失败即无结果 | LLM 失败自动降级 BM25 |

---

### Step 3：合同大纲生成

**功能**：基于匹配的模板结构和用户需求，生成标准化的层级合同大纲（最多 3 级）。如果 Step 2 模板匹配结果为空，自动降级进入**零模板对抗式大纲生成**模式（见 Step 3b）。

**使用 Agent**：OutlineGenerationAgent（有模板）/ AdversarialOutlineAgent（零模板）

**输出示例**（`initial_outline.json`）：

```json
{
    "标准化模板文本": {
        "合同首部": "数字内容授权协议\n\n甲方（授权方）：{甲方公司名称}...",
        "正文章节": [
          {"章节编号": "第一条","章节标题": "定义与解释","条款列表": 
          [{"条款编号": "1.1","条款大致说明": "定义：对本协议中出现的...","子条款列表": [],"locator": "1.1"},
          {"条款编号": "1.2","条款大致说明": "xxx","子条款列表": [],"locator": "1.2"}],
          "locator": "1"},
          {"章节编号": "第二条","章节标题": "授权内容与范围","条款列表": 
          [{"条款编号": "2.1","条款大致说明": "xxx","子条款列表": [],"locator": "2.1"},]
          "locator": "2"},
        ],
        "合同尾部": "（以下无正文）\n\n甲方（盖章）..."
    },
    "思考": "用户需求为复杂版游戏推广数字内容授权协议，核心诉求包括..."
}
```

**技术要点**：
- 基于模板章节结构 + 用户特殊需求生成初始大纲
- locator 定位体系（1, 1.1, 1.2, 2, 2.1...），支持精确节点定位
- 脱敏规则自动应用（替换敏感信息为占位符）

**约束与边界**：
- **模板优先级**：模板仅作可选骨架。若模板元数据与用户需求冲突，**以用户真实需求为准**，模板必须让步；模板质量差/冗余/过时时可丢弃不采用
- **并列 vs 从属区分**：并列子项（7.1、7.2）各自承载内容，不得设为父子关系；仅当需拆分细则时才使用从属关系（如 7.1 为总述，7.1.1 为细则）

---

### Step 3b：零模板对抗式大纲生成（降级路径）

**功能**：当 Step 2 模板匹配结果为空时，无法基于模板生成大纲，系统自动降级进入对抗式大纲生成模式。该模式下通过 WebSearch 获取参考 + 双模型对抗循环，从零生成合同大纲。

**使用 Agent**：AdversarialOutlineAgent（`OutlineGenerationWOTem.py`）

**完整流程**：

```
Step 2 匹配结果为空
    │
    ▼
┌─ Stage 0：WebSearch ──────────────────────────────────────┐
│  以"用户需求.contract_type 合同"为 Query 发起网络搜索     │
│  Top5 结果格式化为参考上下文                                │
│  后台 daemon 线程异步抽取结构化模板存入模板库（不阻塞）      │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Stage 1：Round 0 — ModelA 初始生成 ─────────────────────┐
│  模型：deepseek-v3.2（temperature=0.3）                    │
│  配置：config_outline_adversarial_initial.json              │
│  输入：用户需求 + WebSearch 上下文                          │
│  输出：初始大纲（与 Step 3 同格式）                         │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Stage 2：对抗循环（ModelA ReAct + ModelB 审查） ─────────┐
│                                                             │
│  for loop in range(1, max_loop):                            │
│      │                                                      │
│      ▼                                                      │
│  ┌─ ModelB 审查 ──────────────────────────────────┐        │
│  │  模型：deepseek-v4-flash（temperature=0.8）      │        │
│  │  配置：config_outline_adversarial.json           │        │
│  │  输入：当前大纲 + 审查历史 + 上一轮修复记录       │        │
│  │  输出：漏洞列表 or "完备"                         │        │
│  │  角色："红队"，只检查"面和点是否到位"，不评判内容  │        │
│  └─────────────────────────────────────────────────┘        │
│      │                                                      │
│      ▼                                                      │
│  若"完备" → 结束循环，返回最终大纲                           │
│      │                                                      │
│      ▼                                                      │
│  ┌─ ModelA ReAct 内循环 ───────────────────────────┐        │
│  │  模型：deepseek-v3.2（temperature=0.3）           │        │
│  │  配置：config_outline_adversarial_react.json      │        │
│  │  架构：Think → Act → Observation（内循环）        │        │
│  │                                                     │    │
│  │  for a_round in range(1, inner_max_loop):            │    │
│  │      Think: 分析漏洞 → 确定修复位置+工具           │    │
│  │      Act: use_tool (insert_clause/update_clause/...) │    │
│  │      Observation: 工具结果 → 反馈到下一轮           │    │
│  │      completed=True → 退出内循环                    │    │
│  │                                                     │    │
│  │  记忆：history_manager 分角色管理                    │    │
│  │  (user/assistant/tool/contract)，每轮清理 contract  │    │
│  └─────────────────────────────────────────────────┘        │
│                                                             │
│  每轮外循环闭包时：                                         │
│    review_history.append(ModelB 本轮漏洞)                    │
│    ModelB 下一轮可见【上一轮审查结果】+【上一轮已修复】      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Stage 3：返回大纲 ─────────────────────────────────────────┐
│  格式与 Step 3 OutlineGenerationAgent 输出完全一致            │
│  后续 Step 4-8 无差别处理，无需感知来源                       │
└─────────────────────────────────────────────────────────────┘
```

**技术要点**：

| 维度 | 说明 |
|------|------|
| 双模型设计 | ModelA（v3.2, T=0.3）负责生成+修复；ModelB（v4-flash, T=0.8）负责审查，分工明确 |
| 后台并行 | WebSearch 发起后立即启动 daemon 线程异步抽取结构化模板入库，与后续 LLM 调用并行执行 |
| 双层循环 | 外循环（ModelB 审查轮次）+ 内循环（ModelA 工具修复轮次），修复充分后再提交审查 |
| 审查浓缩 | ModelB 的 REVIEWER_SYSTEM_PROMPT 明确约束仅审查"面和点是否到位"，不评判措辞质量/合法性 |
| 修复轨迹 | 每轮 ModelA 的修复记录通过 history_manager add_tool_observation 持久化，供下一轮 ModelB 审查时参考（【上一轮已修复】块） |
| 审查历史 | review_history 随轮次递增，每轮 ModelB 可见前几轮的审查结果（【前面轮次审查结果】），避免重复提已修复的问题 |
| 3 层配置分离 | initial（生成）/ react（内循环）/ reviewer（审查）各独立 json 文件，model_id/temperature 可独立调优 |
| 无模板依赖 | 整个流程不依赖 template_retrieve，全链路使用 WebSearch + LLM 自身知识 |
| 增量积累 | 后台建库线程将 WebSearch 结果转化为结构化模板存入模板库，后续同类需求可直接匹配 |

### Step 4：大纲修改（交互式）

**功能**：与用户交互修改合同大纲，直到用户满意确认。

**使用 Agent**：OutlineModificationAgent

**交互示例**：

```
🤖：合同大纲已为您生成完毕，如果你有任何问题或者疑惑都可以与我交流~
👤用户：在租赁标的下面增加一个房屋交付状态的条款
🤖：【思考】用户要求在1.租赁标的下新增子条款，需加载outline-editor Skill
    【行动】load_skill(skill_name="outline-editor")
    【观察】Skill加载成功，获得工具列表
    【行动】use_tool(tool_name="insert_clause", params={"locator": ["1"], "content": ["房屋交付状态"], "position": "child"})
    【观察】插入成功，当前大纲已更新
    【回答】已为您在"租赁标的"下新增了"房屋交付状态"条款
👤用户：没问题了，确认
```

**技术要点**：
- **ReAct 循环架构**：Think → Act → Observation，每轮只执行一个阶段
- **Skill 渐进式披露**：Agent 初始只有基础能力（回答/追问），调用 `load_skill` 后才注册对应工具。已加载的 Skill 幂等跳过，防止重复加载
- **搜索参考优先**：无明确参考时，优先搜索模板库（`search-reference` Skill）→ Web Search 兜底
- **记忆分角色管理**：user / assistant / tool / skill 分类存储，每次 tool/skill 调用后自动清理对应记忆段，避免上下文膨胀
- **超时熔断机制**：单次 `run()` 默认 200s 超时。超时后自动恢复快照，提示用户重新输入，不崩溃退出
- **Session 管理**：session 默认 1800s（30 分钟）过期，超时自动退出交互循环
- **用户意图识别**：外侧轻量化 LLM 实时识别用户是否满意，匹配"退出/确认/没问题"等关键词直接退出；Intent 识别失败时提示用户重新回答

**工具调用边界**：
- **update_clause**：修改指定 locator 的 content。支持批量，参数为 `locator: [str]` + `content: [str]`
- **insert_clause**：在指定 locator 下插入新节点（可批量）。支持 `position: "child"`（子节点）/ `"after"`（同层级后）/ `"before"`（同层级前）
- **delete_clause**：删除指定 locator 的节点。支持批量删除
- **批量操作排序规则**：当 `position` 为 `after`/`before` 时，按 locator 降序排列执行，防止前序插入导致后续 locator 偏移；`child` 操作不受影响，可并行执行

---

### Step 5：合同起草

**功能**：将大纲按章节分 Chunk 并行处理，由多个 DrafterAgent 补全条款细节，合并为初始完整合同。如果前面无匹配模板，见Step 5b。

**使用 Agent**：DrafterAgent（并行）

**输出**（`initial_contract.json`）：

**技术要点**：
- 按大章节平均分 Chunk，多线程并行起草
- 每个 DrafterAgent 独立 ReAct 循环，调用 search 工具检索模板细节
- 仅允许 update / insert 操作，且 insert 只能在父节点下添加子条款，**不得破坏大纲结构**

**工具调用边界**：
- **DrafterAgent 只加载两个工具和一个Search Skill**：`update_clause`（修改条款内容）和 `insert_clause`（在父节点下添加子条款）,`search-reference`（检索参考，工具包括`template_retrieve`和`web_search`）
- **禁止使用 delete_clause**：不得删除大纲中的任何节点

### Step 5b：零模板合同起草
**工具调用边界**：
- **DrafterAgent 只加载两个工具和一个Search Skill的部分**：`update_clause`（修改条款内容）和 `insert_clause`（在父节点下添加子条款）,`search-reference`（检索参考，工具只有`web_search`），并且无模板情况下，前面对抗生成的大纲已经足够详细了，这里大部分仅需补全即可，所以优化提示词，让Agent只有在【特定触发规则】才会使用websearch。具体规则可以看./Agent_generation/agent/skills/search-reference-web/SKILL.md
---

### Step 6：多维度合同审查

**功能**：4 个审查 Agent 并行审查合同的完整性、一致性、合法性、用户需求符合性。

**使用 Agent**：ReviewCompletenessAgent / ReviewConsistencyAgent / ReviewLegalAgent / ReviewUsageAgent

**输出示例**（`ReviewCompletenessAgent.json`）：

```json
{
    "review_agent_name": "ReviewCompletenessAgent",
    "problems": [
        {
            "problem_id": "risk_01",
            "problem_type": "核心条款缺失",
            "problem_level": "高",
            "problem_description": "合同正文明确提及附件一《授权内容素材清单》和附件二《结算与考核细则》作为核心履约依据，但当前合同文本中仅列出了附件名称，未提供其具体内容...",
            "impact_scope": "授权范围与结算费用章节",
            "suggestions": [
                "建议将附件一《授权内容素材清单》和附件二《结算与考核细则》...",
                "或可在协议正文中增加条款，约定双方应于..."
            ]
        },
    ]
}
```

**技术要点**：
- 4 个 Agent 并行运行，每个调用对应的 Skill
- 结构化输出：每种问题含 problem_id、类型、等级、描述、影响范围、修改建议
- 审查结果供下一步 LeaderAgent 汇总决策

**兜底与重试机制**：
- 每个审查 Agent 在主流程中最多重试 3 次（捕获异常后自动重试）
- 如果某个 Agent 文件未生成，LeaderAgent 通过 `_safe_load_json()` 兜底处理，返回空 problems 列表 + 错误标记，不影响整体流程
- ReviewUsageAgent 额外接收 `user_need` 参数（内存模式），如未传入则从文件读取兜底

---

### Step 7：Leader 修订

**功能**：汇总 4 个审查报告，生成修订计划（Plan）并按计划执行（Execute），自动修订合同。

**使用 Agent**：LeaderAgent

**输出示例**（`LeaderPlan.json` → 执行后生成修订合同）：

```
== 审查结果聚合 ==
[高优先级] 完整性: 缺少违约责任章节 → 新增
[中优先级] 一致性: 付款节点与验收条款矛盾 → 调整措辞
[低优先级] 表述优化: 部分条款措辞不够严谨 → 润色

== 执行结果 ==
✅ 成功: 新增违约责任章节（含违约金条款）
✅ 成功: 调整第5.2条付款节点措辞
⚠️ 已弃用: 冗余修改建议已过滤（完整性已覆盖）
```

**技术要点**：
- **Plan-Execute 模式**：先分析审查结果生成 Task List（Plan），再依次执行（Execute）
- **优先级管理**：P0（法律/安全）> P1（一致性）> P2（表述优化）
- **智能去冗余**：多个审查 Agent 的重复/冲突建议自动过滤

**兜底与边界**：
- 审查结果总量为 0 时，直接返回"未检测到可处理的问题，合同保持不变"，不执行任何修改
- Plan 生成失败（LLM 返回 error）时，保存失败报告，不执行修改
- 每个 decision 必须包含 `tool_name` 和 `tool_params`，缺失任一则该 decision 标记为 failed 并跳过
- 执行失败的 action 单独记录到 `failed_actions` 列表，不阻塞后续 action 执行
- 仅允许使用 `leader_agent_tools.json` 中注册的工具（update_clause / insert_clause），**禁止 delete 操作**

---

### Step 8：终稿确认（含自我审查）

**功能**：与用户交互确认最终合同，Agent 在修改时自动评估风险，重大风险先预警再执行。

**使用 Agent**：ContractModificationAgent

**交互示例**：

```
🤖：整体合同已为您生成完毕，如果你有任何问题或者修改需求都可以与我交流~
👤用户：把付款方式从"验收合格后30日"改为"合同签订后7日"
🤖：【思考】用户要求提前付款节点。检查相关条款...发现第7.1条验收条款与此关联，存在中风险
    【行动】ask_user "提醒：若付款提前至合同签订后7日，第7.1条'验收不合格买方有权拒绝支付'可能出现条款冲突..."
👤用户：我知道风险，按我说的改
🤖：【思考】用户已知风险并坚持修改，执行修改
    【行动】use_tool(tool_name="update_clause", params={"locator": ["5.2"], "content": ["合同签订后7日内支付..."})
    【回答】已修改，请知悉第7.1条仍以验收为前提，建议后续统一调整
👤用户：没问题了
```

**技术要点**：
- 沿用 ReAct 交互架构，提示词中耦合自我审查（Self-Review）能力
- 修改前风险评估：无风险/低风险直接执行，重大风险先预警再等待确认
- 无法判断风险时，可调用搜索 Skill 获取参考
- 轻量化 LLM 识别用户确认意图，确认后退出

**自我审查风险分级**：

| 风险等级 | 触发条件 | 行为 |
|----------|----------|------|
| 无/低 | 错别字修正、措辞润色、明显笔误 | 直接执行修改，无需用户确认 |
| 重大 | 导致条款冲突、违反法律强制性规定、破坏完整性、偏离原始需求 | 先清晰说明风险点与影响范围，等待用户明确确认后再执行 |

**约束与边界**：
- **安全第一**：绝不因用户简单要求就忽略重大风险，任何可能导致合同效力瑕疵的操作必须先预警
- **效率优先**：低风险修改跳过询问直接执行，保持交互流畅
- 超时熔断、Skill 渐进式披露、Session 管理等机制与 OutlineModificationAgent 一致（见 Step 4）
- 用户确认终稿后，自动保存合同文本至 `outputs/final_contract_text.txt`
- 退出时返回 `(user_confirmed, contract_data)` 元组，供主流程判断后续操作

---

## 核心模块

### LLM 模块

| 组件 | 功能 |
|------|------|
| `llm.py` | 大语言模型调用（统一接口，支持多 Provider） |
| `prompt_builder.py` | 各 Agent 提示词构建（含 Schema 定义） |

### 工具管理模块

| 组件 | 功能 |
|------|------|
| `tool_manager.py` | 工具注册与执行（支持从目录扫描或 JSON 文件加载） |
| `outline_manager.py` | 合同结构管理（locator 定位、快照、原子保存） |
| `response_parser.py` | LLM 响应解析与格式校验 |
| `history_manager.py` | 多角色记忆管理（user/assistant/tool/skill） |

### 技能系统（Skill）

Skill 是一组相关工具的集合，Agent 按需渐进式加载：

| 技能名称 | 包含工具 | 用途 |
|----------|----------|------|
| `outline-editor` | update_clause / insert_clause / delete_clause | 增删改合同条款 |
| `search-reference` | retrieve_template_reference / web_search | 检索参考模板和互联网资料 |
| `review-completeness-risk` | review_completeness | 完整性审查 |
| `review-consistency` | review_consistency | 一致性审查 |
| `review-legal-compliance` | review_legal | 合法性审查 |
| `review-user-requirement` | review_user_requirement | 用户需求匹配审查 |
| `version-control` | snapshot / restore / diff | 合同版本快照与回滚 |

### 配置系统

各 Agent 配置文件位于 `configs/` 目录：

- `config_userNeedExtraction.json` — 用户需求提取
- `config_outline_generation.json` — 大纲生成（有模板）
- `config_outline_adversarial.json` — 对抗式大纲 reviewer + 循环控制
- `config_outline_adversarial_initial.json` — 对抗式大纲 ModelA 初始生成
- `config_outline_adversarial_react.json` — 对抗式大纲 ModelA ReAct 内循环
- `config_outline_adversarial_background.json` — 对抗式大纲后台建库
- `config_outline_modification.json` — 大纲修改
- `config_review.json` — 审查 Agent
- `config_leader.json` — LeaderAgent
- `config_ini_contract.json` — 合同初始化

---

## 技术特点

| 特点 | 说明 |
|------|------|
| 多智能体协作 | 8 个 Agent 专业分工，顺序 + 并行混合编排 |
| ReAct 循环 | 所有交互式 Agent 采用 Think-Act-Observation 架构 |
| Plan-Execute 模式 | LeaderAgent 先规划再执行，确保修订有序 |
| 渐进式技能加载 | Agent 按需加载 Skill，避免工具列表过长 |
| Chunk 并行 | 合同起草阶段多线程并行处理，大幅加速 |
| 自我审查 | 终稿阶段 Agent 在修改前自动评估风险 |

---

## 输出文件一览(仅用于输出测试或Agent间JSON传第失败兜底)

| 文件 | 所属步骤 | 说明 |
|------|----------|------|
| `agent/data/user_need_summary.json` | Step 1 | 用户需求摘要 |
| `agent/data/match_result.json` | Step 2 | TopK 匹配模板 |
| `agent/data/initial_outline.json` | Step 3 | 初始合同大纲 |
| `agent/data/initial_contract_text.txt` | Step 5 | 初始合同文本 |
| `outputs/Review*.json` | Step 6 | 4 份审查报告 |
| `outputs/LeaderPlan.json` | Step 7 | 修订计划 |
| `outputs/LeaderExecutionReport.json` | Step 7 | 修订执行报告 |
| `outputs/modified_contract_text_review_before.txt` | Step 7 | 修订前合同 |
| `outputs/modified_contract_text_review_after.txt` | Step 7/8 | 终版合同 |

---

## 后续改进方向

### PreToolUse 风险拦截（借鉴 Claude Code Plan Mode）

当前 Step 8 的自我审查仅耦合在提示词中，由 Agent 自行判断风险，缺少系统级的强制执行。后续计划引入 **PreToolUse 拦截机制**，在每次调用修改类工具前插入强制审查环节：

**流程设计**：

```
用户请求 → 轻量 LLM 风险扫描 → 用户确认 → 分流执行
```

| 步骤 | 说明 |
|------|------|
| **Step 1：风险扫描** | 在模型调用 `update_clause` / `insert_clause` / `delete_clause` 前，由一个轻量 LLM 按内置条件判断当前请求是否为复杂/高风险：涉及多步骤不明确、跨文件操作、敏感操作（删除/重构）、上下文信息不足等 |
| **Step 2：用户确认** | 扫描结果以简要文本展示给用户（如"你的修改可能会牵扯到 XX 条款，建议先规划"），用户选择确认或放弃 |
| **Step 3a：复杂请求 → Plan Mode** | 若判定为高风险且用户确认，调用 `EnterPlanMode` 工具进入规划模式，先制定详细计划再分步执行 |
| **Step 3b：简单请求 → 直接执行** | 若判定为低风险，直接由 LLM 执行工具调用，无需额外规划 |

**核心价值**：
- **安全性**：避免模型在信息不全或影响范围大的情况下盲目修改合同
- **可控性**：用户在执行前了解风险并决定是否继续
- **效率**：简单请求跳过规划，保持快速响应；复杂请求强制规划，减少错误
- **轻量化**：只用一个轻量 LLM 做判断，不引入重型链路

---

## 扩展与定制

- **新增 Agent**：在 `agent/agents/` 下创建新类，继承 `BaseAgent`
- **新增 Skill**：在 `agent/skills/` 下创建目录，包含 `SKILL.md` + `tools.json` + `scripts/`
- **新增工具**：在 `agent/skills/<skill>/scripts/` 下添加脚本，自动注册
- **修改配置**：编辑 `configs/` 下对应 JSON 文件
- **新增模板**：在 `template_library/` 下添加 JSON，运行 `AddAbstractToTemplates.py` 补充摘要

## 注意事项

1. 需要设置 `DeepSeekKey` 环境变量