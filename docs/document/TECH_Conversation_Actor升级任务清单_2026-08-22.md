# Conversation Actor 升级任务清单

> 这是 2026-08-22 新建的执行清单，保留原有任务树和方案文档，不覆盖历史记录。
>
> 总状态：TODO-00 至 TODO-09 已按生产 checkpoint/RPC 契约完成本地与隔离 PostgreSQL 验证；TODO-10 仅剩完整发布和线上验收，当前仍禁止最终生产部署。

## 状态约定

- `[x]` 已完成并有本地验证证据。
- `[~]` 代码已有一部分，但缺少关键实现或真实验收，不能视为完成。
- `[ ]` 未完成。
- 每项完成后必须填写“实现位置”和“验证结果”，再开始下一项。

## 已完成

### TODO-00：生产 Conversation Actor 契约复核与应用层适配 `[x]`

生产只读核对确认：生产数据库已经存在 `conversation_turn_checkpoints`、
`pause_generation_turn_owned`、`cancel_generation_turn_owned`、
`resume_paused_generation_turn`、`save_generation_checkpoint` 和
`load_generation_checkpoint`；不存在本地新增的
`conversation_replay_checkpoints`、`control_generation_turn`、
`resume_generation_turn`、`write_replay_checkpoint`、
`read_latest_replay_checkpoint`。因此应用层必须复用生产契约，不能直接部署本地
235-238 的旧迁移路径。

实现位置：

- `backend/services/conversation_task.py`
- `backend/services/conversation_execution.py`
- `backend/services/replay_checkpoint_store.py`
- `backend/services/handlers/chat/executor.py`
- `backend/api/routes/task.py`

实现结果：

- CANCEL 用户入口复用 `cancel_generation_turn`，安全点收尾使用
  `cancel_generation_turn_owned`。
- PAUSE 用户入口写入 `pause` 控制事件，安全点收尾使用
  `pause_generation_turn_owned`。
- RESUME 复用 `resume_paused_generation_turn`，兼容生产返回的
  `enqueued/already_enqueued`。
- ReplayCheckpoint 读写改为 `save_generation_checkpoint` /
  `load_generation_checkpoint`，恢复读取使用当前 execution token。

验证结果：相关后端定向测试 34 项通过；生产只读 RPC/表结构核对通过。

注意：本项只完成应用层适配；本地兼容迁移、回滚和隔离 PostgreSQL 重验归入
TODO-01，不代表已部署。

### DONE-01：统一命令和 Runtime 状态骨架 `[x]`

范围：

- `CANCEL`、`PAUSE`、`RESUME` 命令类型。
- `PAUSING`、`PAUSED`、`RESUMING` Runtime 状态。
- Runtime 只在安全点处理控制命令。
- 保留 PostgreSQL claim、lease、fencing 和现有 Runtime，不新增长期 OS Thread。

实现位置：

- `backend/services/conversation_commands.py`
- `backend/services/conversation_state.py`
- `backend/services/conversation_turn_runtime.py`

验证结果：Command/State/Runtime 定向测试通过。

### DONE-02：暂停/取消安全点快照 `[x]`

范围：

```text
用户请求
  -> 写入 pause/cancel 控制事件
  -> Runtime 到安全点
  -> flush accumulated_content / accumulated_blocks
  -> messages 写入 interrupted 快照
  -> tasks 原子进入 paused/cancelled
```

实现位置：

- `backend/migrations/239_conversation_actor_production_contract_compat.sql`
- `backend/services/conversation_execution.py`
- `backend/services/handlers/chat/actor_sink.py`
- `backend/services/handlers/chat/execution_engine.py`
- `backend/api/routes/task.py`

验证结果：迁移契约、Runtime、执行服务和 Actor sink 定向测试通过。

### DONE-03：前端暂停入口和旧 chunk 闸门 `[x]`

范围：

- 生成中显示暂停入口。
- 暂停时立即关闭前端显示闸门，但不提前覆盖数据库快照。
- `user_pause` 中断标记可刷新后恢复展示。

实现位置：

- `frontend/src/components/chat/input/InputControls.tsx`
- `frontend/src/components/chat/input/useInputTaskControls.ts`
- `frontend/src/services/message.ts`
- `frontend/src/components/chat/message/MessageContentBlocks.tsx`

验证结果：前端定向测试 18 项通过，生产构建通过。

