<div align="center">

# AI Contract Copilot

**多智能体协作的中文合同生成系统：从一句需求到可下载的 Word 终稿**

<p>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/FastAPI-0.122-009688?logo=fastapi&logoColor=white">
  <img src="https://img.shields.io/badge/Vue-3.5-4FC08D?logo=vuedotjs&logoColor=white">
  <img src="https://img.shields.io/badge/MySQL-8-4479A1?logo=mysql&logoColor=white">
  <img src="https://img.shields.io/badge/LLM-DeepSeek-5B5BD6">
  <img src="https://img.shields.io/badge/License-MIT-lightgrey">
</p>

**[在线体验](https://litchi-shrimp.github.io/AI-Contract-Copilot/)** · [评测报告](https://litchi-shrimp.github.io/AI-Contract-Copilot/report.html) · [快速开始](#快速开始) · [工作流程](#工作流程) · [工程设计](docs/ENGINEERING.md)

<img src="figs/2_generation_process_2.png" width="900" alt="生成过程：左侧对话与 Agent 时间线，右侧合同实时增量修改">

</div>

## 简介

AI Contract Copilot 把合同起草拆成 8 个步骤，由多个分工明确的 Agent 完成：需求提取、模板匹配、大纲生成、并行起草、多维审查、集中修订和终稿确认。用户在对话中描述需求，右侧文档面板实时显示合同的每一次增删改，完成后可导出 Word。

模板库命中时，系统基于私有合同模板生成大纲；未命中时，系统联网检索参考资料，再由生成方与审查方两个模型对抗迭代出大纲。

## 在线体验

- 前端：<https://litchi-shrimp.github.io/AI-Contract-Copilot/>（注册即可使用，每个账号含 2 次免费完整生成；也可在个人主页配置自己的模型 API）
- 后端 API：`https://ecosilk.cn/api`，健康检查 <https://ecosilk.cn/api/health>

一次完整生成通常需要 7 到 15 分钟。

## 特性

- **多 Agent 工作流**：ReAct、Plan-Execute 与“审查-修订”三种模式组合编排。每个 Agent 只拿到自己的 Skill 和工具白名单。
- **结构化增量编辑**：合同按层级存储，每个条款有 `locator`（如 `3.2.1`）。Agent 通过 `update` / `insert` / `delete` 工具定点修改，不整篇重写。
- **节点级容错**：所有 LLM 调用经过统一网关（重试、降级、熔断、超时、预算、JSON 自修复）；所有工具调用经过 Harness（参数校验、失败回滚、审计）。每个步骤都有兜底策略。
- **停止与继续**：每步写检查点。停止会立即取消在途请求，继续时从最近的完整检查点恢复，服务重启后会话自动转为暂停。
- **Web 平台**：登录注册、历史会话、SSE 实时流（支持断线重连）、文档 diff 高亮、目录视图、Word 导出、免费额度与用户自有模型 API（OpenAI / Anthropic / Gemini 等协议）。
- **三层评测**：端到端、大纲阶段、工具调用分别评测。主观质量由 LLM-as-Judge 按分析型 Rubric 打分，要求给出原文证据并做多次投票。

## 截图

| 大纲交互修改 | 中途停止与恢复 |
|:---:|:---:|
| <img src="figs/1_generation_process_1.png" alt="大纲修改"> | <img src="figs/4_Interrupt.png" alt="停止与恢复"> |
| **终稿完成** | **评测报告** |
| <img src="figs/3_generation_finish.png" alt="终稿完成"> | <img src="figs/EVAL1.png" alt="评测报告"> |

## 工作流程

```mermaid
flowchart LR
    A[1 需求提取<br/>多轮对话] --> B[2 模板匹配<br/>召回 + LLM 精排]
    B -->|命中| C[3 大纲生成]
    B -->|未命中| C2[3b 对抗式大纲<br/>联网检索 + 双模型]
    C --> D[4 大纲修改<br/>用户确认]
    C2 --> D
    D --> E[5 并行起草<br/>按章节分块]
    E --> F[6 并行审查<br/>完整性 / 一致性 / 合法性 / 需求]
    F --> G[7 Leader 修订<br/>先计划后执行]
    G --> H[8 终稿确认<br/>导出 Word]
```

| 步骤 | 主策略 | 失败时 |
|---|---|---|
| 模板匹配 | 前缀召回 + LLM 精排 | BM25 → 零模板路径 |
| 大纲生成 | 基于模板生成 | 对抗式生成 → 通用合同骨架 |
| 并行起草 | 按章节分块并行 | 单块重试 → 保留大纲原文 |
| 并行审查 | 4 个维度并行 | 单维重试 → 跳过该维度 |
| 修订 | Leader 计划 + 执行 | 计划失败时合同保持不变 |
| 联网检索 | 博查 API | 360 → 搜狗 → Bing → 模型自身知识 |

所有降级都会推送到前端并记入检查点。

## 架构

```mermaid
flowchart TB
    FE[frontend/<br/>Vue 3 + Vite] -- REST / SSE --> SV[server/<br/>FastAPI · 认证 · 会话 · RunManager]
    SV --> PL[agent/pipeline/<br/>8 步可重入状态机 · 检查点]
    PL --> AG[agent/agents/<br/>各步骤 Agent]
    AG --> GW[agent/runtime/llm_gateway<br/>唯一 LLM 出口]
    AG --> TH[agent/runtime/tool_harness<br/>唯一工具出口]
    AG --> RP[agent/repository/<br/>模板库 MySQL / JSON]
    SV --> DB[(MySQL)]
    RP --> DB
```

每个会话在独立的 `RunContext` 中运行，持有自己的取消令牌、工作区、事件通道和 Token 预算，并行 Agent 与多个会话之间互不串扰。

## 评测

评测集包含 6 个合同用例（住房租赁、技术服务、数字内容授权、设备采购、保密竞业、基金托管，其中基金托管未命中模板）。基线为同模型单次生成：同一需求、同一输出 Schema，不使用模板、检索和审查。Judge 为 `deepseek-flash`，温度 0，每项采样 3 次，Rubric 版本 `e2e-v1.3` / `outline-v1.1`。

| 指标 | 本系统 | 单 LLM 基线 |
|---|---:|---:|
| 要素准确率（用户给定的金额、期限、比例等） | **100.0%** | 85.1% |
| 必备条款召回率 | **100.0%** | 87.9% |
| 端到端 6 维 Rubric | **98.0%** | 81.5% |
| 大纲 3 维 Rubric | **94.2%** | 78.7% |
| 成对盲评（双向交换位置） | 5 胜 1 平 0 负 | |

| 阶段 | 结果 |
|---|---|
| 对抗式大纲 | Rubric 从初稿 76.7% 提升到收敛后 99.3%，平均 2.0 轮收敛 |
| 联网检索 | 噪声率 8%，相关结果利用率 100% |
| 工具调用 | 非法操作率 0%，批量利用率 85.3%，绕路率 3.2%，编辑落地率 84.7% |
| Judge 可靠性 | Fleiss κ 0.88，3 次采样全一致 96.8%，证据核验失败 0 |

完整报告（含分维度得分、逐要素定位、盲评理由、迭代对比与审计明细）可[在线查看](https://litchi-shrimp.github.io/AI-Contract-Copilot/report.html)，源文件为 [`Agent_generation/evals/report.html`](Agent_generation/evals/report.html)。

> 被评系统与 Judge 使用同一模型，存在自我偏好风险。证据核验与双向成对比较可以缓解但无法完全消除，详见 [`docs/ENGINEERING.md`](docs/ENGINEERING.md)。

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 22+（构建前端）
- MySQL 8（可选：未配置时模板库回退到本地 JSON，Web 平台需要 MySQL）
- DeepSeek API Key

### 安装

```bash
git clone https://github.com/litchi-shrimp/AI-Contract-Copilot.git
cd AI-Contract-Copilot
pip install -r requirements.txt
```

配置环境变量（完整示例见 [`deploy/backend.env.example`](deploy/backend.env.example)）：

```bash
export DeepSeekKey=sk-xxxx
export DATABASE_URL="mysql+pymysql://user:password@127.0.0.1:3306/contract_copilot?charset=utf8mb4"
```

导入模板库（幂等，可重复执行）。仓库中的 `template_library/` 只含少量示例模板：

```bash
cd Agent_generation
python -m db.import_templates --dir ../template_library
```

### 启动 Web 平台

```bash
# 后端：http://localhost:8000，首次启动自动建表
cd Agent_generation
python main.py server

# 前端：http://localhost:5173，/api 代理到 8000
cd frontend
npm ci
npm run dev
```

### 命令行

```bash
cd Agent_generation
python main.py                        # 交互式
python main.py --need "完整需求描述"    # 无人值守，自动确认
python main.py --resume <run_id>      # 从检查点继续
```

## 配置

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DeepSeekKey` | 平台 LLM Key，也兼容 `DEEPSEEK_API_KEY` | 无 |
| `DATABASE_URL` | MySQL 连接串 | 本地 `contract_copilot` |
| `TEMPLATE_BACKEND` | 模板库来源：`auto` / `mysql` / `file` | `auto` |
| `BOCHA_API_KEY` | 博查搜索，未配置时自动降级 | 无 |
| `JWT_SECRET` | 登录令牌签名密钥，生产环境必须设置 | 自动生成 |
| `SECRET_KEY` | 用户 API Key 的加密密钥，丢失后已保存的 Key 无法解密 | 自动生成 |
| `FREE_RUNS` / `FREE_TOKENS` | 每用户免费完整生成次数 / Token 上限 | `2` / `1800000` |
| `LLM_ALLOWED_MODELS` | 允许调用的模型白名单 | `deepseek-flash` |
| `LLM_MAX_CONCURRENCY` | 同一时刻在途的 LLM 请求上限，决定整体吞吐 | `8` |
| `MAX_ACTIVE_RUNS_PER_USER` / `MAX_ACTIVE_RUNS_TOTAL` | 正在执行的生成任务上限（等待用户回复的会话不计入） | `3` / `64` |

## 测试与评测

```bash
cd Agent_generation
python -m pytest tests -q                 # 后端测试（需要 MySQL 测试库，自动建表）

python -m server.devserver --port 8001    # Fake LLM 后端，不消耗额度
python ../frontend/e2e/e2e_test.py        # Playwright 端到端测试（需先构建并预览前端）

python -m evals.generate --all            # 生成系统与基线的评测产物
python -m evals.run_eval --samples 3      # 三层评测，Judge 结果带缓存
python -m evals.report                    # 生成 evals/report.html
```

## 部署

- **后端**：[`deploy/contract-copilot.service`](deploy/contract-copilot.service)（systemd）和 [`deploy/nginx.conf`](deploy/nginx.conf)（已关闭 SSE 缓冲）。单进程多线程运行：等待用户输入的会话不占线程，闲置会话自动回收出内存，吞吐由 `LLM_MAX_CONCURRENCY` 决定，详见 [`docs/ENGINEERING.md`](docs/ENGINEERING.md)。
- **前端**：[`.github/workflows/pages.yml`](.github/workflows/pages.yml) 构建并发布到 GitHub Pages，后端地址通过仓库变量 `API_BASE` 注入。

## 项目结构

```
Agent_generation/
├── agent/
│   ├── agents/        # 各步骤 Agent
│   ├── pipeline/      # 8 步状态机、检查点、兜底
│   ├── runtime/       # LLM 网关、多协议适配、工具 Harness、取消与预算
│   ├── repository/    # 模板库（MySQL / 本地 JSON）
│   └── skills/        # Skill 定义：SKILL.md + tools.json + scripts/
├── configs/           # 各 Agent 的模型与提示词配置
├── server/            # FastAPI 后端
├── db/                # 表结构、迁移、模板导入
├── evals/             # 评测框架、数据集、Rubric、报告
├── tests/             # 后端测试
└── main.py            # CLI 与服务入口
frontend/              # Vue 3 前端与 Playwright E2E
deploy/                # 部署配置
docs/ENGINEERING.md    # 工程设计说明
```

## 已知局限

- 对抗式大纲的审查方只检查遗漏，不检查重复和层级，修复轮次偶尔会引入主题重叠。
- 个别合同存在交叉引用指向不存在条款、前后条件不一致的问题，评测审计明细中可定位到具体条款。
- 后端为单进程设计（流程以等待 LLM 为主，单进程即可支撑数十个会话同时生成）；横向扩展到多台机器需要引入 Redis 事件总线与分布式锁。
- Anthropic、OpenAI Responses、Gemini 协议目前只用本地 mock 服务验证过。

## 免责声明

本项目生成的合同仅供参考，不构成法律意见。正式签署前请由专业律师审核。

## License

[MIT](LICENSE)
