# AGENT 09：Context Engineering 上下文工程

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：上下文分层、预算、压缩、记忆、检索、恢复与降级
> 后续专项：Skills、MCP、Persistence、UI Event 和端到端链路继续核验

## 1. 结论摘要

上下文工程不是“尽量把所有历史塞给模型”，而是为每类信息定义存放位置、进入模型
的条件、退出方式和恢复入口：

```text
事实存储（DB / Workspace / Artifact Store / Event Log）
  ↓ 固定 revision
Context Snapshot
  ├─ 常驻控制面：Agent / Policy / Goal / 当前输入
  ├─ 工作集：近期消息 / 当前步骤 / 相关工具结果
  ├─ 摘要：较早事件的结构化延续载体
  └─ 引用：Memory / Knowledge / File / Artifact 的检索入口
  ↓ 预算与压缩
Model Context
  ↓ 新消息、Action、Artifact、证据
事实存储
```

Grok Build 的核心做法是把三件事分开：

1. **Compaction**：压缩当前会话，保留任务延续所需事实。
2. **Memory Flush**：压缩前提取值得跨会话保存的长期信息。
3. **Memory Retrieval**：首轮或压缩后按当前问题检索少量相关记忆。

EVERYDAYAIONE 已有更适合 SaaS 的不可变 `ContextSnapshot`、Turn revision、分桶预算、
工具归档、会话摘要、知识检索、ResourceManifest 和三层 Memory。主要问题不是能力
缺失，而是这些模块尚未共享一个显式 `ContextPlan`：

- PromptBuilder 可以组装内容，但没有统一说明每块为何进入、占多少、何时失效。
- Web 与企微存在不同容量和压缩参数，尚未由模型能力和 Run 类型统一推导。
- 工具结果“缩短文本”和“可按引用取回原文”没有形成稳定协议。
- 会话摘要、循环摘要、Session Memory、Memory V2 的职责存在交叠。
- 压缩失败没有 Grok 式抑制状态，可能在后续循环重复消耗模型和时间。

推荐保留本项目的 Snapshot 优势，新增轻量的
`ContextPlan → ContextAssembler → ContextCompactor → ContextReceipt`，而不是重写
所有上下文模块。

## 2. Grok Build 的上下文生命周期

### 2.1 自动压缩门槛

源码：

- `crates/common/xai-grok-agent/src/compaction.rs`
- `crates/codegen/xai-grok-shell/src/session/compaction_config.rs`
- `crates/codegen/xai-grok-shell/src/session/compaction.rs`

默认策略：

| 参数 | Grok 默认值 | 语义 |
|---|---:|---|
| `auto_compact_threshold_percent` | 85% | 达到上下文窗口占比后触发 |
| `compaction_wall_clock_budget` | 300 秒 | 压缩的总墙钟上限 |
| `two_pass_compaction` | `false` | 默认单次摘要 |
| `memory_flush_before_compaction` | `false` | 能力存在，但默认不开 |
| compaction model | 当前模型 | 未配置专用模型时复用 |

85% 不是可复制到所有模型的绝对答案。它依赖模型窗口、输出保留、工具 schema 固定
成本和压缩调用延迟。本项目应以 `usable_input_tokens` 计算触发，而不是只看厂商标称
窗口。

### 2.2 压缩失败抑制

Grok 不会在每次模型循环中无脑重试压缩。它记录四种状态：

- `NONE`：可正常触发。
- `TURN`：本轮可恢复错误，本轮内不再重试，下轮清除。
- `STICKY`：内容尺寸、schema 等稳定失败；只有窗口变化、rewind、换模型或成功压缩
  才清除。
- `UNTIL_SUCCESS`：认证、余额等基础设施问题；下一次普通模型调用成功后才清除。

同时有单次 in-flight guard，避免并发压缩；异步预执行结果带
`prefix_len + fingerprint + model_slug`，只在上下文前缀仍一致时复用。

这是本项目需要补齐的关键可靠性机制。现在摘要失败通常直接降级到预算裁剪，但没有
持久、可解释的“本 Run 为何不再摘要”状态。

### 2.3 压缩产物不是普通聊天文本

Grok 支持三种压缩模式：

- `Summary`：较早对话变成延续摘要。
- `Transcript`：完整历史保留在 `updates.jsonl`，上下文只放读取提示。
- `Segments(detail)`：简化消息和摘要分段持久化，可按细节级别恢复。

摘要清洗会：