### DONE-04：本地回归验证 `[x]`

验证结果：

- 后端相关定向测试：49 项通过。
- 前端相关定向测试：18 项通过。
- 前端 `npm run build`：通过。
- `git diff --check`：通过。

## 剩余任务（2 项）

### TODO-01：完成 PostgreSQL 迁移和 RPC 集成验证 `[x]`

> 重新核对生产后，本节原先基于 235 草稿迁移的验证记录全部降级为历史记录，
> 不能作为当前验收证据。旧 235-238 文件已从源码移除，必须改用 239 兼容迁移
> 在隔离 PostgreSQL 重新执行。该重验现已完成。

当前验证结果：239 兼容迁移真实加载成功；PAUSE 快照、PAUSE 后 CANCEL、
RESUME、新 execution token、旧 token fencing 共 4 项隔离 PostgreSQL 集成测试通过。

目标：确认 235 迁移在真实数据库中可执行，并验证以下原子性：

- running 任务请求暂停只写入控制事件，不提前改任务进度。
- Runtime 带正确 execution token 收尾。
- `messages` 在 `tasks` 终态更新之前完成写入。
- 旧 token 返回 `ownership_lost`，不能覆盖新 owner。
- pending 任务可直接生成暂停/取消快照。

完成内容：

- 已修正 `paused + cancel` 必须进入最终取消的 RPC 分支。
- 已修正旧文本消息内容的安全 JSON 解析。
- 已增加隔离数据库集成测试：`backend/tests/test_conversation_actor_pause_snapshot_integration.py`。
- 已增加迁移/回滚契约断言。
- 使用本机临时 PostgreSQL 实例真实执行 235 迁移。
- PAUSE 请求延迟、Runtime owner 快照、旧 token 拒绝、暂停后 CANCEL：2 项集成测试通过。
- 在无暂停数据的临时数据库中真实执行 235 回滚脚本：通过。

验收：真实 PostgreSQL RPC 集成测试通过；迁移和回滚脚本均可执行。

### TODO-02：按生产表建立 ReplayCheckpoint 兼容模型 `[x]`

> 原先基于 `conversation_replay_checkpoints` 的验证记录已失效；生产实际使用
> `conversation_turn_checkpoints`，现已按 239 迁移重新验证。

目标：把“前端显示进度”和“模型恢复依据”彻底分开。

至少支持：

- `BEFORE_MODEL`
- `AFTER_TOOL`
- `BEFORE_COMMIT`

Checkpoint 必须带：`task_id`、`turn_id`、`execution_token`、序号、状态、上下文快照或引用、时间和参数版本。

约束：模型中途 partial 只能进入 DeliveryProgress，不能直接作为模型恢复上下文。

实现位置：

- `backend/migrations/239_conversation_actor_production_contract_compat.sql`
- `backend/migrations/rollback/239_conversation_actor_production_contract_compat_rollback.sql`
- `backend/services/replay_checkpoint_store.py`

验证结果：

- 迁移契约、回滚保护和 Store 测试通过。
- 本机临时 PostgreSQL 真实执行 236 迁移。
- ReplayCheckpoint 写入、重复写入幂等、边界读取、fencing 和 paused 读取集成测试通过。
- 空数据回滚脚本真实执行通过。

验收：迁移、读写 RPC、去重和旧版本兼容测试通过。

### TODO-03：按生产 RPC 接入安全点 Checkpoint 写入和读取 `[x]`

目标：在现有 Runtime 中接入 ReplayCheckpoint，而不是另起一个 Runtime。

- `BEFORE_MODEL`：记录本轮模型输入基线。
- `AFTER_TOOL`：工具成功结果或已确认的 uncertain 状态进入可恢复边界。
- `BEFORE_COMMIT`：记录提交前的完整可重放上下文。
- Worker 崩溃后只能从最近合法边界恢复。

实现位置：

- `backend/services/conversation_turn_runtime.py`
- `backend/services/handlers/chat/execution_engine.py`
- `backend/services/handlers/chat/executor.py`
- `backend/services/conversation_runtime.py`

验证结果：

- `MODEL_CHUNK` 不触发 ReplayCheckpoint 写入。
- `BEFORE_MODEL`、`AFTER_TOOL`、`BEFORE_COMMIT` 均接入安全点回调。
- 暂停时先写 ReplayCheckpoint，再刷 DeliveryProgress。
- 定向测试 43 项通过。

