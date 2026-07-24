# AGENT 03：Agent 定义、实例与能力装配

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：Agent 身份、提示词、工具、Skills、权限、预算和会话资源的装配
> 后续专项：Model Loop、Policy、ToolBridge、Context、Skills、MCP 和 Subagent 分别深入

## 1. 结论摘要

Grok Build 对 Agent 的核心定义很直观：

```text
AgentDefinition（可移植、可版本化）
  + Session 级资源与策略
  ↓ AgentBuilder
Agent（绑定某次 Session 的运行实例）
```

EVERYDAYAIONE 已具备 PromptBuilder、工具域隔离、权限模式、执行预算、组织上下文和专业执行器，但主聊天 Agent 没有一个明确的定义与实例边界。它由 `ChatHandler`、`PreparedChatStream`、`PromptBuilder`、工具配置、`ToolLoopContext` 和若干进程内上下文共同构成；ERP、定时任务和图片 Agent 又有各自的装配方式。

第一轮结论为“融合升级”：

1. 采用 Grok 的 `AgentDefinition` 与 Session-bound `AgentInstance` 分层。
2. 保留本项目按租户、组织、权限和请求动态过滤工具的能力。
3. 保留 PromptBuilder 的静态、会话稳定和轮次动态分层，它更适合 SaaS 个性化与缓存。
4. 不复制 Grok 2,396 行的集中式 `AgentBuilder`；本项目应使用声明式定义和小型组合器。
5. 统一的是 Agent 契约与装配顺序，不把 ERP DAG、媒体任务等专业执行器改造成同一个万能 Agent。

## 2. Grok Build 的 Agent 分层

### 2.1 AgentDefinition：稳定身份契约

源码：`crates/codegen/xai-grok-agent/src/config.rs::AgentDefinition`

源码注释明确把它定义为可从 `.grok/agents/*.md` 解析、可作为顶层或子 Agent 使用的 portable identity。它不包含压缩和系统提醒等 Session 策略。

主要字段：

| 类别 | 字段与语义 |
|---|---|
| 身份 | `name`、`description`、`plugin_name`、`source_path`、`scope` |
| 提示词 | `prompt_mode`、`prompt_body`、`system_prompt`、`user_message_template`、`initial_prompt` |
| 工具 | `tool_config`、`inject_default_tools`、`tools`、`disallowed_tools` |
| 能力 | `capability_mode`、`permission_mode`、`isolation`、`background` |
| Skills | `skills`、`discover_skills=true`、`inherit_skills=true` |
| 项目规则 | `agents_md=true` |
| 推理 | `effort`、`model`、`max_turns` |
| 扩展 | `mcp_servers`、`mcp_inheritance`、`hooks`、`memory` |
| 完成 | `completion_requirement` |
| 子 Agent | 构建期派生的 `allowed_subagent_types` |
| 会话钳制 | `session_tools_allowlist`、`session_tools_denylist` |

`CompletionRequirement` 进一步声明：

```text
tool
reminder
recovery:
  max_retries
  base_delay_ms
  max_delay_ms
```

这让“必须调用某工具才算完成”成为机器可校验契约，不只是提示词要求。

内置定义通过 `BuiltinAgentName::definition()` 生成，包括 Grok Build、Plan、Explore、Browser Use、Orchestrator 等。可移植定义和内置模板最终进入同一构造链。

### 2.2 AgentBuilder：把定义绑定到 Session

源码：`crates/codegen/xai-grok-agent/src/builder.rs::AgentBuilder`

Builder 同时接收三类输入：

1. Agent 作者声明：名称、提示模式、工具 allow/deny、Skills、权限。
2. Session 资源：真实工作目录、展示目录、文件系统、终端、环境变量、状态路径、Session ID。
3. 运行策略：压缩、提醒、上下文窗口、搜索、内存、LSP、图片、视频、子 Agent、询问用户、插件和 MCP 输出上限。

关键默认值：

| 参数 | 默认值 |
|---|---:|
| `prompt_mode` | `Extend` |
| `permission_mode` | `Default` |
| `agents_md` | `true` |
| `memory_enabled` | `false` |
| `backend_search` | `false` |
| `write_file_enabled` | `true` |
| `subagents_enabled` | `false` |
| `ask_user_question_enabled` | `true` |
| `system_reminder_tag` | `system-reminder` |

`build()` 的实际顺序：

```text
解析 AgentDefinition
→ 发现或继承 Skills
→ 解析显式预加载 Skill，并注入 prompt_body
→ 克隆定义内 tool_config
→ 按开关注入默认工具
→ 删除不可用的 memory / ask-user / subagent 工具
→ 合并 Bash、WebFetch、AskUser 参数
→ 应用 Agent 作者 allowlist / denylist
→ 应用子 Agent 类型限制
→ 最终应用 Session 操作者 allowlist / denylist
→ finalize ToolBridge + SessionContext
→ 写入 MCP 结果截断配置
→ 恢复 Skill 公告状态
→ 发现并种入 AGENTS.md、gitignore、Skills
→ 构造并渲染 PromptContext
→ 构造 Hosted Tools
→ 返回 Agent
```

