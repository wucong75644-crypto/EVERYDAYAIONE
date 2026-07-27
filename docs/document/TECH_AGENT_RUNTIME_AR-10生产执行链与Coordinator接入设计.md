# Agent Runtime AR-10：生产执行链与 Coordinator 接入设计

> 基线：`73eb090f`（`codex/agent-runtime-ar-08-09-integration`）
> 性质：生产代码只读审计 + 后续实施冻结；本任务不修改运行时代码、不新增迁移、不部署。
> 决策状态：冻结供 AR-11 及后续任务实施。migration `212`～`216` 保持不可修改。

## 1. 审计结论

### 1.1 已证实事实

1. 当前生产 Chat 的唯一执行 Owner 是 Conversation Actor，而不是 Agent Runtime：
   `message.generate_message` / WeCom ingress → generation enqueue RPC → `ConversationWorker`
   → `ConversationExecutionService` → `ChatGenerationExecutor`
   → `execute_chat` → `prepare_chat_stream` → existing provider adapter
   → `_execute_tool_calls` → Actor fenced terminal RPC。
2. `backend/services/agent/runtime/` 已具备 Session、Command、Run、RunAttempt、
   ModelStep、Event、Projection Outbox 的领域合同，迁移 `212`～`216` 及
   Postgres/Model adapter；没有 Coordinator application service，也没有生产调用方。
3. `complete_model_step` 只完成 `agent_model_steps` 并追加事件/outbox；它不创建 Action。
   当前没有 Action、ActionAttempt、ActionResult 表、RPC 或 Repository。
4. Model adapter 能把 timeout、HTTP 502/503/504、响应开始后断流表示为
   `ProviderAttemptOutcome.UNKNOWN` / `ModelCallUnknownError`；数据库没有 provider
   attempt 表，也没有可持久恢复的 ModelStep unknown 状态。
5. Runtime Event/Projection Outbox 已有持久合同和 claim/readback adapter，但没有
   production projector worker；WebSocket 与 WeCom 仍消费旧 `messages/tasks/
   conversation_deliveries` 事实。

### 1.2 冻结决策

`RuntimeCoordinator` 是目标执行链唯一 Owner。Conversation Actor 在切换前继续是旧
Owner；切换后仅保留为兼容 ingress/wakeup shim，不再 claim、续租、执行模型、执行工具
或提交终态。禁止让旧 Actor 与 Runtime 同时消费同一 generation。

Coordinator 只编排持久状态，不在数据库事务内做 Provider/Executor/通道外部 I/O。
每个外部调用前先持久化 attempt 与幂等身份，调用后再以 fencing token 和
state version 提交结果。

## 2. 审计证据与当前生产链

### 2.1 Web 普通聊天

| 阶段 | 真实位置 | 当前事实 |
|---|---|---|
| HTTP、认证、租户 DB | `backend/api/routes/message.py::generate_message`、`backend/api/deps.py` | `OrgCtx`、`ScopedDB` 建立请求边界 |
| 请求幂等 | `MessageIdempotencyService.claim` | 在创建 generation 占位前 claim/replay |
| Chat 准备/入队 | `backend/api/routes/message_chat_preparation.py::prepare_and_start_chat_generation` | 生成稳定 Actor task id，写旧 messages/turn/tasks，并标记 `delivery_context.actor=true` |
| Worker claim | `backend/services/conversation_worker.py::ConversationWorker` | DB 扫描为事实源，Redis 仅唤醒 |
| lease/fencing | `backend/services/conversation_execution.py::claim_* / _renew_loop` | generation task execution token；连续续租失败时设置 ownership lost 并取消本地执行 |
| 执行 | `ChatGenerationExecutor.execute` | 读取固定 input/context anchor 和 execution scope |
| 上下文/模型 | `execute_chat` → `prepare_chat_stream` → `create_chat_adapter` → `stream_chat` | `ProviderContextPlan` 已用于每轮 provider projection，但 Runtime ModelPort 未接入 |
| 工具循环 | `execution_engine._run_loop / _execute_tools` | 多轮模型；`_execute_tool_calls` 可并行，结果直接写回内存 messages |
| 原子终态 | `ConversationExecutionService._commit` | `worker_commit_generation_turn_with_context_v2` 原子提交 message/task/turn/context/artifact/credits 相关旧事实 |
| 投递 | `ActorWebSink`、`ActorTerminalDelivery`、`WebSocketManager` | 流式 best-effort；终态以旧持久事实恢复 |

若 HTTP 响应在入队提交后断开，generation request/task 已是持久事实，Actor 后续继续；
客户端用已有消息/任务查询和 WebSocket 重连恢复。HTTP 响应本身不是执行提交点。

### 2.2 企业微信

`backend/api/routes/wecom.py` 验签解密后调用 `enqueue_wecom_callback`；
`CallbackInboxWorker` 以 lease claim 入站记录，解析组织、用户和 Channel；
`WecomIngressMixin._enqueue_actor_message` → `enqueue_wecom_message`
→ `enqueue_wecom_generation_turn_v2`，之后共用 Conversation Actor Chat 链。

