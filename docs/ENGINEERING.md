# 工程设计说明（鲁棒性 · 评测 · 平台）

本文档描述 v2 版本新增的工程化能力。代码位置均为相对 `Agent_generation/` 的路径。

## 1. 架构总览

```
frontend/ (Vue3, 静态部署)  ──HTTPS/SSE──▶  server/ (FastAPI)
                                              │ RunManager：每会话一个运行单元（取消令牌/工作区/预算/事件通道）
                                              ▼
                                       agent/pipeline/ContractPipeline（8 步可重入状态机，检查点）
                                              │
              ┌───────────────────────────────┼───────────────────────────────┐
              ▼                               ▼                               ▼
   agent/runtime/llm_gateway        agent/runtime/tool_harness        agent/repository
   （唯一 LLM 出口）                 （唯一工具出口）                   （模板库：MySQL / 文件）
```

## 2. 节点鲁棒性

### 2.1 LLM 网关 `agent/runtime/llm_gateway.py`
| 机制 | 实现 |
|---|---|
| 重试 | 指数退避 + 抖动；识别 429 `Retry-After`；鉴权/参数错误（400/401/403/404/422）不重试 |
| 降级 | 主模型连续失败 `fallback_after` 次 → 备用模型（可配置；当前主备均为 `deepseek-flash`，降级退化为同模型重试） |
| 模型白名单 | `LLM_ALLOWED_MODELS`（默认仅 `deepseek-flash`）；配置或代码中出现其它模型名时一律改写为默认模型，防止误用高价模型 |
| 熔断 | 按 `base_url|model` 维度；连续失败 5 次 OPEN，30s 后 HALF_OPEN 放行探测，成功即 CLOSED；OPEN 时直接走备用模型 |
| 超时 | 连接超时 + 流式空闲超时（chunk 间隔）+ 单次尝试总时长上限 |
| 舱壁 | 按供应商限制并发（`LLM_MAX_CONCURRENCY`），并行 Agent 不会打爆限流 |
| 取消 | 取消令牌触发时立即关闭 HTTP 流，阻塞中的线程马上退出 |
| 预算 | 单次运行 LLM 调用次数 / Token 上限（防死循环烧钱） |
| 结构化输出 | `call_llm_json`：JSON 解析 + Schema/业务校验失败时，把错误回喂模型自修复（默认 2 次） |
| 空响应 | 视为可重试故障 |
| 可观测 | `llm.start/retry/fallback/done/error` 事件 + 按 Agent/模型聚合的调用、Token、耗时、重试指标 |

`RunCancelled` 继承 `BaseException`，Agent 内部大量 `except Exception` 无法吞掉取消信号。

### 2.2 工具 Harness `agent/runtime/tool_harness.py`
- 白名单：只执行该 Agent JSON 声明的工具；系统中不存在执行任意代码/命令/文件读写的工具
- 参数规范化与校验：单值→列表、长度一致、locator 格式与存在性、position 合法、内容非空且限长、层级上限
- 事务：执行前快照；异常 / 工具自报失败 / 结构不变量破坏 → 回滚
- 审计：每次调用记录 `status / error_code / 批量大小 / 影响 locator / 前后结构哈希 / 耗时`，供评测与前端展示
- 修复：chunk 并行起草时保留全局 locator，原 `find_parent` 按位置索引越界——改为按 locator 值查找 + 统一 `renumber()`

### 2.3 步骤级兜底 `agent/pipeline/engine.py`
| 步骤 | 主策略 → 备选 → 兜底 |
|---|---|
| 1 需求提取 | LLM 多轮 → 单轮失败回滚并提示重试 → 轮数上限后用已收集信息继续；摘要失败用字段拼接 |
| 2 模板匹配 | 前缀召回 + LLM 精排 → BM25 → 全库 BM25 粗筛 + LLM → 空（走零模板路径） |
| 3 大纲生成 | 有模板生成（Schema 校验）→ 零模板对抗式生成 → 通用合同骨架 |
| 4/8 交互修改 | 单轮超时/失败 → 回滚本轮修改与对话，提示用户 |
| 5 并行起草 | 单 chunk 重试 → 失败 chunk 保留大纲原文；全部失败才判失败；已完成 chunk 断点续跑 |
| 6 并行审查 | 单维度重试 → 跳过该维度（记录降级）；已完成维度断点续跑 |
| 7 修订 | Plan 结构化校验 + 自修复 → 计划失败合同保持不变 |
| Web 检索 | 博查 API → Bing 网页解析 → 模型自身知识；并行抓取 + 整体时间预算 + 熔断 + 缓存 |