顺序很重要：Session 操作者限制是完全装配后的最终钳制，后续默认工具注入不能绕过它。

### 2.3 工具能力一致性

Builder 不只是增删工具，还同步维护关联能力：

- 未启用子 Agent时删除 `task`。
- 没有任何后台任务启动器时删除 `get_task_output`、`wait_tasks`、`kill_task`。
- 禁止全部 `Agent(...)` 后，同时关闭终端后台执行参数。
- `tools` 中的 `Agent(type)` 派生可创建的子 Agent 类型。
- denylist 未匹配任何工具时记录警告。
- allowlist 含无法解析名称时，Grok 当前选择保留完整工具集并告警。

最后一项偏向兼容而非最小权限。多租户 SaaS 不应照搬“解析失败就保留全部”；本项目应 fail closed，或只保留已确定允许的核心工具。

### 2.4 PromptContext

源码：`crates/codegen/xai-grok-agent/src/prompt/context.rs::PromptContext`

它是可序列化的 Agent 提示词输入，当前 `version=1`。主要包含：

- `prompt_mode`、`audience=Primary|Subagent`、自定义正文和模板。
- AGENTS.md 文件及其优先顺序。
- Persona 摘要、角色指令、Persona 指令。
- 构建时间、当前日期、OS、Shell、模型可见工作目录。
- Memory 开关及全局/工作区路径。
- 非交互模式和系统身份标签。

子 Agent 持久化时清除 Persona 摘要，但保留完整 AGENTS.md。Builder 允许工具执行使用真实 overlay/worktree 路径，同时向模型展示原项目路径，避免泄漏内部隔离路径。

### 2.5 Agent：已构建的 Session 实例

源码：`crates/codegen/xai-grok-agent/src/agent.rs::Agent`

最终实例持有：

- `AgentDefinition`
- `PromptContext`
- 已渲染并缓存的 `system_prompt`
- `Arc<ToolBridge>`
- `ReminderPolicy`
- `CompactionPolicy`
- Hosted Tools
- Backend Search 开关

它是 fully built、session-bound、effectively immutable 的对象。动态 MCP 和工具状态由 `ToolBridge` 内部锁管理。

Agent 可根据总 Token 和上下文窗口判断自动压缩，可在时间变化时重新渲染提示词。源码留有明确限制：中途替换定义时，Session 级压缩与提醒策略尚不能同步更新。

## 3. EVERYDAYAIONE 当前实现

### 3.1 主聊天没有显式 Agent 对象

主入口：

```text
execute_chat(ChatExecutionRequest)
→ prepare_chat_stream()
→ ChatHandler._build_llm_messages()
→ PromptBuilder.build()
→ PermissionMode + get_tools_for_mode()
→ ToolLoopContext + ExecutionBudget
→ _run_loop()
```

`ChatExecutionRequest` 已显式承载：

- `user_id`、`conversation_id`、`task_id`、`message_id`
- `model_id`、`context_anchor`、`params`
- `permission_mode="auto"`
- `needs_google_search=false`
- `calculate_credits=true`
- `execution_scope`

`PreparedChatStream` 承载消息、Adapter、权限、核心工具、Provider 参数、工具上下文和预算，已经接近 Agent 运行实例的一部分。但它没有 Agent 身份、定义版本、能力来源、Skill/MCP 清单或统一的资源所有权。

运行期还会写入 Handler：

```text
handler._adapter
handler._pending_emit_payloads
handler._pending_form_block
```

因此一次运行的状态分布在 Request、Prepared Stream、Handler 和 ContextVar 中。后续并发与恢复专项必须核验这些字段是否可能被同一 Handler 的并发执行交叉覆盖。

### 3.2 PromptBuilder 是现有优势

源码：`backend/services/prompt_builder/builder.py`

`BuildInput` 包含用户、会话、组织、正文、工作区文件、图片、普通文件、权限模式、位置、偏好、数据库、摘要、上下文快照、请求时间上下文和个人上下文开关。

它的实际构造层次：

```text
并行获取 memory / summary / history
→ 静态层
→ 会话稳定层：权限、偏好、Persona、Memory
→ 轮次动态层：时间、位置
→ User 层：附件与文本
→ 消息组合
→ 上下文预算约束
```

这比把所有 Agent 信息直接拼成一个 system prompt 更适合 Prompt Cache、个性化和多租户数据门控，应作为未来 AgentFactory 的依赖，而不是被替换。