本项验收：安全点写入顺序正确，`MODEL_CHUNK` 不写库，暂停不会丢失安全点前的可重放上下文。实际恢复读取和防重复拼接由 TODO-04 RESUME 继续验收。

### TODO-04：按生产 RPC 实现 RESUME 完整链路 `[x]`

> 原先 `resume_generation_turn` 的验证记录已失效，当前契约是
> `resume_paused_generation_turn`，现已按 239 迁移重新验证。

目标：实现真正的“从最近 ReplayCheckpoint 新开执行尝试”，而不是重新读取 interrupted history 盲目生成。

链路：

```text
用户点击继续
  -> 校验 task=paused
  -> 原子创建新的 execution attempt
  -> claim 新 execution token
  -> 读取最近 ReplayCheckpoint
  -> Runtime 从 checkpoint 恢复
  -> 新 turn 继续模型/工具循环
```

需要覆盖：后端 RPC、API、前端继续入口、消息展示和重复点击幂等。

实现位置：

- `backend/migrations/239_conversation_actor_production_contract_compat.sql`
- `backend/migrations/rollback/239_conversation_actor_production_contract_compat_rollback.sql`
- `backend/services/conversation_task.py`
- `backend/api/routes/task.py`
- `backend/services/handlers/chat/executor.py`
- `backend/services/handlers/chat/stream_setup.py`
- `frontend/src/services/message.ts`
- `frontend/src/components/chat/message/MessageActions.tsx`
- `frontend/src/components/chat/message/MessageArea.tsx`

实现结果：

- `paused -> pending` 由生产 `resume_paused_generation_turn` 原子完成，旧 token/lease 清除；Worker 后续重新 claim 时签发新 token。
- 执行器消费最近 ReplayCheckpoint，不再把 interrupted history 当恢复上下文。
- ReplayCheckpoint 的 `content_blocks` 会进入新执行结果，旧 partial 不会被再次拼接。
- `BEFORE_COMMIT` checkpoint 带有可直接提交的结果，提交前崩溃不会再次调用模型。
- RESUME 会清理旧 WebSocket cancel gate、发布 Actor 唤醒，并通过 task/message 去重。
- 仅 `user_pause` 标记显示“继续”；`CANCEL` 仍然是最终状态。

验证结果：

- RESUME 迁移契约、Store、Executor 定向测试通过。
- 迁移 235、236、237 在隔离 PostgreSQL 真实加载通过。
- ReplayCheckpoint + RESUME PostgreSQL 集成测试：2 项通过。
- 237 空数据回滚脚本真实执行通过。
- 前端 TypeScript/Vite 构建通过。

验收：继续后上下文正确、旧 partial 不重复、旧 token 不能写入新结果；线上手工验证留到 TODO-10。

### TODO-05：完成 `stream_end` / `message_done` 终态语义 `[x]`

目标：固定两个事件的边界：

- `stream_end`：只表示模型流结束。
- `message_done`：只在数据库 commit 成功后发送。

需要审计 Actor、非 Actor、工具循环和前端 WebSocket 处理，确保暂停/取消不发送错误的完成事件。

实现位置：

- `backend/services/conversation_delivery.py`
- `frontend/src/contexts/wsMessageHandlers.ts`
- `frontend/src/contexts/__tests__/wsMessageHandlers.test.ts`

实现结果：

- `stream_end` 只刷完 chunk buffer 并记录“模型流结束”，不再把消息直接标记 completed。
- `message_done` 继续由数据库完成消息负责最终状态更新和清理 streaming 状态。
- Actor `paused` 终态只释放资源，不发送 `message_error` 或 `message_done`。
- 暂停/取消后的迟到 `stream_end` 不会覆盖 interrupted/paused 状态。
- 旧链路仍保持 `stream_end -> 持久化 -> message_done` 的顺序。

验证结果：

- Actor delivery 定向测试 9 项通过。
- WebSocket handler 定向测试 60 项通过。

验收：断线、刷新、重复 `message_done` 和暂停后旧 `stream_end` 都不会把 interrupted/paused 显示成 completed；跨进程迟到 chunk 闸门由 TODO-06 继续覆盖。

### TODO-06：完成 Redis/跨进程旧 chunk 闸门 `[x]`

目标：Redis 只做唤醒，不能绕过数据库控制状态和 fencing 闸门。

