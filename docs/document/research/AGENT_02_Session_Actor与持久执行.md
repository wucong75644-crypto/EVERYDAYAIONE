# AGENT 02：Session Actor、队列与持久执行

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：Session 命令、Prompt 队列、所有权、Claim/Lease/Fencing、取消、进度、恢复和等待交互
> 后续专项：模型循环、Goal、Context、MCP 和完整持久化文件格式分别深入

## 1. 结论摘要

双方的 Session Runtime 解决的是同一个问题：让一项 AI 工作拥有稳定身份、串行化状态变化，并能在工具调用和用户交互之间继续运行。

实现取向不同：

- Grok Build：每个 Session 一个 Actor Thread，使用内存 Mailbox 和权威 Prompt FIFO，配合本地更新日志恢复。
- EVERYDAYAIONE：PostgreSQL 任务队列是事实源，Worker 使用 Claim、Lease、Fencing Token 和原子 RPC 获得执行权。

第一轮结论为“融合升级”：

1. 保留本项目数据库 Actor，它在多进程、多租户和强杀恢复方面更适合 SaaS。
2. 引入 Grok 的统一 Session 命令面、权威输入队列、send-now/排队语义和持久等待交互。
3. 不把 Grok 的专用 OS Thread 机械移植到 Python 服务。
4. 将当前分散在 DB Task、WebSocket Manager、Chat Handler 和迁移 RPC 中的 Session 行为收口成统一 Runtime 协议。

## 2. Grok Build Session 模型

### 2.1 创建参数与边界

源码：`xai-grok-shell/src/session/acp_session_impl/spawn.rs::spawn_session_actor`

`spawn_session_actor` 的参数已经覆盖完整 Session 装配，包括：

- `SessionInfo`、Gateway、SamplingConfig、Credentials。
- ToolContext、MCP Servers、Client MCP Servers、父 Session MCP Pool。
- Permission、Telemetry、Auto Update。
- PersistenceHandle、Conversation、Rewind Points。
- 初始 Token、Prompt Text、Compaction 状态。
- AgentDefinition、SkillsConfig、PluginRegistry。
- 持久化 Plan、Goal、Signals、Announcement State。
- Memory、Image、Video、Web Search、Web Fetch。
- Goal、Subagent、Ask User Question 开关。
- Client Hooks、Tool Parameters、Permission Rules。
- `inference_idle_timeout_secs`、`max_retries`、`max_turns`。

硬校验：

```text
max_turns == 0
→ AgentBuildError::InvalidConfig
```

Session 使用 `mpsc::unbounded_channel()` 建立命令 Mailbox。Actor 在专用 Session Thread 内构造，非 `Send` 会话状态不跨线程移动。

优点是状态所有权清晰；代价是 `spawn_session_actor` 参数非常多，说明 Grok 当前 Composition Root 与 Session 构造之间已经存在较高装配复杂度，不能复制其函数签名形态。

### 2.2 Session 状态所有权

源码：`xai-grok-shell/src/session/acp_session.rs::State`

Actor 调度状态：

```text
State
├── running_task
├── pending_inputs: VecDeque<InputItem>
├── pending_notifications
├── notifications_suppressed
├── rewindable
└── nudges_used_this_session
```

对话、Token、Prompt Index、模型配置等已经迁移到独立 `ChatStateActor`，SessionActor 持有 Handle。Credentials 放在同步锁中；调度状态放在 Tokio Mutex 中。

这是一条重要边界：

> SessionActor 管工作调度，ChatStateActor 管模型对话状态。

### 2.3 SessionCommand 命令面

源码：`xai-grok-shell/src/session/commands.rs`

命令按职责可归为：

| 类别 | 代表命令 |
|---|---|
| 初始化 | `Initialize`、`ReplaceSystemPrompt` |
| 输入 | `Prompt`、排队编辑/删除/插队、`Cancel` |
| 模型 | `SetSessionModel`、`OverrideModelName`、`RebuildAgentForDefinition` |
| 模式 | `SessionMode`、Plan Mode、Yolo/Auto Permission |
| 上下文 | `CompactSession`、Memory Flush、History Repair |
| 回退 | `Rewind`、Rewind Points、Tracker Reconcile |
| Skills/Plugins | `RefreshSkillBaseline`、`ReloadPlugins`、`ReloadHooks` |
| MCP | Update/Toggle/Status/Call/Read Resource/Snapshot Pool |
| 子 Agent | Usage Fold、工具和 Hook Snapshot |
| 后台任务 | Foreground、Kill、List、Delete Scheduled Task |
| 持久化 | `CopyFile`、`FlushComplete` |
| 生命周期 | `IsBusy`、`Shutdown` |