旧 task 达终态时，`124_conversation_delivery_outbox.sql` 的 trigger 创建
`conversation_deliveries`；`WecomDeliveryWorker` 通过 claim/renew/complete/fail RPC
投递最终回复。重复回调由 callback inbox 与 actor enqueue 的稳定身份吸收；最终通道
投递由 delivery id/lease/checkpoint 去重。Runtime 的 `projection_kind='wecom'`
当前没有接替它。

### 2.3 Actor、恢复和 single-owner

- `ConversationWorker` 周期扫描 DB 并接受 Redis best-effort wakeup，进程重启不丢队列。
- claim RPC 可重新领取 lease 过期任务；`ConversationExecutionService` 每 5 秒续租，
  两次失败后取消本地 execution task。
- terminal commit/fail 必须携带 generation execution token；丢 lease 的旧 Worker
  不能提交。
- 服务关闭会 drain/cancel 本地任务；孤儿由扫描器和过期 lease 恢复。
- 当前 single-owner 的权威位置是 generation task claim RPC + Conversation Actor
  worker，不是 Python 进程内集合，也不是 Redis。

### 2.4 模型、工具、Completion 与结算

- `prepare_chat_stream` 创建旧 `ChatAdapter` 并装配历史、工具、permission、预算。
- 每轮 `_read_turn` 通过 `ProviderContextPlan.project()` 调用 `stream_chat`；旧链把
  流 chunk 直接投递给 Web Sink。
- `_execute_tools` 收集全部 tool calls 后调用 `ChatHandler._execute_tool_calls`；
  同步工具、ERP、文件、Sandbox 及媒体提交仍由旧工具实现负责，Runtime Action 未参与。
- Tool 结果进入内存 context，随后下一轮模型。Action blocker 目前不存在。
- Completion Gate、Validation、Artifact ledger 已在旧 RuntimeState/handler 侧工作，
  但最终权威提交仍是 generation terminal RPC。
- credits 使用模型累计 usage，在旧 terminal commit 中结算；Runtime ModelStep 不计费。
- 旧 adapter 的 provider timeout/429/5xx/断流处理不等于 AR-09 ModelPort 的
  provider attempt 合同；生产尚未调用 `ExistingProviderModelAdapter.complete`。

## 3. AR-05～AR-09 已实施能力与缺口

| 能力 | 已实施 | 真实缺口 |
|---|---|---|
| Session / Command | 表、领域类型、ensure/submit RPC、幂等冲突、scope 校验 | 无 Web/WeCom ingress 接线 |
| Run / RunAttempt | create/claim/renew/wait/wake/complete/fail/cancel；lease、fencing、state version；claim 响应丢失 readback | 无 Coordinator/扫描 worker；没有“按队列 claim next”入口 |
| ModelStep | create/complete/fail RPC、状态机、request/response receipt | 无 provider attempt 持久化；unknown 无闭合状态；无取消 ModelStep RPC |
| ModelPort | ContextPlan 投影、稳定 request hash、provider attempt receipt、`ModelCallUnknownError` | 无生产调用方；attempt receipt 仅在进程内 |
| Event sequence | Session row lock 下单调 sequence；同事务写 RuntimeEvent 与 Projection Outbox | 业务覆盖不完整；无 Action event；无 projector |
| Projection Outbox | claim/complete/fail/readback、lease | 没有 web/wecom/audit projection 实现和 worker |
| Run claim readback | `PostgresRuntimeRepository.claim_run` 捕获连接类异常后按 run+worker readback | 只覆盖 claim 响应歧义，不覆盖 Model/Action 外部 I/O |
| 权限矩阵 | Runtime ingress、WeCom Runtime、Worker 的 RPC grant 与 scoped DB adapter 限制 | migration 216 未部署；生产入口未使用这些 role |
| cancel_run | Runtime/Worker 可调用、终态竞态由 row lock/state version 决定 | 没有 Web/WeCom cancel ingress 映射和本地 model/action cancel propagation |
| Action 合同 | 领域状态、attempt、result、retry disposition、`ExecutorPort` | 无表、RPC、Repository、Coordinator、生产 executor adapter |
| trace harness | 文本、工具、失败、restart、lease lost、scope 等 fixture | 是合同回放，不是 production wiring 集成测试 |

### 3.1 migration 212～216 的原子和锁事实

- `212`：建核心表；`append_agent_runtime_event` 锁 Session，分配 event sequence，并在
  同一事务插入 event/outbox。
- `213`：Session/Command/Run 创建、Run claim/renew。主要锁顺序是
  Conversation（ensure 时）→ Session → Command/Run。
- `214`：Run wait/wake/terminal/cancel；先锁 Session 再锁 Run；completion 检查
  blocker 与最后一个 completed ModelStep。