- 暂停/取消后迟到 chunk 必须被丢弃。
- 新 execution token 的消息不能被旧 token 覆盖。
- 进程 A 请求暂停、进程 B 执行时仍然成立。
- 闸门清理不能导致旧消息重新显示。

实现位置：

- `backend/services/cancel_gate.py`
- `backend/services/websocket_manager.py`
- `backend/services/websocket_redis.py`
- `backend/tests/test_cancelled_gate.py`
- `backend/tests/test_websocket_redis.py`

实现结果：

- 取消入口同步写入本地闸门，避免取消请求与旧 chunk 之间出现事件循环窗口。
- `send_to_task_or_user` 在本地投递前、投递中和 Redis 发布前均检查闸门。
- user 维度 Redis 广播携带 `task_id`，远端 Worker 可以按 `(org_id, task_id)` 再次校验。
- Redis 入站 delivery 再过一次闸门；task channel 从 `target_id` 推导 task_id。
- RESUME 仍由显式 API 清理闸门；租户复合 key 保持隔离。

验证结果：

- 取消闸门、Redis 入站/发布、组织隔离、Actor delivery 定向测试：41 项通过。
- Redis 断线重连行为仍由现有 listener 重连测试覆盖；生产环境双 Worker 拓扑手工演练归入 TODO-10。

验收：本地与跨进程迟到 chunk 的闸门逻辑已完成；生产双 Worker 真实拓扑演练继续在 TODO-10 完成。

### TODO-07：完成工具 uncertain / 结果回放验收 `[x]`

现有工具幂等登记代码已经存在，但还需要按 Actor 恢复链路验证：

- running 长时间未完成转为 `uncertain`。
- uncertain 不允许盲重试外部副作用。
- 已成功的 invocation 按 invocation ID 和参数 hash 回放。
- 只读工具和有副作用工具采用不同重试策略。

实现位置：

- `backend/migrations/239_conversation_actor_production_contract_compat.sql`
- `backend/migrations/rollback/239_conversation_actor_production_contract_compat_rollback.sql`
- `backend/services/tool_invocation_store.py`
- `backend/services/handlers/chat_tool_mixin.py`
- `backend/tests/test_tool_invocation_store.py`
- `backend/tests/test_tool_invocation_uncertain_migration.py`
- `backend/tests/test_tool_invocation_uncertain_integration.py`

实现结果：

- Actor 恢复前先按 task/turn/tool_call、当前 execution token 检查旧 `running` 调用。
- 超过 900 秒仍未完成的调用原子转为 `uncertain`，返回未知结果并禁止自动重试。
- 当前 owner、参数 hash 和 invocation identity 仍由原有 RPC 校验；旧 token 不能完成调用。
- 已成功调用由 `begin_tool_invocation` 返回 replay，结果通过安全序列化/反序列化回放。
- 只读工具不进入副作用 ledger，按普通错误路径处理；可能产生副作用的工具进入 ledger。

验证结果：

- 工具 Store、ChatToolMixin、迁移契约定向测试：39 项通过。
- 隔离 PostgreSQL 真实加载 139、239 迁移，并验证 stale `running -> uncertain`、旧 token fencing、uncertain 禁止重试和 succeeded replay：通过。

验收：模拟 Worker 在外部调用返回前崩溃，恢复后不会重复执行副作用；真实生产工具供应商的回查流程保留为 TODO-10 线上验收。

### TODO-08：完成子任务父 Runtime 回注入验收 `[x]`

目标：确认真实业务子 Agent 而不只是协议层满足：

```text
父任务创建子任务
  -> 父任务等待
  -> 子任务完成
  -> 写入 SUBTASK_COMPLETED
  -> 父 Runtime 安全点消费
  -> 父模型继续
```

已完成实现：

- `backend/services/conversation_turn_runtime.py`
  - 安全点归约 `SUBTASK_COMPLETED` payload。
  - 以控制事件 ID 去重，并提供一次性 `consume_subtask_completions()`。
  - 校验 child_task_id、终态和 result 对象，拒绝非法回传。
- `backend/services/handlers/chat/execution_engine.py`
  - 在下一次 `BEFORE_MODEL` 安全点后，把子任务结果作为受控 system 事件注入模型上下文。
  - 不伪造不存在的 tool_call/tool_result 配对。