大部分需要同步结果的命令携带 `oneshot::Sender`。这让调用者可以等待 Actor 完成真实状态变化，而不是在命令入队后假定成功。

### 2.4 Prompt 输入参数

`SessionCommand::Prompt` 关键参数：

| 参数 | 作用 |
|---|---|
| `prompt_id` | 稳定的 Prompt 身份 |
| `prompt_blocks` | ACP 结构化内容块 |
| `prompt_mode` | 当前 Prompt 模式 |
| `artifact_upload_ctx` | 产物上传上下文 |
| `client_identifier` | 多客户端来源 |
| `screen_mode` | fullscreen/inline/minimal/headless，仅遥测 |
| `verbatim` | 跳过 user_query 包装和大 Prompt 截断 |
| `traceparent` | 跨 Mailbox 的 W3C Trace 关联 |
| `json_schema` | 结构化输出约束 |
| `send_now` | 取消当前轮并使该输入优先 |
| `respond_to` | 返回完整 Turn 结果 |
| `persist_ack` | 用户消息持久化屏障完成后的确认 |
| `parsed_prompt_tx` | 返回截断后文本、原文和落盘路径 |

`persist_ack` 的语义非常重要：

```text
用户消息追加到 Chat History
→ Persistence Flush Barrier
→ ack
→ 才允许依赖持久化事实的调用方继续
```

### 2.5 权威 Prompt 队列

源码：`acp_session_impl/prompt_queue.rs::queue_input`

队列为 `VecDeque<InputItem>`，当前运行 Prompt 仍保留在队首，直到 Turn Completion 或 Cancel 时弹出。这条不变量用于避免取消和队列编辑误删下一条输入。

用户 Prompt 入队过程：

1. 记录当前 Queue Depth。
2. 立即把非空真实 Prompt 追加到按工作目录维护的快速历史。
3. Synthetic Prompt 不进入用户历史。
4. 新用户输入会清理尚未运行的 Synthetic Auto-wake，真实用户输入优先。
5. 用户 Prompt 建立 `QueueEntryMeta`；Synthetic Prompt 不暴露为用户队列项。
6. 判断是否处于可中断等待。
7. 根据 `send_now` 决定插入位置。
8. 广播权威队列变化；广播不持久化。

队列优先级：

```text
当前运行 Prompt
→ 已排队的 send_now Prompt，保持 FIFO
→ 新 send_now Prompt
→ 普通 FIFO Prompt
```

当工具正处于 Blocking Wait 且没有其他用户输入排队时，新用户输入会自动获得 send-now 语义。

Goal Active 时 send-now 只调整排队优先级，不取消正在执行的 Goal Turn；普通 Turn 可被取消。

### 2.6 Turn Completion 类型

`PromptCompletionKind` 区分：

- `Completed`
- `Cancelled`
- `MaxTurnsReached`
- `Rewound`
- `RemovedFromQueue`

`RemovedFromQueue` 不是普通 Cancel：该 Prompt 从未开始，因此不能广播整个 Session 的 Turn Complete 或 Idle，否则多客户端会误以为当前真正运行的 Turn 已结束。

这说明队列操作和 Turn 生命周期必须分别建模。

### 2.7 Cancel 行为

源码：`acp_session_impl/tasks_cancel.rs`

普通取消默认只取消队首运行 Prompt，保留后续用户队列。只有明确要求 kill background tasks 等场景才整体清空。

取消过程包括：

1. 从 Actor State 原子取出 Running Task 和应取消的 Input。
2. 记录是否仍有后续用户 Prompt。
3. 清理 ToolBridge 当前 Prompt 资源。
4. 清理 Goal Loop Active 标志。
5. 记录取消原因与 trigger：`send_now`、Esc、Ctrl+C 等。
6. 处理 dangling tool calls，必要时为下一轮设置中断提醒。
7. Abort Running Task。
8. 将 Blocking Wait Depth 置零，避免异步 Guard 延迟 Drop 误取消下一轮。
9. 保存本轮 Usage。
10. 对每个被取消 Prompt 完成其 `respond_to`，防止客户端永久等待。
11. 继续调度保留的队列。

Grok 对取消竞态的处理细致，特别是“不能丢掉下一条 Prompt”和“所有 oneshot 必须被解决”，值得作为本项目行为基线。