- `215`：ModelStep create/complete/fail 及 Projection Outbox 生命周期；ModelStep
  路径按 Session → Run → ModelStep 加锁。
- `216`：Session/Event/RunClaim/ProjectionClaim readback；未增加写模型。

不得修改这些迁移。后续新增 RPC 保持固定锁序：
`Session → Run → ModelStep → Action → ActionAttempt`；Projection Outbox 独立按
outbox row `SKIP LOCKED`，不得反向锁回业务聚合。

## 4. 目标 Owner 图

| 职责 | 唯一 Owner | 说明 |
|---|---|---|
| Session Command ingress | Web/WeCom Runtime ingress adapter | 只验证身份、ensure session、submit command、返回 durable receipt |
| Run create/claim/renew | Runtime Coordinator + Runtime Worker repository | ingress 不 claim；worker 不直接创建用户 Run |
| ModelStep create/execute/terminal | Runtime Coordinator | ModelPort 只做一次已持久化 attempt 的外部 I/O |
| 非工具 Attempt/Step terminal | AR-11 的 217 RPC | final/structured final/failure；unknown 不终结 Step |
| Tool Call → Action | AR-12 `complete_model_attempt_step_and_create_actions` RPC | 同一事务终结 Attempt/Step 并创建全部 Action |
| Action dispatch/reconcile | Action Worker / Reconciler，经 Coordinator 状态合同 | ExecutorPort 不改 Run |
| credits reserve/settle/adjust | AR-11 的 217 窄财务 RPC | ledger 权威；稳定 settlement key；Projection 无权记账 |
| Run continuation | Runtime Coordinator | blocker 清零后 wake，claim 后创建下一 ModelStep |
| Run completion/cancel | Runtime Coordinator | 所有终态由 fenced RPC 决胜 |
| Event/Outbox | 对应聚合 mutation RPC | 禁止业务代码另行 best-effort append |
| Web/WeCom Projection | Runtime Projection Worker | 投影可重放；通道发送仍由 delivery outbox |

切换后 Conversation Actor 只允许：

1. 接受旧 wakeup 并转发到 Runtime command/run 唤醒（过渡期）；
2. 为尚未迁移的非 Chat media task 保持原职责；
3. 不得 claim Runtime 已接管的 Chat task，不得调用 `execute_chat`。

## 5. ModelStep unknown 闭合设计

### 5.1 状态归属

`unknown` 属于一次 provider attempt，不是 ModelStep 业务终态。ModelStep 保持
`running`，但进入“被 unresolved attempt 阻塞”的可恢复状态。不得把 unknown 写成
`failed` 后重新调用 provider。

新增：

- `agent_model_attempts`：`id, model_step_id, run_id, attempt_number,
  idempotency_key, request_hash, provider, provider_request_id,
  status(prepared|dispatching|completed|failed|unknown|cancelled),
  response_started, request_receipt, response_receipt, ambiguity_evidence,
  usage, worker_id, execution_token, lease_expires_at, timestamps`。
- ModelStep 增加可由 RPC 维护的 `unresolved_attempt_count`（或等价约束字段），只允许
  0/1；不新增 `unknown` ModelStep 状态。
- RPC：`prepare_model_attempt`、`record_model_attempt_unknown`、
  `complete_model_attempt_without_actions`、
  `fail_model_attempt_and_step`、`record_late_model_receipt`、
  `claim_model_attempt_reconciliation`、`resolve_model_attempt`。

### 5.2 恢复规则

1. Provider I/O 前，Coordinator 在事务中创建 `dispatching` attempt，写 request hash、
   provider idempotency key 和当前 Run fencing token。
2. timeout、502/503/504、响应开始后断流或 Worker 崩溃遗留的 `dispatching` attempt，
   转为/被扫描为 `unknown`；崩溃检测基于 attempt lease 与 Run lease，不依赖日志。
3. 有 provider status/readback 能力时 Reconciler 先按 provider request id 查询；
   完成则落 receipt/usage，明确未接收且 provider 合同保证安全时才标记 retry-safe。
4. 没有可证明 readback/idempotency 的 provider：禁止再次调用；Run 进入 paused/
   waiting_interaction，要求人工决策或以已收到的可验证部分作为 degraded 结果。
5. 只有 `failed-before-dispatch` 或 provider 明确保证相同 idempotency key exactly-once/
   replay-safe 时允许新 attempt。新 attempt 仍复用 logical request hash，attempt number
   递增。
6. credits ledger 继续是财务权威。217 同时冻结窄财务 RPC：调用前按稳定
   `settlement_key=model_step_id:model_attempt_id` 预留额度（若现有计费规则要求）；
   completed attempt 以该 key 唯一结算；unknown 不结算；late receipt 只走 adjustment。
   Event/Projection 不得充当账本或自行拼装扣费。

### 5.3 原子边界与竞态