- 去掉模型草稿 `<analysis>`。
- 解包外层 `<summary>`。
- 中和摘要正文中的控制标签，防止下一轮被提示注入。
- 检查摘要是否短到无法承载任务，退化摘要不能作为成功结果。
- 明确告诉模型“摘要只覆盖更早部分”，避免把它误认成当前用户输入。

因此本项目的 `[工具循环摘要]`、`以下是之前对话的摘要` 也应升级为有类型、有覆盖
边界、有来源 revision 的内容块，不能仅靠字符串前缀区分。

## 3. Grok Build 的 Memory 路径

### 3.1 压缩前 Flush

源码：`session/helpers/memory_flush.rs`

Flush 阈值位于自动压缩阈值之前：

```text
flush threshold
= context window × compact percent
- soft_threshold_tokens headroom
```

它只提取可复用的信息，例如决策、技术上下文、失败尝试、待办和文件状态；OS、shell
等全局偏好不写到会话记忆。增量 Flush 只要求新信息，并保留上次成功内容作为 delta
基线。

结果保护包括：

- 空回复/无回复不写入。
- 超过 `max_flush_write_chars` 截断。
- 文本去重。
- 向量语义去重默认相似度 `0.92`，KNN 只取 3 条。
- Flush 期间禁止再次触发自动压缩。

### 3.2 按需恢复

源码：

- `session/helpers/memory_context.rs`
- `session/helpers/compaction_context.rs`
- `session/memory_state.rs`

首轮可注入记忆；压缩后会用当前摘要/查询恢复相关记忆。压缩后检索参数是：

- `limit=3`
- `score_threshold=0.0`
- 单条 snippet 最多 500 字符

结果以 `<memory-context>` reminder 注入，并检测会话是否已有同一记忆块。已有则复用，
不重新搜索，避免检索分数变化导致 system prompt 前缀漂移、破坏缓存与异步压缩
fingerprint。

模型也可以主动调用 memory search。这说明“自动注入”和“模型按需查询”是两条
互补路径，不应把全部长期记忆常驻上下文。

## 4. EVERYDAYAIONE 现状

### 4.1 已有优势

| 能力 | 现有实现 | 判断 |
|---|---|---|
| 固定历史边界 | `context_snapshot.py` + Turn revision | 优于仅进程内对话数组 |
| 缓存一致性 | Redis v2 闭合历史信封 | revision 不匹配即回源 DB |
| 历史加载 | `history_loader.py` | 8K token 驱动，不再固定 10 条 |
| 工具预算 | `context_compressor/budget.py` | 独立工具桶，旧结果优先归档 |
| 历史预算 | 同上 + `message_scorer.py` | 规则与相关度结合，保护尾部 4 条 |
| 循环摘要 | `context_compressor/summary.py` | 超阈值摘要，失败预算裁剪 |
| 资源边界 | `resource_manifest.py` | 输入资产和允许路径显式化 |
| 长期记忆 | `services/memory/` | L1 Atom、L2 Scene、L3 Persona |
| 知识过滤 | `chat_context/knowledge.py` | ≥0.7 全留，0.5～0.7 最多 1 条 |

当前主要参数：

| 链路 | 总预算 | 历史桶 | 工具桶 | 压缩触发 | 原文保护 |
|---|---:|---:|---:|---:|---:|
| 普通/企微 | 32K | 8K | 6K | 80% | 工具最近 10 轮 |
| Web | 200K | 200K | 200K | 70% | 最近 10 个用户回合 |

循环摘要最多 500 字符，会话摘要默认最多 2000 字符。历史相关度最终分数为
`0.4 × rule + 0.6 × relevance`；规则分低于 0.2 时直接判低分。

### 4.2 当前断层

1. **预算不是模型能力派生**：32K/200K 是通道配置，不是
   `model context window - output reserve - tool schema - fixed prompt`。
2. **摘要职责重叠**：会话摘要、循环摘要、Session Memory、Memory V2 都可能保存
   “过去发生了什么”，但生命周期和权威级别不同。
3. **归档不可恢复**：工具文本压成元数据后，没有统一 `artifact_ref/tool_run_id`
   让模型按需读取原结果。
4. **组装无回执**：目前难以回答某次 Run 丢弃了哪些内容、各块 token、为何保留。
5. **压缩无抑制状态**：模型或认证失败后可能在后续轮次再次触发相同失败。
6. **旧实现并存**：`services/memory/context_compressor.py` 与 Handler compressor 的
   阈值和算法不同，需要在调用方审计阶段确定权威入口，不在本轮擅自删除。

