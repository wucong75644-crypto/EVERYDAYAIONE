# Conversation Actor 命令运行时升级

> 状态：代码实现完成，待生产 PostgreSQL 并发演练验收（阶段 1、阶段 2A/2B、阶段 3、阶段 4 协议与取消快照已完成）
> 日期：2026-08-18
> 基础设计：`TECH_Conversation_Actor持久执行架构.md`
> 实施验收：`TECH_Conversation_Actor实施与验收附录.md`

## 1. 决策与目标

保留 PostgreSQL Conversation Actor 作为执行权、持久状态、fencing 和崩溃恢复机制；不为每个 Conversation 创建长期 OS Thread，也不引入 `!Send` SessionActor。

在现有 claim/lease/commit 链路上增加一个逻辑运行时层：

```text
持久任务/控制状态
    -> ConversationCommand
    -> ConversationTurnRuntime
    -> SafePoint 状态转换
    -> 模型、工具、子任务
    -> fencing 保护的原子提交
```

目标：

1. 用户消息、取消、工具完成、审批结果和子任务完成拥有统一的运行时入口。
2. 关键状态转换只能在明确的安全点执行。
3. 取消优先于迟到的工具/子任务结果；丢失执行权优先于所有普通命令。
4. Worker 崩溃、Redis 丢消息或重复回调不会破坏 PostgreSQL 的所有权和终态语义。
5. 有副作用的工具在重试时不会因为租约恢复而重复执行。

非目标：

- 不替换 `ConversationWorker`、PostgreSQL claim/lease/fencing 或 `ChatGenerationExecutor`。
- 不把每个 token 写成 Command 或持久事件。
- 不为第一阶段新增外部队列、中间件或第三方依赖。
- 不改变现有 WebSocket、企微和 `/messages/generate` 的外部协议。

## 2. 当前基础与缺口

现有基础已经具备：

- `ConversationWorker` 从数据库扫描任务，并通过 Redis 做 best-effort 唤醒。
- `ConversationExecutionService` 负责 claim、租约续期、本地执行取消以及原子 commit/fail。
- `execute_chat` 负责模型流、工具循环、上下文压缩和结果聚合。
- `ActorWebSink` 使用 execution token 持久化过程进度。
- PostgreSQL RPC 负责任务范围校验、积分、Turn revision 和 fencing。

本轮审计前的缺口已按以下方式收敛：

- 取消现在先写入持久 `CANCEL` Command，由当前 fencing owner 在安全点处理；不再由 API 直接抢写 Actor 终态。
- 取消安全点先强制保存 Actor 的 `accumulated_content/accumulated_blocks`，再由带 execution token 的 RPC 将快照投影到 `messages.status='interrupted'`。
- Runtime 的命令副作用在事件确认前执行；子任务完成结果在下一次模型安全点注入当前上下文。
- 前端收到 `TASK_CANCELLED` 时保留 partial 内容并显示中断态，不再覆盖成失败文本。
- 外部副作用工具使用持久调用记录；`uncertain` 仍然禁止盲重试。

## 3. 目标职责

### 3.1 PostgreSQL

PostgreSQL 是唯一的持久事实来源，负责：

- pending/running/terminal 任务状态；
- serial owner、lease 和 fencing token；
- ContextSnapshot 基线；
- 消息、积分、Turn revision 和任务终态的原子提交；
- 跨进程控制事件和子任务完成记录（第二阶段起）。

### 3.2 ConversationTurnRuntime

每次成功 claim 创建一个短生命周期 Runtime，负责当前执行尝试：

- 保存当前 `ConversationState`；
- 接收并去重 Command；
- 在 SafePoint 调用 reducer；
- 决定继续模型、执行工具、等待子任务、取消或提交；
- 不拥有数据库执行权，不绕过 RPC 改写终态。

### 3.3 ConversationCommand

第一阶段定义不可变的逻辑命令：

```text
USER_TURN
CANCEL
TOOL_COMPLETED
APPROVAL_RESULT
SUBTASK_COMPLETED
LEASE_LOST
SHUTDOWN
```

同步工具结果可以先使用当前 Runtime 内存 Inbox；跨进程或跨生命周期事件必须在数据库落盘后再唤醒 Runtime。

### 3.4 SafePoint

统一安全点：

```text
BEFORE_MODEL
MODEL_CHUNK
AFTER_MODEL
BEFORE_TOOL
AFTER_TOOL
BEFORE_SUBTASK_WAIT
AFTER_SUBTASK_COMPLETE
BEFORE_COMMIT
```

优先级：

```text
LEASE_LOST > CANCEL > APPROVAL/SUBTASK > TOOL_COMPLETED > CONTINUE
```

执行权丢失是硬停止信号；普通 Command 只在安全点改变执行流程。长时间工具必须支持超时或协作式取消，不能承诺任意时刻强制终止外部副作用。

## 4. Runtime 状态机

Runtime 状态只描述当前执行尝试，不替换现有 `tasks.status`：