- prepare attempt：Session → Run → ModelStep 锁，验证 Run token/lease/version 后插入。
- 非 tool-calls terminal：`complete_model_attempt_without_actions` 按同锁序再锁 Attempt；
  仅允许 `final` / `structured_final`，一次性写 Attempt、ModelStep、usage、
  settlement intent、event/outbox。provider 明确失败由
  `fail_model_attempt_and_step` 原子写 Attempt/ModelStep/event；unknown 只写 Attempt
  ambiguity，不终结 ModelStep。
- tool-calls terminal：AR-11 的任何 RPC 都不得终结 ModelStep；唯一 Owner 是 AR-12
  的 `complete_model_attempt_step_and_create_actions`。
- cancel 与 provider completion 竞争：两者按 Session → Run 加锁；先提交者决定。
  completion 若见 Run cancelled 不调用普通 terminal RPC，改调
  `record_late_model_receipt`。
- Run lease 丢失不自动重调 provider；旧 attempt 先进入 reconcile。

| Provider/竞态结果 | 唯一 RPC | ModelStep |
|---|---|---|
| 普通文本 `final` | 217 `complete_model_attempt_without_actions` | completed |
| `structured_final` | 217 `complete_model_attempt_without_actions` | completed |
| `tool_calls` | 218 `complete_model_attempt_step_and_create_actions` | completed，并同事务创建全部 Action |
| 明确 provider failure | 217 `fail_model_attempt_and_step` | failed |
| unknown | 217 `record_model_attempt_unknown` / reconcile | 保持 running |
| cancel 先胜、receipt 迟到 | 217 `record_late_model_receipt` | 保持 cancelled 前既有状态，不复活 |

### 5.4 取消后的迟到 receipt 与结算

`record_late_model_receipt` 是 217 独占的 audit/reconcile RPC，锁序固定为
Session → Run → ModelStep → ModelAttempt → settlement row。它只在 Run 已
`cancelled` 且 Attempt 是该 ModelStep 的合法 dispatching/unknown attempt 时接受：

- 保存 `provider_request_id`、response receipt/hash、usage、ambiguity evidence、
  provider terminal/readback outcome，并把 Attempt 标为 `completed_late` 或
  `failed_late`；
- 不修改 ModelStep/Run 状态，不递增 blocker，不发布第二个用户终态，不触发 Provider
  retry；只追加 `model_attempt.late_receipt_recorded` audit event；
- 幂等键为 `model_attempt_id:provider_request_id:response_hash`。同 key/hash replay
  返回 `already_recorded`；同 attempt/provider request id 不同 response hash 返回
  `receipt_conflict` 并进入人工对账；
- completed late usage 通过窄财务 adjustment RPC，以
  `adjustment:model_attempt_id:response_hash` 唯一入账。已有相同 settlement/adjustment
  只读返回，绝不重复扣费；每个 ModelStep 只允许一个 effective settlement lineage。

若 completion 先锁定并提交 ModelStep/Run，后到 cancel 返回 `terminal_conflict`；
若 cancel 先提交，普通 completion 返回 `run_cancelled_use_late_receipt`，调用方随后使用
上述 RPC。两条路径都不能重新打开聚合。

## 6. Tool Call → Action 原子边界

### 6.1 数据模型

新增：

- `agent_actions`：Run/ModelStep、stable tool call id、name、normalized args/hash、
  status、retry disposition、dependency set、blocking、policy snapshot、state version。
- `agent_action_attempts`：worker/lease/fencing、idempotency key、request hash、
  dispatch/accepted/unknown/terminal、external receipt/ambiguity evidence。
- `agent_action_results`：每 Action 最多一个规范化结果，result hash、summary/data、
  artifact ids、usage/cost/receipt。

稳定 Action idempotency key：
`sha256(session_id, run_id, model_step_id, tool_call.index,
provider_call_id-or-derived_call_id, normalized_name, normalized_arguments_hash)`。
同一个 ModelStep 内 tool call id 必须唯一；同 key 不同 request hash 返回
`idempotency_conflict`。

### 6.2 必须同事务

AR-12 在 218 提供唯一 RPC `complete_model_attempt_step_and_create_actions`。它依赖
217 的 ModelAttempt/settlement 表与辅助函数，按 Session → Run → ModelStep →
ModelAttempt 加锁，验证 Run fencing/lease/version、Attempt request/response hash，
一次性完成 Attempt 和 ModelStep、批量插入全部 Actions、写 usage/settlement intent、
计算 `blocking_action_count`，再追加 `model_step.completed` 与 `action.requested`
events/outbox。任何一项失败全部回滚。

不允许先调用 217 terminal RPC 再创建 Action，也不允许 218 在 Attempt 已终结后补建
Action；否则 crash 会永久丢 tool call 或形成双 terminal Owner。

### 6.3 并行、异步与失败

- 多 tool calls 默认没有彼此依赖，形成同一 wave，可由 Action Worker 并行 claim；
  显式依赖只来自经过验证的计划，不从返回顺序猜测。