## 5. 目标信息分层

### 5.1 L0：权威事实层，不直接整块进入模型

保存：

- 完整 messages、ToolCall、ToolOutput、Action/Attempt、审计事件。
- 文件原文、图片、视频、表格、代码和导出文件。
- Goal 状态、计划、证据、Artifact metadata。
- 长期 Memory atoms/scenes/persona。

位置：PostgreSQL、Workspace、OSS/Artifact Store、可检索索引。

原则：大对象永远存引用；UI 事件不是模型事实源；摘要不能覆盖原始记录。

### 5.2 L1：常驻控制面

每次模型调用必须有：

- AgentDefinition 与当前 EffectiveCapabilities。
- 当前 InteractionMode、Policy 约束和有效 AuthorizationGrant 摘要。
- 当前用户输入与输入资产 manifest。
- 若属于 Goal：Objective、Acceptance Contract、当前 gap/step、剩余预算。
- 当前时间、租户/会话作用域等确实影响执行的环境事实。

禁止常驻：完整工具目录、历史 UI progress、二进制内容、无关用户画像、过期授权。

### 5.3 L2：近期工作集

包括：

- 最近闭合 Turn。
- 当前未完成 ToolCall 与对应 ToolOutput。
- 当前步骤引用的文件片段、查询结果和错误。
- 最近一次用户纠正、决策和未解决问题。

按 token 从近到远保留，但必须以完整 ToolCall/ToolOutput 对和 Turn 边界切割。不能把
工具结果留在没有调用原因的位置，也不能把 assistant 调用留下而删除 output。

### 5.4 L3：压缩延续层

摘要必须是结构化 `ContextSummary`：

```text
summary_id
conversation_id
from_revision / through_revision
objective_and_intent
decisions
completed_actions
artifacts[]
failed_attempts
open_questions
next_step
source_refs[]
model / prompt_version / created_at
```

摘要进入上下文时只带延续字段和引用。事实冲突时优先级：

```text
当前用户输入 > 权威 ToolOutput/Artifact > 原消息 > 摘要 > 检索记忆
```

### 5.5 L4：按需检索层

长期 Memory、知识库、旧 ToolOutput、文件全文和历史 Transcript 默认不进入。模型通过
稳定工具按需取回：

- `memory_search/get`
- `knowledge_search/get`
- `artifact_search/get`
- `tool_output_get`
- `workspace_search/read`

Search 只回 snippet、score、来源和 ref；Get 才读取指定正文。每次结果仍受 Context
预算和权限控制。

## 6. 预算模型与推荐参数

不建议把固定百分比写死为全局常量。每个 Run 先计算：

```text
usable_input
= model_context_window
- reserved_output_tokens
- safety_margin

dynamic_budget
= usable_input
- fixed_prompt_tokens
- effective_tool_schema_tokens
```

第一阶段推荐参数是迁移起点，不是永久行业标准：

| 参数 | 推荐初值 | 说明 |
|---|---:|---|
| 输出保留 | `max(模型输出上限, 窗口 15%)` | 避免输入吃满导致无法完成 |
| 安全余量 | 窗口 5% | token 估算误差和 Provider 包装 |
| 自动压缩 | usable input 的 80% | 介于现有 70/80 与 Grok 85 |
| 紧急裁剪 | usable input 的 95% | 只作最后防线 |
| 最近 Turn 保护 | 至少 2 个完整 Turn | 按任务复杂度扩张 |
| 单次记忆自动注入 | 最多 3 条 | 与 Grok 相同的保守起点 |
| 单条记忆 snippet | 500 字符 | 与 Grok 相同，Get 可展开 |
| 摘要最小有效性 | 必须含 objective + open/next | 不只用字符长度判断 |

工具 schema 很大时先做 EffectiveToolset 选择，再组上下文；不能先注入全部工具再靠
删除聊天历史弥补。高风险 Action 的 Policy 信息不得被压缩掉。

## 7. 目标组件

### 7.1 ContextPlan

由 Session Actor 在调用模型前冻结：

```text
ContextPlan
  run_id / snapshot_revision / model
  input_budget / output_reserve / safety_margin
  required_blocks[]
  retrieval_queries[]
  protected_refs[]
  compaction_policy
```

它描述“应装什么和预算多少”，不保存大段文本。

### 7.2 ContextAssembler

按确定顺序组装：