- `backend/migrations/140_conversation_subtasks.sql`
  - 父子租户/fencing 校验、子任务终态触发 `SUBTASK_COMPLETED`、唯一 dedupe 和父任务终态 ignored 逻辑已验证。

验证结果：

- Runtime、命令 Store、上下文回注入定向测试：21 项通过。
- Actor 执行/Worker/Runtime/Delivery 回归测试：49 项通过。
- 隔离 PostgreSQL 真实验证父子注册、完成触发、事件去重和父任务终态行为：通过。

架构边界确认：现有 `ERPAgent` 及其部门 Agent 是当前 Worker 内的短生命周期业务子 Agent，设计上不自动改造成独立长期 Conversation Actor 任务；异步父子任务协议独立服务于需要跨 Worker/跨进程等待的任务生产者。真实多 Worker 竞态、父 lease 过期和子任务迟到场景统一在 TODO-09 验证。

验收结论：父任务创建/登记、父任务等待、子任务终态触发 `SUBTASK_COMPLETED`、父 Runtime 安全点消费、结果回注入和去重保护已完成。

### TODO-09：完成崩溃、租约和并发验证 `[x]`

必须逐项验证：

1. Worker 在模型流中崩溃。
2. Worker 在工具返回前崩溃。
3. Worker 在 `BEFORE_COMMIT` 崩溃。
4. lease 过期后新 Worker claim。
5. 旧 execution token 尝试写进度/终态。
6. 两个 Worker 同时处理同一 Conversation。
7. pause、cancel、resume 与工具/子任务完成竞态。

实现/验证位置：

- `backend/tests/test_conversation_actor_recovery_matrix.py`
- `backend/tests/test_conversation_actor_recovery_integration.py`
- `backend/tests/test_conversation_execution.py`
- `backend/tests/test_conversation_worker.py`
- `backend/tests/test_chat_generation_executor.py`
- `backend/tests/test_conversation_actor_pause_snapshot_integration.py`
- `backend/tests/test_conversation_actor_resume_integration.py`
- `backend/tests/test_replay_checkpoint_integration.py`
- `backend/tests/test_conversation_subtasks.py`
- `backend/tests/test_websocket_redis.py`

验证结果：

- Actor 迁移、Runtime、Worker、执行权、Replay、工具、子任务、Redis 闸门回归矩阵：133 项通过。
- 双 Worker 同一 serial Conversation：单元模型和两个独立 PostgreSQL 连接均验证只允许一个执行者进入执行阶段。
- 模型流中断、lease 续租失败、外部 shutdown、提交竞态、旧 token fencing：定向测试通过。
- 隔离 PostgreSQL 验证 lease 过期重领、新 token、旧 token 的 renew/commit `ownership_lost`：通过。
- 两个独立 PostgreSQL 连接同时 claim 同一 serial Conversation：一方 `claimed`，另一方 `busy`：通过。
- 隔离 PostgreSQL 验证 pause/cancel/resume、ReplayCheckpoint、工具 uncertain、子任务终态触发：通过。

验收：租约过期重领、旧 token fencing、双 Worker、pause/cancel/resume、工具和子任务竞态均有状态/事件断言；真实生产进程级崩溃和跨进程 Redis/生产拓扑手工验证属于 TODO-10 上线验收，不改变数据库恢复协议。

### TODO-10：完整部署和线上验收 `[ ]`

只有 TODO-01 至 TODO-09 全部完成后执行：

1. 运行全量相关测试。
2. 备份并应用数据库迁移。
3. 构建前端和后端发布包。
4. 完整部署，不做灰度。
5. 线上手工验证暂停、刷新、继续、取消、工具和子任务。
6. 失败时只回滚应用版本，并保留已写入的控制事实和 checkpoint 数据。

## 当前指针

```text
当前已完成：DONE-01 ~ DONE-04、TODO-00
当前已完成：TODO-00 ~ TODO-09
当前待完成：TODO-10 完整部署和线上验收
部署状态：禁止最终部署
```

## 执行规则

- 一次只推进一个 TODO 项。
- 每项完成后更新本文件的状态、实现位置和验证结果。
- 不删除或覆盖原有任务树、方案文档和用户已有修改。
- 发现需要扩大范围时先停在当前任务，不把新问题混入当前提交。
- 任何生产部署必须等 TODO-10 到达，并按发布技能执行完整发布和回滚准备。