- 同步只读工具也建 Action，以便幂等、审计和恢复；可走同进程 executor，但仍先持久化。
- ERP/文件/Sandbox 根据工具副作用声明设置 retry disposition。
- 图片/视频提交在外部接受后为 `accepted`，receipt 持久化；媒体完成回调/reconciler
  写 ActionResult。accepted/unknown 不能普通 retry，因为外部副作用可能已发生。
- Policy/Interaction 尚未实施时，只允许 `policy=preauthorized` 的现有工具进入 queued；
  需要授权的 Action 保持 `awaiting_authorization`，不得暗中执行。
- tool failure 也产生规范化 ActionResult(error)。全部 blocking Action 终态后 blocker
  清零，RPC 同事务 wake Run；下一次 claim 创建下一 ModelStep，让模型看见工具错误并恢复。
- Run blocker 是未达到可供模型消费结果状态的 `blocking=true` Actions 数量，不由内存
  future 数量计算。

### 6.4 Coordinator pending Command 领取合同

现有 213 的 `submit_session_command` 和 `create_agent_run` 不提供 pending scan，也未给
Worker 核心表直权。AR-13 必须以 migration 219 独占新增窄 RPC：

- `claim_pending_agent_command_and_ensure_run(worker_id, lease_seconds,
  max_attempts)`：以 PostgreSQL 为事实源，按 cancel 优先、created_at/id 排序，使用
  `FOR UPDATE SKIP LOCKED`（或等价 advisory claim）；锁 Session → Command，复核
  RuntimeScope/role/request hash，在同一事务创建或返回 `UNIQUE(command_id)` 的唯一 Run
  及 CommandClaim lease/fencing receipt；
- `get_agent_command_run_claim(command_id, worker_id)`：claim RPC 已提交但响应丢失后的
  readback；只返回该 worker 当前有效 claim 和唯一 Run；
- `renew_agent_command_claim` / `finish_agent_command_claim`：续租和关闭领取，不授予
  `agent_session_commands`、`agent_runs` 或其他核心表直接权限。

同 Command 重复领取返回同一 Run；同 session/idempotency key 不同 request hash 返回
`idempotency_conflict`；过期 claim 可 fenced 重领，旧 token 不能创建/修改 Run；
达到 max attempts 返回 `attempts_exhausted` 并产生 durable event。不可执行 scope 返回
`scope_rejected`。cancel Command 排在 submit/continuation 前：若目标 Run 尚未创建，
同事务创建 cancelled Run/取消结果；若已创建则调用既有 fenced cancel 合同；与 submit
并发时依固定锁序先提交者决定且只产生一个 Run。Redis 只发布唤醒，扫描与恢复始终依赖
PostgreSQL。

## 7. 旧生产事实兼容矩阵

| 旧事实 | 切换期判定 | 目标判定 |
|---|---|---|
| `conversations` | 继续权威：会话身份、成员/Channel 关联 | 继续权威；Runtime Session 1:1 引用 |
| `messages` | Runtime terminal projection 写入，旧读 API 继续读 | 用户可见消息 Projection；Runtime Event/Run 是执行权威 |
| `turns` | 临时由兼容 projection 维护 | Runtime Projection；旧表保留读兼容至 contract |
| `tasks` / `scheduled_tasks` | Chat tasks 临时双写/映射；非 Chat、调度任务继续权威 | Chat 变 projection/兼容索引；媒体/调度按独立迁移决策 |
| `conversation_artifacts` | Runtime completion projection 写入，存储物化仍复用 | Artifact ledger/Runtime 事实投影到旧表 |
| generation requests | ingress 幂等继续权威并映射 command/run | 只读兼容，最终由 SessionCommand 幂等取代 |
| credits transactions | 继续财务权威；由 Runtime receipt 驱动唯一结算 | 继续权威，不把 event 当账本 |
| Actor queue/lease | 切换前唯一权威；维护窗口排空后 Chat 禁止再 claim | Chat 淘汰；非 Chat 可保留 |
| WeCom delivery | 继续唯一通道投递权威 | Runtime WeCom projector 只创建 delivery，不直接发送 |
| WebSocket event | best-effort 实时投递，不能作为事实 | Runtime Event replay + message/task projection 恢复 |

“临时双写”必须由同一 Runtime projection/outbox Owner 执行，不允许旧 Actor 和
Coordinator 各写一份。旧表投影失败只重试 outbox，不回滚已提交 Runtime 聚合。

## 8. 十二条目标调用链

以下统一约定：`C`=Coordinator，`DB`=fenced RPC，`IO`=外部 I/O，
`K`=幂等键，`E/P`=Runtime Event/Projection。

1. **普通文本最终回答**：ingress submit command(K=request id) → 219 pending claim
   原子 ensure unique Run → C claim Run
   (token) → DB prepare ModelAttempt → IO Model → DB complete attempt+step →
   credits settlement(K=step:attempt) → Completion Gate → DB complete Run
   (E run.completed) → web projection 写 message/task → WebSocket。失败从
   CommandClaim/attempt/Run/outbox 恢复。