所有降级都会推送 `step.degraded` 事件并记入检查点，前端可见。

### 2.4 会话隔离
`RunContext`（contextvars）携带：取消令牌、私有工作区 `workspaces/<user>/<conversation>`、事件出口、预算、指标。
替代了原先的类级全局 `_streaming_ctx`（并行 Agent / 多会话事件串台）与全局 `outputs/*.json`（并发互相覆盖）。

## 3. 停止 / 继续 与原子性

- **检查点**：每完成一个步骤（以及交互步骤的每一轮）写一次 `PipelineState`（原子写文件或单条 SQL 事务）
- **停止**：前端立即结束“处理中”状态；后端取消令牌触发 → LLM 流关闭 → 当前步骤抛 `RunCancelled`，**未完成步骤不落任何产物**
- **防双写**：`conversations.run_epoch` 每次启动 +1，检查点写入 `WHERE run_epoch=:ep`，已停止运行的残留线程写入会被拒绝
- **继续**：从数据库最后一个完整检查点恢复 → 未完成步骤整体重放（并行步骤内已完成的子任务跳过）
- **服务重启**：启动时把遗留 `running` 会话置为 `paused`，用户可继续
- **消息幂等**：前端生成 `client_msg_id`，网络重试/双击只记一条
- **完成即关闭**：`completed` 状态拒绝新消息与继续

## 4. 平台安全
- bcrypt(12) 密码哈希；JWT HS256；5 次失败锁定 15 分钟；登录/注册与发消息按 IP 限流
- 所有会话接口按 `user_id` 过滤（跨用户访问返回 404）
- 输入清洗：去控制字符、长度上限；Markdown 渲染经 DOMPurify
- 配额：每用户 / 全局并发运行数；单次运行 LLM 调用与 Token 预算
- CORS 白名单（含 `*.github.io` 正则，可配置）
- 未实现（建议上线前补充）：HTTPS 由 Nginx 终止、refresh token 轮换、验证码/邮箱验证、审计日志查询界面

## 5. 三层评测 `evals/`

| 层 | 指标 | 方法 |
|---|---|---|
| 端到端 | 要素准确率、必备条款召回率、结构合法性、占位符残留 | 规则（cn2an 数字归一 + 正则，定位到 locator），零方差 |
| | 6 维 Rubric（需求符合/完整/合规/一致/可执行/规范） | 分析型二值检查项 + 证据绑定 + k=3 投票 |
| | 成对盲评（vs 单 LLM） | A/B 与 B/A 双向，一致才计胜负 |
| 大纲 | 3 维 Rubric（覆盖/结构/可起草性） | 同上；对比：系统大纲 / 对抗初稿 / 对抗收敛 / 单 LLM |
| | WebSearch 噪声率、利用率、收敛轮数 | 每条检索结果 LLM 相关性投票 + 主题词被大纲吸收判定；ModelB 首次“完备”轮次 |
| 补全 | 终态要素准确率（大纲→起草→修订）、非法操作率、批量利用率、绕路率、自纠正率、编辑落地率、Leader 计划执行率 | ToolHarness 审计轨迹，确定性计算 |

**LLM-as-Judge 可复现与可审计**
1. 不打整体分，只判二值检查项；“通过”必须给出逐字原文引用 + locator，程序核验引用存在于原文，否则判不通过（抑制幻觉式通过）
2. k 次独立采样、每次打乱检查项顺序，逐项多数决；报告 Fleiss κ 与全一致率
3. 成对比较双向交换消除位置偏差
4. Judge 模型、温度、采样数、Rubric 内容哈希写入报告；每次调用按 (rubric 版本, 模型, prompt 哈希, 采样序号) 缓存原始输出（`evals/cache/judge.jsonl`），重跑结果一致；报告中每个检查项可追溯到缓存键
5. Rubric 修改必须升级 version