```text
CLAIMED
  -> RUNNING_MODEL
  -> WAITING_TOOL
  -> WAITING_APPROVAL
  -> WAITING_SUBTASK
  -> COMMITTING
  -> COMPLETED

任何非终态
  -> CANCELLING
  -> CANCELLED

任何非终态
  -> FAILED / OWNERSHIP_LOST
```

合法转换由 reducer 集中实现。命令处理不得直接修改 `tasks`；需要持久化状态时调用现有或新增的数据库 RPC。

## 5. 数据与恢复设计

### 5.1 第一阶段

第一阶段不新增数据库表。用户消息继续由 `tasks` 持久化，lease 继续由 `renew_generation_lease` 表达；取消和其他跨进程事件由控制事件表持久化。141 迁移新增的是快照物化与带 fencing token 的取消 RPC，不改变 PostgreSQL Actor 的所有权模型。

### 5.2 第二阶段控制事件

跨进程、跨生命周期的事件增加 `conversation_control_events`：

```text
id, conversation_id, task_id, turn_id,
event_type, payload, dedupe_key,
status, created_at, applied_at
```

事件表只保存取消、审批结果、外部工具回调和子任务完成，不保存 token 流。唯一去重键保证同一外部事件重复投递只被应用一次。

### 5.3 工具幂等

具有副作用的工具增加 `tool_invocations` 或等价的持久记录：

```text
task_id, turn_id, tool_call_id,
tool_name, args_hash, status, result
```

同一个逻辑 `tool_call_id` 重试时先读取记录；已完成则复用结果，不再次执行外部写操作。只读工具可以继续按现有策略重试。

### 5.4 子任务

子任务必须带 `parent_task_id`、`parent_conversation_id` 和 `parent_command_id`。子任务只提交自己的终态；子任务完成后写入 `SUBTASK_COMPLETED` 事件并唤醒父 Conversation。父 Runtime 只在安全点消费结果，子任务不得直接修改父 Conversation。

## 6. 代码实施范围

新增：

- `backend/services/conversation_commands.py`：Command、SafePoint、Inbox 和去重协议。
- `backend/services/conversation_state.py`：Runtime 状态和合法转换 reducer。
- `backend/services/conversation_turn_runtime.py`：单次 claim 的执行控制器。
- `backend/services/conversation_command_store.py`：PostgreSQL 控制事件读写与 fencing 确认适配器。
- `backend/services/tool_invocation_store.py`：副作用工具幂等登记、完成记录和安全回放。
- `backend/services/conversation_subtasks.py`：父 Runtime 注册子任务的 fencing 适配器。

修改：

- `backend/services/conversation_execution.py`：创建 Runtime，接入 lease/终态控制信号。
- `backend/services/handlers/chat/execution_engine.py`：暴露模型/工具循环安全点。
- `backend/services/handlers/chat_tool_mixin.py`：统一包装工具完成结果。
- `backend/services/conversation_task.py`：统一取消命令映射。
- `backend/services/handlers/chat/actor_sink.py`：保留 fencing 进度语义，必要时补充事件去重标识。
- `backend/api/routes/ws.py`、`backend/services/websocket_manager.py`：审批响应的任务/会话归属校验、持久化和同进程唤醒。
- `frontend/src/contexts/wsMessageHandlers.ts`、`frontend/src/contexts/WebSocketContext.tsx`：审批响应回传完整任务/会话上下文。

基本不改：

- `backend/services/conversation_worker.py` 的扫描、并发和唤醒职责。
- PostgreSQL 现有 claim、lease、commit/fail/cancel RPC 的所有权语义。
- Web/企微外部消息协议。

## 7. 分阶段实施

### 阶段 1：逻辑 Runtime

- 增加 Command/SafePoint/State 类型。
- 在现有执行链中接入 Runtime 骨架。
- 不改变数据库协议和正常生成结果。
- 验证取消、丢权、工具完成和 commit 前检查。

### 阶段 2：统一外部事件

- 取消入口统一追加持久 `CANCEL` Command；当前 owner 在安全点保存最新进度后调用带 execution token 的取消 RPC，事务内完成消息快照、任务终态和取消事件收敛。
- 已增加控制事件表、fencing owner 读取/确认 RPC 和 Runtime 数据库适配器。
- 审批结果已接入 WebSocket 入口：先校验用户、任务、会话、Actor 标记和 running 状态，再以 `approval:{tool_call_id}` 去重写入 PostgreSQL。
- Actor 执行端同时保留同进程即时唤醒，并在 WebSocket 与 Actor 分进程时轮询当前 fencing token 下的审批事件；旧非 Actor 确认链路保持兼容。
- 外部回调和子任务事件必须在各自入口具备可靠的 task/conversation 关联；无可靠关联时拒绝写入，不能靠 task_id 猜测归属。
- Redis 继续只做唤醒，数据库负责恢复。

### 阶段 3：工具幂等