2. **多轮模型+同步只读工具**：218 统一 RPC 完成 Attempt/ModelStep 并创建 Actions →
   Action Worker claim(token,K=tool key) → IO tool → DB ActionResult/E → blocker=0+wake →
   C claim 新 attempt/ModelStep。任何 crash 从 Action/Run lease 恢复。
3. **并行 Tool Calls**：一个 RPC 批量创建同 wave Actions；各自 claim token 并行 IO；
   每个 terminal RPC 原子递减 blocker；最后一个 wake Run，state version 防重复 wake。
4. **Tool 失败后模型恢复**：ActionResult(error) 是终态结果而非 Run fail；blocker 清零后
   下一 ModelStep context projection包含错误；模型可改用其他工具或最终回答。
5. **异步图片/视频 accepted**：dispatch 前 attempt；IO 返回 external receipt →
   DB accepted(E)；Run waiting_actions；回调/reconciler(K=external id)写 result/artifact；
   禁止 accepted 普通重试。
6. **Provider unknown**：dispatching attempt + IO → unknown DB 记录（不结算）；Run paused；
   Reconciler claim(token) readback IO；明确完成则 terminal，明确未接收且 retry-safe 才重调，
   否则人工决策。不得直接 fail+retry。
7. **用户取消与模型完成竞态**：219 优先领取 cancel Command；cancel_run 与 model
   terminal RPC 按相同锁序。取消胜则本地 cancel，迟到 receipt 由
   `record_late_model_receipt` 持久化并以 adjustment key 对账，不复活 Step/Run；
   完成胜则 cancel 返回 terminal_conflict；两者只发布一个用户终态。
8. **Worker 丢 lease**：renew 返回 ownership_lost/连续通信失败 → 本地取消；任何迟到 DB
   mutation 被 fencing 拒绝；外部已 dispatch 的 Model/Action 转 reconcile，不自动重发。
9. **Actor/Coordinator 进程重启**：219 pending Command/expired CommandClaim 和 queued/
   expired Run scan 为事实源；Redis 仅唤醒；expired attempt 被 claim/reconcile；
   内存 stream 丢失但 durable event 可 replay。
10. **WebSocket 断线恢复**：实时 ephemeral chunk 可丢；重连按 Session sequence replay
    durable events，并读取 messages/tasks projection；projector checkpoint 保证幂等。
11. **WeCom 重复回调和最终投递**：callback inbox key → submit command key → one Run；
    Run terminal event → WeCom projector 创建/确认一个 `conversation_deliveries` →
    delivery worker fenced send/checkpoint；重复均返回 existing。
12. **Scope**：企业员工=`user` scope + org/user；Channel=`channel` + org、user null；
    散客=`user` + org null；system worker=`system`，created_by 可 null。每个 ingress/RPC
    复核 scoped DB identity；Worker 不能伪造 Runtime ingress，WeCom Runtime 只操作其
    Channel/企业范围。

所有链路中数据库事务都在 RPC 内结束，Provider、Tool、WebSocket、WeCom send 均在事务外。

## 9. 发布、切换与回滚

### 9.1 顺序

1. 按依赖部署 additive schema/RPC：217 ModelAttempt+credits settlement，218 Action+
   统一 tool terminal，219 Coordinator Command/Run claim，220 Projection compatibility；
   不改 212～216。apply 必须 `217 → 218 → 219 → 220`。
2. 部署未成为 Owner 的 repository、Coordinator、Action/Reconciler、Projection 代码，
   默认关闭 claim/dispatch。
3. shadow 只允许：读取旧 generation、构造 context/request hash、验证 scope、记录
   metrics/日志或专用无副作用 shadow receipt。禁止创建 Run、调用 Provider/Tool、扣费、
   写 message/task/artifact、创建 WeCom delivery、发 WebSocket 用户终态。
4. 门禁：真实 PostgreSQL 并发/崩溃测试通过；Command claim/readback、统一 tool
   terminal、cancel late receipt、settlement/adjustment 唯一性、projection replay
   对账、unknown/accepted reconcile、角色权限均通过；旧队列可观测为零；回滚演练通过。
5. 维护窗口停止新 Chat ingress，排空旧 Actor Chat claims，确认没有 running/未提交
   generation；停旧 Chat claimant。
6. 一次性切换所有 Web/WeCom Chat ingress 与 Runtime worker owner，不按租户、用户、
   Channel 或流量 canary；确认 single-owner。
7. 恢复 ingress，观察 Run/Action/Projection/credits/WeCom delivery 对账。
8. 若回滚：先停新 ingress/claim，排空或 fence Runtime workers，保留所有 Runtime
   Session/Run/Attempt/Action/Event/Outbox 事实供审计和对账；把未对外提交的请求按稳定
   generation id 映射回旧队列，再启旧 Owner。不得删除新事实或反向迁移。