当前遗留项：`ChatContextMixin` 将 `user_preferences=None` 固定传入，并留有 TODO。该问题只记录，不在本轮修复。

### 3.3 权限和工具装配

源码：`backend/config/chat_tools.py`

`get_chat_tools(org_id)` 按组织身份装入 ERP、爬虫、文件、代码和通用工具。主 Agent 通过 `_CORE_TOOLS` 缩小初始工具集，并用 `filter_tools_for_domain(..., "general")` 做域隔离。

`get_tools_for_mode()`：

- `plan`：移除 `erp_agent`、`image_agent`、`social_crawler`。
- `ask/auto`：保留核心工具。
- 出口按工具名和嵌套 key 规范化，维持 Prompt Cache 字节稳定。

若个人上下文被禁用，`stream_setup.py` 再移除：

- `get_conversation_context`
- `manage_scheduled_task`

Google Search Hosted Tool 只在请求明确需要且 Adapter 支持时动态加入。

这条链路适合 SaaS，但能力过滤分散在组织、Domain、Permission、Personal Context 和 Provider 五个位置，尚无一份最终能力快照供审计和恢复。

### 3.4 权限模式与预算参数

`PermissionMode` 有 `auto`、`ask`、`plan` 三态：

- 无效值静默降级为 `auto`。
- Plan 保存进入前模式，退出后恢复。
- 每 5 轮注入一次 sparse reminder。
- `ask` 没有模式提醒。

主 Agent 预算：

| 参数 | 当前值 |
|---|---:|
| `budget_max_turns` | 15 |
| `budget_max_wall_time` | 600 秒 |
| `context_window_tokens` | 128,000 |

`context_window_tokens` 用于上下文压缩，不在 `PreparedChatStream` 的 Agent 定义中。

无效权限值自动变成 `auto` 对执行型 Agent 风险较高。未来结构化定义解析失败时应拒绝或降级为 `ask/plan`，不能默认获得自动执行权限。

### 3.5 Agent Domain 当前固定

主聊天在 `stream_setup.py::_prepare_request_context()` 中固定创建：

```python
ToolLoopContext(org_id=handler.org_id, agent_domain="general")
```

`ToolLoopContext` 跨轮累计已识别编码、同步警告、已用/失败工具和动态发现工具；失败提示只保留最近 3 个唯一工具。

Domain 已参与动态工具发现过滤，但它不是 `ChatExecutionRequest` 或 Agent 定义的一部分。新增专业 Agent 时容易再次形成硬编码分支。

### 3.6 多种“Agent”装配路径

当前名称相同但运行语义不同：

| 实现 | 装配特点 |
|---|---|
| 主 Chat | PromptBuilder + Chat 专用流式循环 + Handler Mixins |
| ScheduledTaskAgent | 自建轻量上下文、Adapter、ToolExecutor、ExecutionBudget、ToolLoopExecutor |
| ERPAgent | 先提取 DAG，再并行创建领域子执行器，并有 SQL 降级 |
| ImageAgent | 专业提示词与媒体生成流程 |
| 部门 Agent | 领域系统提示词和 ERP 工具策略 |

ScheduledTaskAgent 参数：

| 参数 | 当前值 |
|---|---:|
| 单工具超时 | 30 秒 |
| 上下文窗口 | 50,000 |
| 默认 Deadline | 180 秒 |
| 最大工具轮次 | 12 |

它使用 `get_core_tools()` 后把全套工具一次性暴露给模型，不使用主 Chat 的动态发现方式。ERP Agent 则使用 `dag_global_timeout`，并受父预算剩余时间钳制。

这些差异有合理业务原因，但目前没有共享的 `AgentDefinition → EffectiveCapabilities → AgentInstance` 契约来说明哪些差异是设计、哪些是漂移。

## 4. 差距矩阵

| 能力 | Grok Build | EVERYDAYAIONE | 决策 |
|---|---|---|---|
| 可移植 Agent 定义 | 明确结构体和文件协议 | 分散在代码、模板和配置 | 采用思想 |
| Session Agent 实例 | 明确、近似不可变 | 隐式分布在多个对象 | 融合升级 |
| Prompt 分层 | PromptContext + 模板渲染 | PromptBuilder 三层与预算 | 保留现有优势 |
| 工具最终钳制 | Builder 末端统一执行 | 多位置过滤 | 收口有效能力快照 |
| 多租户过滤 | 本地单用户取向 | 组织、个人上下文、Domain | 保留现有 |
| Skills / AGENTS.md | 构建期发现、继承、恢复 | Skills 未进入主 Chat 统一装配 | 后续专项 |
| MCP | 定义与 Session 都可声明 | 尚未形成主 Runtime 契约 | 后续专项 |
| Completion Requirement | 结构化声明 | 主要依赖提示词/循环行为 | 引入候选 |
| 专业执行器 | Agent/子 Agent 工具集 | ERP DAG、媒体任务更专业 | 保留专业内核 |
| Builder 规模 | 单文件 2,396 行 | 装配分散 | 都不作为目标形态 |