模型说明：系统全部 Agent、单 LLM 基线、Judge 均使用 `deepseek-flash`。基线为同模型单次生成（同 Schema、同需求、无模板/检索/审查），
衡量的是“多 Agent 流水线”相对“单次生成”的增益（环境中无 Claude API Key，未做 Claude 对比）。
注意：Judge 与被评对象同模型，存在自我偏好风险；已通过证据核验与双向成对比较缓解，但无法完全消除。

## 6. 部署
- 后端：`deploy/contract-copilot.service`（systemd）+ `deploy/nginx.conf`（SSE 关闭缓冲）+ `deploy/backend.env.example`
  - 单进程（`--workers 1`）：运行状态与事件通道在进程内；多实例需引入 Redis 事件总线与分布式锁（检查点已在 MySQL，可直接复用）
- 前端：`.github/workflows/pages.yml` 构建并发布 GitHub Pages；仓库变量 `API_BASE` 写入 `dist/config.js`

## 7. 账户、免费额度与自有模型 API（2026-11-07 新增）

**免费额度**（`server/account.py`，环境变量可调）
- `FREE_RUNS=2`：每用户 2 次免费完整合同生成（流程走完 Step 8 才计 1 次；中途停止 / 失败不计次）
- `FREE_TOKENS=1800000`：平台 Key 的累计 Token 上限。估算依据：6 个评测用例真实运行（deepseek-flash），
  单次完整生成 38.7 万 ~ 92.6 万 tokens，均值约 60 万（约 80% 为输入上下文）→ 3 次 ≈ 180 万
- 任一额度用尽：发送消息 / 继续生成返回 HTTP 402；运行中 Token 耗尽则在当前步骤安全中止（`BudgetExceeded` 继承 BaseException，
  不会被步骤兜底吞掉而继续消耗），会话进入“已暂停”，配置自有 API 后点击“继续生成”从检查点恢复
- 启用自有 API 时不消耗免费额度；每次运行结束写入 `usage_log`（billing=free/own、tokens、调用次数、是否完成）

**自有模型 API**（`agent/runtime/providers.py`，纯 httpx 实现，不依赖厂商 SDK）
| API 格式 | 端点 | 鉴权头 |
|---|---|---|
| OpenAI Chat Completions | `{base}/chat/completions` | `Authorization: Bearer` |
| OpenAI Responses API | `{base}/responses` | `Authorization: Bearer` |
| Anthropic Messages | `{base}/v1/messages` | `x-api-key` + `anthropic-version` |
| Google Gemini | `{base}/v1beta/models/{model}:streamGenerateContent?alt=sse` | `x-goog-api-key` |
- 预置服务商：DeepSeek、OpenAI、Anthropic、Gemini、阿里云百炼、Kimi、智谱、火山方舟、硅基流动、OpenRouter、Ollama、自定义
- 统一流式输出 content / reasoning / usage，错误映射为可重试 / 不可重试，复用网关的重试、超时、取消与预算
- 连通性测试：发送极短请求，校验能解析到模型输出（Base URL 指向网页、协议选错等常见误配置会给出明确提示）
- API Key 使用 Fernet 加密存储（密钥 `SECRET_KEY` 环境变量或本地 `.secret_key`），接口只返回脱敏提示 `sk-…abcd`

**数据保留**：删除会话为软删除（`conversations.deleted=1`），消息、事件、合同版本全部保留；停止 / 中断不删除任何数据（测试覆盖）。

**前端**：并行阶段（起草 / 审查）只显示一条聚合进度，不渲染各 Agent 的思考流与逐条工具调用；文档面板更新节流 400ms、滚动按帧合并；
侧栏与文档面板宽度可拖拽（双击恢复默认，持久化）；左下角头像进入个人主页（头像、昵称、密码、额度、模型配置）。