9. 观察期结束后才做 contract：移除旧 Chat claim/wiring 和临时 projection；表删除另立
   任务，不属于本阶段。

### 9.2 绝对部署门禁

在 217～220 的 Model unknown/late receipt/credits、Action 统一原子终态、
Coordinator Command claim/readback、Projection compatibility 未全部完成并通过真实
PostgreSQL 测试前，绝不能部署任何可使 Runtime 成为 Chat Owner 的配置或入口代码。
migration 216 必须在任何 Runtime production read/claim worker 启用前先部署。
additive rollback 仅允许在未产生依赖事实且 Owner 未切换时执行，逆序为
`220 → 219 → 218 → 217`；产生 Runtime 事实后停止 Worker并保留表，不做破坏性 down。

## 10. 后续实施任务冻结

| 任务 | 目标 / Owner | 依赖 | 允许范围 | 禁止范围 | migration | 验收与并行 |
|---|---|---|---|---|---|---|
| AR-11 | ModelAttempt prepare/unknown/reconcile、非工具终态、late receipt 与 credits settlement；Owner=Model lifecycle | AR-10 | `runtime/domain/model*`, `ports/model*`, 新 postgres model/settlement files、migrations/tests | production ingress、旧 Actor、Action 表/RPC | 217 独占 | 真实 PG：dispatch crash、unknown、cancel/late receipt、settlement replay；完成后才能启动 AR-12 |
| AR-12 | Action 三表及统一 Attempt+Step+Actions+usage/events 事务；Owner=Tool terminal/Action subsystem | AR-11 | `domain/action.py`, `ports/executor.py`, 新 postgres action files、migrations/tests | Coordinator、projection、旧 handler、217 文件 | 218 独占 | 真实 PG：全事务 crash、批量原子、并行 blocker、accepted/unknown；不得与 AR-11 并行 |
| AR-13 | pending Command claim/readback + Runtime Coordinator scanner/lease/continuation；Owner=Coordinator | AR-11+12 | 新 `runtime/application/`、postgres command-claim files、migrations/tests | ingress、旧 Actor、projection、217/218 | 219 独占 | 真实 PG：SKIP LOCKED、unique Run、cancel priority、response-loss readback；串行位于 AR-12 后 |
| AR-14 | Web/WeCom Runtime ingress adapter，只 submit Command | AR-13 | 新 ingress adapter、最小 route/wecom enqueue 接线、定向测试 | Run 创建、Actor executor、projection、delivery sender | 无 | duplicate/cancel/scope；Web 与 WeCom 子任务不可同时改共享 composition |
| AR-15 | Runtime Projection worker + 旧事实兼容 projection | AR-12+13 | `runtime/application/projection*`, `infrastructure/projection*`, message/task/artifact compatibility tests | ingress、Action/Model/Command schema、credits ledger、delivery sender | 220 独占 | 真实 PG replay、断线、outbox crash；可与 AR-14 在 AR-13 后并行 |
| AR-16 | Existing tools → ExecutorPort adapters，按副作用分类 | AR-12+13 | 新 executor adapters、相关工具定向 tests | Coordinator、schema、旧工具业务逻辑 | 无 | sync/ERP/file/sandbox/media accepted；可与 AR-15 并行，避免改 composition |
| AR-17 | 生产 wiring、single-owner feature gate、维护窗口/回滚脚本 | AR-14+15+16 | composition/startup/deploy scripts、运行手册、集成测试 | 212～220、业务工具 | 无 | 全链真实 PG + staging 演练；必须串行 |
| AR-18 | 观察期后 contract，删除旧 Chat Owner 接线 | AR-17+观察验收 | 旧 Actor Chat wiring、兼容投影清理、共享索引 | Runtime schema 历史事实 | 221 仅在确需 contract DB 变更时独占 | 不与 AR-17 并行；需单独确认 |

### 10.1 合并顺序与并行度

- 下一阶段只启动 **1 个任务：AR-11**。AR-12 使用 217 的表/辅助函数且拥有 tool-calls
  唯一 terminal RPC，必须等待 AR-11 合并；禁止二者并行。
- 合并/apply：AR-11(217) → AR-12(218) → AR-13(219) → AR-15(220)。
  `ports/__init__.py`、`domain/__init__.py` 等共享 export 由总控在各串行合并点统一更新。
- AR-13 完成后可并行 AR-14、AR-15、AR-16；三者的 startup/composition 统一留给 AR-17。
- AR-17、AR-18 必须串行。Coordinator、Action、Projection、兼容接线、Owner 切换不能
  由两个任务同时修改同一 composition/startup 文件。

### 10.2 表/RPC 所有权

- 217：`agent_model_attempts` 与 model attempt/reconcile RPC，仅 AR-11。
- 217 同时拥有 `record_late_model_receipt` 和窄 credits reserve/settle/adjust RPC；
  AR-11 的 terminal RPC 仅覆盖非 tool-calls。