- Actor 工具调用按 `task_id + turn_id + tool_call_id` 建立持久幂等记录，并校验参数 hash。
- 已增加 `begin_tool_invocation` / `complete_tool_invocation` RPC：成功结果可回放，执行中或外部结果未知时禁止盲目重试。
- Actor 工具执行已接入登记、成功完成和异常 `uncertain` 记录；旧非 Actor 路径保持原逻辑。
- Worker 崩溃与 lease 重试的真实 PostgreSQL 演练仍待本阶段收尾后执行。

### 阶段 4：子任务（协议完成）

- 已增加父子任务关联表和父 owner fencing 注册 RPC。
- 子任务进入 completed/failed/cancelled 终态时，由数据库触发器写入去重的 `SUBTASK_COMPLETED` 事件；父任务已终态时事件直接记为 ignored。
- 父 Runtime 继续只在安全点读取和确认完成事件；具体业务子 Agent 仍不自动改造成长期驻留任务。
- 子任务完成事件在确认前先由 Runtime 应用到下一轮模型上下文；真实 PostgreSQL 父子任务并发/终态演练仍待部署验收阶段执行。

### 阶段 4A：取消快照与恢复语义

- 取消链路：`API -> append CANCEL -> SafePoint -> persist_progress -> cancel_generation_turn_owned`。
- `messages` 保存已生成的文本/内容块和 `interrupt_marker`，后续用户消息按普通新 turn 读取 `interrupted` 历史；这不是 token 级原地续流，而是可恢复的上下文续接。
- WebSocket 只是 best-effort 投递；断线、重复通知或 Redis 丢唤醒不改变 PostgreSQL 事实。

## 8.1 本轮行业标准逐条核对

| 能力 | 当前结论 | 代码/数据库保证 |
|---|---|---|
| 单 Conversation 所有权 | 可实现 | PostgreSQL claim、lease、execution token 和原子 commit/fail |
| 取消与完成竞态 | 可实现 | CANCEL 优先级高于普通完成事件；owner 取消 RPC 校验 token |
| 取消后保留 partial | 可实现 | 141 先物化 `accumulated_*` 到 interrupted message，再结束 task |
| 取消后继续对话 | 可实现 | 新 turn 读取 interrupted history；不承诺 token 级原地续流 |
| Worker 崩溃恢复 | 可实现 | lease 过期后重新 claim；旧 token 不能提交 |
| 重复控制事件 | 可实现 | dedupe key、事件状态和 fencing acknowledge |
| 子任务结果回传 | 协议可实现，业务接入待验收 | DB 触发完成事件，Runtime 安全点注入；当前具体子 Agent 入口仍需真实演练 |
| 任意外部副作用 exactly-once | 不可普遍实现 | 只能保证 invocation id、参数 hash、成功结果回放；`uncertain` 禁止盲重试，第三方必须提供幂等键 |
| WebSocket/Redis 可靠性 | 可实现为 best-effort | DB 是事实源；断线重连靠消息查询/历史恢复，不把 WS 当提交确认 |

### 阶段 5：分阶段部署与收敛

- 每完成一个阶段，直接部署该阶段完整版本并执行验收测试。
- 若验收失败，回滚应用版本；已执行的数据库迁移保留，避免删除已写入的控制事实。
- 对比各阶段版本的终态、revision、积分、审批和投递结果。
- 稳定后删除重复的旧控制分支；不采用并行灰度流量。

## 9. 验收标准

- 同一 Conversation 同时只有一个有效 serial owner。
- 取消与工具完成竞态时，取消优先且不产生新的 commit。
- 旧 lease/token 永远不能覆盖新执行者。
- Redis 丢消息不导致任务永久丢失。
- Worker 在工具完成后、commit 前崩溃时可恢复。
- 副作用工具重试不重复执行。
- 重复的审批、子任务完成和外部回调不会重复推进状态。
- 子任务不能绕过父 Runtime 修改 Conversation。
- 取消后的 `messages` 必须保留 partial content、已完成工具结果和中断标记。
- WebSocket/企微投递失败不影响已提交数据库终态。
- 不把任意第三方副作用宣称为 exactly-once；`uncertain` 任务不得自动盲重试。

## 10. 验证与回滚

验证漏斗：

1. Python 静态检查和模块结构检查。
2. Command/State/Reducer 单元测试。
3. `test_conversation_execution.py`、`test_chat_generation_executor.py` 和新增 Runtime 定向测试。
4. 取消、续租失败、重复 commit、重复事件和并发 claim 集成测试。
5. 需要数据库/多 Worker 时再执行真实 PostgreSQL 并发演练。

第一阶段可通过 feature flag 关闭 Runtime 适配层并回到现有执行器；数据库迁移采用只增不删策略。第二阶段以后新增事件和幂等记录保留，不通过回滚删除已写入的事实数据。

## 11. 未提前扩大范围的内容

- 不新增 Kafka、Celery、Temporal 或新的进程模型。
- 不把普通同步工具强行拆成独立 Actor。
- 不实现 token 级别的生成续接；lease 恢复仍以固定 ContextSnapshot 重新执行为基础。
- 子任务的具体任务类型和父子表字段在进入阶段 4 前根据现有子 Agent 调用链定稿。