### 2.8 Pending Interaction

源码：`session/pending_interaction.rs`

种类：

- Permission
- Question
- PlanApproval

使用 `tool_call_id` 作为稳定键，RAII Guard 注册和移除，Drop 时广播 resolved，重复移除无动作，因此 first-answer-wins。

重要限制：

- Permission 和 Question 只在内存中，不持久化。
- Plan Approval 另外拥有 `awaiting_plan_approval` 持久 Gate。
- Pending Interaction 通知 fire-and-forget，不写更新日志。

对于本地 Agent 可以接受；对多用户 SaaS 不足。EVERYDAYAIONE 最终应让所有需要等待的交互拥有数据库状态。

### 2.9 Idle 与卸载

统一 Idle 条件：

```text
running_task is None
AND pending_inputs is empty
AND notifications_suppressed is false
```

Leader 判断 Session 是否仍有工作时分三层：

1. 同步检查 `current_prompt_id`，锁损坏时保守视为 Busy。
2. 检查持久 Plan Approval 对应的 Parked Interaction。
3. 通过 Actor Mailbox 发 `IsBusy`，超时 500ms 时保守视为 Busy。

源码注释明确承认 idle-unload 存在 check-then-act 竞态，后续计划用 Actor 内部原子 `Unload-if-idle` 命令解决。这属于 Grok 已知限制，不应复制。

其他已核验参数：

- Idle Notification 默认延迟：60 秒。
- 环境变量 `GROK_IDLE_NOTIFICATION_DELAY_MS` 可覆盖，单位毫秒。
- Session 空闲 600 秒后，下个 Turn 刷新模型元数据。
- Recap 最小空闲：3 分钟。
- 测试和部分默认场景中的 inference idle timeout：300 秒；真实值可由模型和远程配置解析，不能视为全局固定值。

## 3. EVERYDAYAIONE 持久 Actor

### 3.1 数据库状态

迁移：

- `120_turn_revision_foundation.sql`
- `121_conversation_actor_queue.sql`
- `122_conversation_actor_terminal.sql`
- `123_conversation_actor_progress.sql`
- `124_conversation_delivery_outbox.sql`

核心字段：

```text
tasks
├── queue_sequence
├── execution_mode: serial | branch
├── execution_token
├── lease_expires_at
├── execution_attempt
├── base_context_revision
├── context_through_message_id
├── accumulated_content
└── accumulated_blocks

conversations
├── active_serial_task_id
├── context_revision
├── last_closed_message_id
└── actor_updated_at
```

### 3.2 Enqueue 与 Claim

`enqueue_generation_turn` 原子创建 Pending Chat Turn，但不在入队时冻结 Context Revision。

Serial Claim：

1. `FOR UPDATE` 锁 Conversation。
2. 检查 `active_serial_task_id`。
3. 有未过期 Owner 时返回 `busy`。
4. 过期 Owner 未超过尝试上限则回 Pending；达到上限则 Failed。
5. 按 `queue_sequence, id` 获取最早 Pending Task。
6. 使用 `FOR UPDATE SKIP LOCKED` 防止多 Worker 重复认领。
7. 生成 UUID execution token。
8. 状态改为 Running，增加 Attempt。
9. 在 Claim 时绑定最新 Context Revision 和 Last Closed Message。
10. Conversation 写入 Active Serial Owner。

Branch Claim 按精确 `task_id` 认领，不占用 Conversation Serial Owner。

### 3.3 参数

`ConversationExecutionService` 默认：

| 参数 | 默认 | 约束/实际装配 |
|---|---:|---|
| `lease_seconds` | 90 秒 | 15–300 秒 |
| `renew_interval_seconds` | 30 秒 | 大于 0 且小于 Lease；Runtime 实际覆盖为 5 秒 |
| `max_renew_failures` | 2 | 至少 1 |
| `max_attempts` | 3 | 至少 1 |

`ConversationWorker` 默认：

| 参数 | 默认 |
|---|---:|
| DB 扫描间隔 | 2 秒 |
| 进程内并发 | 5 |
| 扫描批量 | 100 |
| 关闭等待 | 10 秒 |

Actor Stream Progress：

- 每 20 个 Text Chunk 持久化一次。
- 每个结构化 Block 立即持久化。
- Flush 时持久化并发送 stream_end。
- Thinking 当前实时推送，但不在 `_persist()` 参数中单独持久化。

### 3.4 Lease 和 Fencing