- 218：Action 三表、`complete_model_attempt_step_and_create_actions`、Action
  claim/reconcile/wake，仅 AR-12；该统一 RPC 是 tool-calls 跨表终态唯一 Owner。
- 219：CommandClaim/attempt（或等价持久 claim 记录）、pending scan+ensure Run、
  renew/finish/readback RPC，仅 AR-13；不得授予核心表直权。
- 220：projection compatibility checkpoint/mapping，仅 AR-15；Projection 不拥有账务。
- 221：观察期后 contract 预留，仅 AR-18；没有实际 DB contract 时不得创建空 migration。

每项输入输出沿用 typed receipt：成功、already_exists、ownership_lost、lease_expired、
stale_version、terminal_conflict、idempotency_conflict 必须是显式 outcome。网络响应歧义
必须有 readback RPC；禁止用异常文本推断提交状态。

## 11. 测试冻结

每个实施任务先定向 unit/contract，再真实 PostgreSQL：

- 幂等同 key 同 hash replay、同 key 异 hash conflict；
- 固定锁序并发、SKIP LOCKED、lease expiry、旧 token 拒绝；
- RPC 提交成功但客户端丢响应的 readback；
- Worker 在 prepare/dispatch/accepted/terminal 每一断点 crash；
- Provider unknown、Action accepted/unknown 禁止普通 retry；
- cancel/completion、最后 blocker/wake 竞态；
- pending Command SKIP LOCKED、cancel priority、unique Run、attempt exhaustion、
  claim response-loss readback 与核心表零直权；
- Attempt/Step/Actions/usage/events/outbox 在每个语句断点的全有或全无；
- late receipt replay/conflict、单 settlement lineage、reserve/settle/adjustment 重放不扣费；
- Event sequence 无 gap/duplicate，Projection replay 幂等；
- Runtime/WeCom/Worker scope/role 权限矩阵；
- 旧 messages/tasks/credits/WeCom delivery 对账与唯一副作用。

External/付费 Provider 不是数据库合同验收前提；Provider adapter 用协议级 fake。媒体和
WeCom staging 端到端在 AR-17 发布门禁执行。

## 12. 共享文档纠偏清单（由总控统一修改）

本任务不修改共享索引。总控在 AR-10 合并后统一核对：

- `TECH_AGENT_RUNTIME目标架构与模块边界.md`：将 AR-05～09 domain/ports/postgres/model
  adapter 标为已实现；Coordinator/Action persistence/production wiring 仍为缺口。
- `TECH_AGENT_RUNTIME核心状态机.md`：明确 ModelStep 无 unknown，unknown 属于未来
  ModelAttempt；Action 状态只有 domain contract。
- `TECH_AGENT_RUNTIME数据库模型.md`：212 已有表与未来 Action/ModelAttempt 分开。
- `TECH_AGENT_RUNTIME数据库RPC与原子边界.md`：登记 213～216 实际签名；说明
  `complete_model_step` 不创建 Action，并补 217～220 的唯一事务 Owner/锁序。
- `TECH_AGENT_RUNTIME统一Session运行时与上下文加载合同.md`：ProviderContextPlan 已在
  旧 execute_chat 使用，但 Runtime ModelPort 未生产接线。
- `PROJECT_OVERVIEW.md`：补 AR-08/09 adapter 和本设计文档路径。
- `FUNCTION_INDEX.md`：补 Postgres/Model adapter 已有公共类；未来 Coordinator 不得提前写。
- `CURRENT_ISSUES.md`：记录 production Owner 仍为 Conversation Actor、216 未部署、
  Model unknown、Action 统一终态、pending Command claim、late receipt/credits 是切换阻塞项。

## 13. 未决风险（不改变冻结 Owner）

1. 各 Provider 是否提供可按 request id 查询的状态与幂等保证，需要 AR-11 按 provider
   逐项形成 capability matrix；缺失者默认不可自动重试。
2. 旧 credits terminal RPC 的精确字段映射由 AR-11 在 217 冻结并实现窄财务合同；
   若现有账本无法表达 reserve/adjustment，必须在 217 内闭合，不能转交 Projection。
3. 部分工具的真实副作用/幂等性目前散落在 handler/executor 中；AR-16 未分类前一律按
   `retry_after_reconcile` 或更严格处理。
4. WebSocket 流式 chunk 不是 durable event；若产品要求逐 token 重放，需要新的产品/
   成本决策，本设计只保证 durable block/terminal 恢复。
5. migration 216 尚未部署是环境事实；AR-17 必须以 migration ledger 验证，不能由代码
   版本推断。

## 14. 本任务边界声明

AR-10 只新增本设计文档。未修改 Runtime 或旧生产运行时代码，未修改 migration
`212`～`216`，未新增 migration，未修改共享索引，未推送、未部署、未切换 Owner。