## 5. 推荐目标边界

本阶段只确定形状，不锁定最终字段和目录：

```text
AgentDefinition
├── identity / version / source
├── prompt profile
├── model profile
├── requested capabilities
├── permission baseline
├── budget profile
├── skills / MCP / plugins
└── completion contract

AgentFactory
├── resolve tenant + channel + user policy
├── resolve provider/model capabilities
├── resolve effective tool/skill/MCP set
├── build PromptBuilder input
├── bind Session resources
└── produce immutable AgentInstance

AgentInstance
├── definition snapshot
├── effective capability snapshot
├── prompt/messages
├── model adapter
├── ToolBridge handle
├── policy/budget state
└── runtime resource cleanup
```

约束：

1. `AgentDefinition` 不保存用户 Token、数据库连接或可变执行状态。
2. `AgentInstance` 必须绑定 Session/Run，不做进程全局单例。
3. 最终能力是“定义请求 ∩ 租户授权 ∩ 用户授权 ∩ 渠道限制 ∩ Provider 能力”。
4. 任何解析失败不得扩大权限。
5. Tool Schema、Prompt 中的能力说明和实际 Executor 必须来自同一有效能力快照。
6. PromptBuilder 保持独立；AgentFactory 只组织输入，不重新实现上下文工程。
7. ERP、图片、视频、文件等保留专业 Executor 或子 Agent 内核。

## 6. 边界与失败场景

后续设计和测试必须覆盖：

| 场景 | 预期 |
|---|---|
| Agent 定义缺失 | 使用显式默认定义并记录版本，不临时拼接 |
| 定义格式错误 | 拒绝构建或安全降级，不进入 `auto` |
| allowlist 工具不存在 | 告警并忽略该项，不放开全部工具 |
| Domain 与工具冲突 | Domain/租户策略优先，工具不可见且不可执行 |
| Provider 不支持 Hosted Tool | 从有效快照移除，并同步 Prompt |
| Skill/MCP 初始化失败 | 根据必需/可选属性失败或降级，记录原因 |
| 同 Session 中切换 Agent | 新建定义快照，明确 Prompt、Policy 和资源迁移 |
| 并发构建两个 Run | Handler 可变字段和 ContextVar 不得交叉 |
| Agent 执行超时 | 释放 Adapter、锁和临时资源，保存可恢复状态 |
| 进程恢复 | 由定义版本和能力快照重建，不依赖旧进程对象 |
| 完成契约未满足 | Verifier/Model Loop 触发有限恢复，不伪装成功 |
| 上下文超预算 | 按 Context 策略压缩，不擅自删除权限和任务约束 |

## 7. 影响范围候选

若总体方案最终确认，预计涉及但不限于：

- `backend/services/handlers/chat/execution_engine.py`
- `backend/services/handlers/chat/stream_setup.py`
- `backend/services/handlers/chat_context_mixin.py`
- `backend/services/prompt_builder/`
- `backend/config/chat_tools.py`
- `backend/services/handlers/permission_mode.py`
- `backend/services/handlers/tool_loop_context.py`
- `backend/services/agent/tool_loop_executor.py`
- `backend/services/agent/scheduled_task_agent.py`
- `backend/services/agent/erp_agent.py`
- 图片、视频和未来 MCP/Plugin/Skill 装配入口
- Agent 定义、有效能力快照和 Run 持久化协议

目前不修改这些文件。具体文件和函数清单必须在全部板块调研及端到端链路完成后重新收敛。

## 8. 第一轮验收证据

已核验：

- Grok `AgentDefinition` 的完整字段和 `CompletionRequirement`。
- Grok `AgentBuilder::build()` 的 Skills、工具、Session 钳制、ToolBridge 和 PromptContext 顺序。
- Grok 最终 `Agent` 的 Session-bound 字段和策略更新限制。
- 本项目主 Chat 的 Request、Prepared Stream、PromptBuilder、权限、工具和预算装配。
- 本项目 ScheduledTaskAgent、ERPAgent 的独立装配路径及主要参数。

尚未在本板块下结论：

- 模型循环的并行工具、停止和重试语义。
- Policy Hook 的执行前后次序。
- ToolBridge 的统一输出和错误模型。
- Skill、MCP、插件和子 Agent 的完整生命周期。
- Agent 定义及有效能力快照的最终数据库协议。

这些内容进入后续独立板块，避免在 Agent 层提前设计万能对象。