每次执行持有 UUID `execution_token`。以下写操作都检查当前 Token：

- Renew Lease。
- 更新临时进度。
- Commit。
- Fail。

Commit 还要求：

- Task 为 Running。
- Lease 未过期。
- Output Message 与 Conversation、Org、Input、Turn 完全匹配。
- Content 为 JSON Array。
- Usage 为 JSON Object。
- Credits 非负。
- Tool Digest 为空或 JSON Object。

成功事务内完成：

```text
扣积分
→ 更新 Assistant Message
→ 关闭 Turn / 增加 Revision
→ 更新 Task 终态
→ 释放 Active Serial Owner
```

终态外部投递由 Observer best-effort 执行，投递失败不能回滚数据库终态。企微可靠投递另有事务 Outbox。

### 3.5 Ownership Lost

Lease Renewal 连续失败达到 2 次，或 RPC 返回 `ownership_lost/terminal`：

```text
设置 ownership_lost Event
→ 取消本地 Executor
→ 不 Commit
→ 不 Fail
```

进程 Shutdown 导致 `CancelledError` 时同样不写假失败终态，等待 Lease 到期后由新 Worker 接管。

Commit 请求发出后连接丢失时，当前代码不调用 Fail，避免把“可能已经 Commit 成功”覆盖成失败。这是正确的未知结果处理。

### 3.6 用户取消

`cancel_generation_turn`：

1. 锁 Conversation 和 Task。
2. 校验 User、Org、Conversation、Actor Task 范围。
3. Pending/Running 才允许取消。
4. Task 改为 Cancelled。
5. 清空 Execution Token 和 Lease，立即使旧 Owner 失效。
6. Assistant Message 改为 Interrupted。
7. 释放 Active Serial Owner。

旧执行者后续 Progress/Commit 都会因为 Token 不匹配或 Terminal 被拒绝。

中断锚点另外写入 `interrupt_marker`，让后续 Context 知道上一轮被用户中断。

### 3.7 当前 Steer

`WebSocketManager` 使用进程内：

```text
_steer_signals: Dict[task_id, asyncio.Event]
_steer_messages: Dict[task_id, str]
```

ToolLoop 在工具结果后处理阶段调用 `check_steer()`，命中后把新消息追加为 User Message。

源码显示 WS Route 与独立 Conversation Actor Worker 分处不同进程，而 steer 状态没有数据库或 Redis 传递证据。因此：

> Actor 链路的 steer 跨进程贯通当前不能判定为成立，应视为待 E2E 验证的高风险断层。

本阶段不修复。

### 3.8 Pending Interaction 现状

历史迁移 `078_pending_interaction.sql` 创建表，迁移 `112_drop_pending_interaction.sql` 删除表。当前 `backend/main.py::lifespan` 仍尝试更新该表清理过期记录，但异常被捕获并以 Debug 降级。

这表示等待用户输入能力曾存在，但当前数据库事实源已经退役，启动代码仍有过期引用。它属于既有代码异味，后续 Policy/Ask User 专项决定新协议后再处理。

## 4. 关键行为对照

| 行为 | Grok | EVERYDAYAIONE | 判断 |
|---|---|---|---|
| Session 所有权 | 专用 Thread + Mailbox | DB Claim + Lease + Fencing | 保留本项目 |
| Prompt 权威队列 | Session 内 FIFO，支持编辑/删除/send-now | DB Task FIFO，主要支持新建/取消 | 引入 Grok 行为 |
| 多进程恢复 | 本地日志 + Session Reload | DB 自动接管 | 本项目更强 |
| 当前输入持久屏障 | `persist_ack` | Enqueue RPC 原子写入 | 语义融合 |
| 串行执行 | 队首 Running Prompt | Conversation Active Owner | 等价目标，不同实现 |
| Branch | 子 Agent/独立 Session | execution_mode=branch | 后续统一定义 |
| 取消后保留队列 | 明确保证 | DB 后续 Pending Task 保留 | 基本具备 |
| send-now | 完整优先级和取消语义 | steer 与 cancel 分散 | Grok 更完整 |
| Pending Interaction | 大部分内存，Plan Gate 持久 | 旧表已删除 | 双方都不足于目标 SaaS |
| 临时进度 | 更新日志/事件 | Fenced DB Snapshot + WS | 本项目更可靠 |
| 终态 | Session Completion | DB 原子消息/积分/Revision | 本项目更强 |
| Rewind | Conversation + 文件 Checkpoint | 消息 Revision/Regenerate，非同等能力 | 后续专项 |
| Idle Unload | 有已知 check-then-act 竞态 | Worker 无常驻 Session | 不复制 |