```text
Agent + Policy
→ Goal Contract
→ Memory/Knowledge reminders
→ ContextSummary
→ Recent closed history
→ Current input + ResourceManifest
→ EffectiveTool schemas
```

组装后执行 Tool 对完整性、revision 边界、授权有效期和 token 上限校验。

### 7.3 ContextCompactor

保留现有专业实现，但统一入口：

1. 归档陈旧 ToolOutput，保留可恢复 ref。
2. 必要时生成结构化 ContextSummary。
3. 摘要失败记录 suppression reason。
4. 超限时按相关度裁剪非保护历史。
5. 最后才进入 emergency tail-preserving truncation。

### 7.4 ContextReceipt

每次调用记录：

- 各 block 的来源、revision、token 和 hash。
- 被压缩/裁剪的 block 与原因。
- 使用的 summary/memory/artifact refs。
- 预算、触发阈值、suppression 状态。

Receipt 用于调试、成本统计和复现，不注入模型，也不保存敏感正文。

## 8. 失败、并发与恢复边界

| 场景 | 正确行为 |
|---|---|
| Summary 模型失败 | 使用原工作集继续；预算不足则确定性裁剪；记录 TURN/STICKY/UNTIL_SUCCESS |
| Summary 内容退化 | 不覆盖旧有效摘要；进入降级路径 |
| Snapshot revision 不存在 | fail closed，不猜测共享 Redis 最新历史 |
| ToolCall/Output 被切断 | 调整到完整 pair；无法闭合则标记中断事实 |
| Memory 检索超时 | 不阻塞普通 Run；不伪造“无记忆”持久事实 |
| 检索结果漂移 | 同一 Run 固定 refs/hash；重试复用，不重新搜索 |
| Artifact 已删除/无权限 | 返回结构化 unavailable，不把旧摘要当原文 |
| 并发压缩 | conversation + base revision 幂等键；同 revision 只接受一个有效结果 |
| 模型切换 | 重新计算预算并清理不适用的 sticky suppression |
| 用户纠正旧事实 | 原消息保留；新 correction 进入高优先级并使旧摘要失效重建 |

## 9. 与 Grok 的取舍

直接采用：

- 压缩、记忆 Flush、记忆 Retrieval 三分法。
- 85% 前留出操作空间的思想，而非照抄数值。
- 压缩失败抑制状态与单 in-flight guard。
- 压缩结果 prefix fingerprint 校验。
- 摘要控制标签清洗和退化检查。
- 首轮/压缩后小规模记忆恢复，结果幂等复用。

保留本项目更优部分：

- 数据库固定 revision Snapshot 和持久 Worker 恢复。
- 按工具/历史分桶，而非只有总窗口阈值。
- SaaS 多租户权限、ResourceManifest 和 OSS Artifact。
- Memory L1/L2/L3 的长期组织能力。

不照搬：

- 本地 `updates.jsonl` 作为唯一 Transcript；服务端以数据库 Event/Message 为事实源。
- 所有模型统一 85%。
- 默认开启每次压缩前 Memory Flush。
- 用 XML 字符串作为长期协议；内部应使用类型化 ContentBlock。

## 10. 分阶段落地边界

本轮只形成设计，不进入编码。后续总体重构建议：

1. 先审计两套 compressor 和所有 PromptBuilder 调用方，确定唯一入口。
2. 定义 `ContextBlock/ContextPlan/ContextReceipt`，先做观测，不改变组装结果。
3. 将预算改为模型能力派生，保留旧配置作为上限和回滚开关。
4. 为工具归档增加稳定 ref 与 `tool_output_get`。
5. 将会话摘要升级为带 revision 覆盖范围的 `ContextSummary`。
6. 增加 compaction suppression、in-flight 和 fingerprint。
7. 最后统一 Memory/Knowledge/Artifact 的 Search → Get 协议。

在 Skills、MCP、Persistence 层完成前，不冻结最终字段：Skill 指令、MCP resource 和
持久化事件都会占用或影响 ContextPlan。

## 11. 本轮影响范围与下一步

本轮仅修改研究文档，不修改运行代码、数据库或 API。

下一层进入 Skills Runtime，重点核验：

- Skill 如何发现、加载、按需进入上下文。
- Skill 内容是指令、资源还是可执行能力。
- Skill 与 Tool/MCP/Policy 的权限边界。
- 多 Skill 冲突、版本、缓存和执行步骤恢复。
- 如何避免所有 Skill 全量常驻上下文。