## 5. 候选目标语义

以下只作为总体设计输入，不是已确认接口：

```text
Session Runtime
├── Durable Command Inbox
├── Current Run Ownership
├── Ordered User Inputs
├── Synthetic Inputs
├── Pending Interactions
├── Cancellation / Steering
├── Progress Checkpoint
└── Terminal Commit
```

目标行为：

1. 用户新输入必须明确选择 Queue、Send Now、Steer Current 或 Replace Goal。
2. 所有输入拥有稳定 ID 和持久状态。
3. 用户输入优先于 Synthetic Continuation，但不能误删正在运行的输入。
4. 等待权限、提问和审批全部持久化。
5. API/企微/定时任务使用同一命令协议。
6. 实时事件丢失不影响数据库恢复。
7. 未持有 Fencing Token 的执行者不能写进度、扣费或提交终态。

## 6. 边界场景

| 场景 | 当前最好实现 | 目标要求 |
|---|---|---|
| 同会话连续发送 | Grok 权威队列更完整 | 持久 FIFO + 明确优先级 |
| 执行中追加要求 | Grok send-now | 建立跨进程持久 Steer |
| Cancel 与 Commit 竞态 | 本项目原子 RPC | 保留 |
| Worker 强杀 | 本项目 Lease 接管 | 保留 |
| DB 短暂不可用 | 本项目停止写入并失去 Owner | 增加明确运行状态和告警 |
| Permission 等待时重启 | 双方都不完整 | Pending Interaction 必须持久 |
| 删除排队输入 | Grok RemovedFromQueue | 引入未开始语义，禁止误发 Turn Complete |
| Synthetic 与用户输入竞争 | Grok 用户优先 | 引入 |
| 租约续期响应丢失 | 本项目两次失败后停止 | 保守停止并可观察 |
| WS 丢失 | DB 继续 | 保留 |
| 多客户端同时编辑队列 | Grok 有 Queue Meta/Version | 研究是否适合 Web/企微 |

## 7. 已核验测试

| 项目 | 已核验测试范围 |
|---|---|
| Grok | Prompt Queue Actor、Cancel Running Task、Idle Resume/Unload、Pending Interaction RAII/poisoned lock、Leader Reconnect 与多 Session Replay |
| 本项目 Execution | Claim/Busy/Branch、Commit/Fail/Ownership Lost、Renew 连续失败、Shutdown 不写假终态、Cancel-Commit 竞态、Commit 响应丢失 |
| 本项目 Worker | Serial 去重、Branch、并发上限、DB 扫描降级、Redis Wakeup 竞态 |
| 本项目迁移 | Queue、Terminal、Progress、Delivery Outbox 契约 |
| 本项目 Interrupt | 当前主要验证单进程 Steer 数据结构和 Context 恢复，尚非跨进程 E2E |

## 8. 风险与待核验项

| 风险 | 等级 | 说明 |
|---|---|---|
| Actor Steer 可能不跨进程 | 高 | 需要真实 API + Actor Worker E2E |
| Pending Interaction 表已删除但启动仍引用 | 中 | 过期代码被异常降级隐藏 |
| Session 命令分散 | 中 | DB RPC、WS、Handler 各自表达状态变化 |
| Thinking 未进入 Actor Progress Snapshot | 中 | 刷新后过程思考恢复语义待核验 |
| Actor Worker 关闭只等待 10 秒 | 中 | 强制取消后依赖 Lease 恢复，需部署时序验证 |
| Grok Idle Unload 有已知竞态 | 不适用 | 不应移植 |
| Grok Permission/Question 不持久 | 不适用 | 目标实现必须超过 Grok |

## 9. 本板块完成标准

- [x] Session 构造和命令面已定位。
- [x] Prompt FIFO、send-now 和取消行为已还原。
- [x] 本项目 Claim/Lease/Fencing/Commit 参数已记录。
- [x] 双方持久化和恢复边界已比较。
- [x] 已记录 Pending Interaction 和 Steer 风险。
- [x] 已形成候选目标语义。
- [ ] Actor Steer 跨进程 E2E。
- [ ] 真实数据库并发 Claim/Cancel/Commit 故障注入。
- [ ] Thinking Progress 刷新恢复验证。

后三项进入最终实施前的验证矩阵，本轮不以纯文字宣称通过。
