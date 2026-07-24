# AGENT 13：Persistence 持久化与恢复

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：事实模型、事件、事务、Checkpoint、Outbox、租约、重放和恢复
> 后续专项：Protocol/UI、Observability、Testing、端到端链路继续核验

## 1. 结论摘要

Agent Runtime 持久化不能只回答“聊天记录存哪里”，它必须保证：

- Worker 崩溃后任务可恢复。
- 同一 Action 不会因重试重复扣费或重复产生外部副作用。
- UI、模型和审计能引用同一个事实对象。
- Goal、SkillRun、SubRun 和异步媒体完成后能准确唤醒父流程。
- Schema 升级后旧 Session 仍可读取，未知状态不会自动执行。

推荐使用混合持久化：

```text
Current State Tables       当前权威状态、claim、CAS
Append-only Runtime Events 时间线、审计、UI 增量
Transactional Outbox      可靠外发和父流程唤醒
Artifact Store            文件、图片、大 ToolOutput、Transcript
Checkpoints / Projections 加速恢复和查询
```

不采用纯 Event Sourcing。项目已有大量 PostgreSQL 状态和原子 RPC，重写为“所有状态都
从事件重放”会显著增加复杂度和迁移风险。也不能继续把所有新概念堆入现有 `tasks`
JSONB；应逐步引入稳定 Run/Action/Artifact 关系。

## 2. Grok Build 的存储模型

### 2.1 Session 目录

Grok 每个 Session 使用独立目录，主要文件：

```text
summary.json
chat_history.jsonl
updates.jsonl
plan.json
plan_mode.json
signals.json
announcement_state.json
goal/state.json
rewind_points.jsonl
feedback.jsonl
btw_history.jsonl
compaction_checkpoints/
subagents/
mcp/
```

`chat_history.jsonl` 保存模型 ConversationItem；`updates.jsonl` 保存 ACP 与 xAI UI
事件的统一时间线；`summary.json` 是列表和元数据 Projection，不是完整事实源。

### 2.2 追加与损坏隔离

JSONL append 不是 crash-atomic。进程被杀或磁盘满可能留下无换行的半条记录。Grok
写下一条前检查尾字节：若不是 `\n`，先补换行，把损坏限制在单条；读取器跳过异常
行，而不是让整个 Session 无法恢复。

`updates.jsonl` envelope 保存：

- timestamp。
- method：标准 `session/update` 或扩展 `_x.ai/session/update`。
- params。

读取兼容旧版没有 envelope 的 ACP 行。Chat format 当前版本为 1，旧 Session 默认
按 version 0 解析。

### 2.3 Summary 并发更新

`summary.json` 的多个写入方不能各自 read-modify-write。Grok 使用固定 sidecar lock：

1. 对 `summary.json.lock` 加 exclusive lock。
2. 锁内重新读取最新 Summary。
3. 应用字段级 Patch。
4. monotonic 字段只能增大，counter 基于锁内新值递增。
5. 临时文件写入后原子 rename。

锁文件本身不 rename，避免 writer 锁住旧 inode。这个细节对应数据库中的行锁/CAS：
Projection 更新必须以最新状态为基准，不能用陈旧对象整行覆盖。

### 2.4 Replay 与 Checkpoint

Grok 可以从 `updates.jsonl` 重放到指定 prompt index，支持：

- 连续取消的用户 Turn。
- Rewind marker。
- 压缩前后目标位置。
- Compaction Checkpoint。
- 损坏行跳过。

Checkpoint schema version 当前支持到 1；发现更高未知版本时拒绝用它恢复 Conversation，
而不是猜测。Checkpoint 缺失或损坏时，根据目标位置回退原始 updates；关键
`original_user_info` 无法恢复时明确报错。

Checkpoint 前的压缩历史作为整体；Checkpoint 后按真实用户 Turn 递进裁剪。目标在
Checkpoint 之前时，必须丢弃该 Checkpoint 并从原始历史恢复。

### 2.5 搜索 Projection

Grok 使用独立 SQLite 搜索索引，并支持从 JSONL byte offset 做 delta replay。索引：

- assistant text 总计最多 100K 字符。
- ToolCall 最多 200 个。
- Tool 内容最多 100K 字符。
- Schema version 升级可清理旧 stub/hash 后重建。

索引读取失败不能被解释成“没有结果”；busy/locked 是暂态故障。Projection 可删除和
重建，不能成为业务事实源。

## 3. Grok 持久化的边界

优点：

- Append timeline、状态 Projection、Checkpoint 职责清楚。
- 子 Agent 独立子目录但保持 Parent 关系。
- Rewind、fork、compaction 和旧 Schema 有完整恢复测试。
- 损坏单行、并发 Summary 更新和增量索引都有明确处理。

限制：

- 本地文件适合单用户 CLI，不适合多 Worker 跨节点 claim。
- Session Actor 的部分活跃状态仍在内存。
- 外部副作用没有数据库事务统一覆盖。
- 后台 OS 进程 Manifest 只能提醒，无法保证重新接管。
- JSONL timestamp 为秒级，不是全局严格事件序号。

## 4. EVERYDAYAIONE 现状

### 4.1 已有强项

项目已有比本地 JSONL 更强的 SaaS 基础：

- PostgreSQL `conversations/messages/tasks`。
- Turn ID、reply_to、context revision 和固定 Snapshot。
- serial/branch queue sequence。
- task execution token、attempt、lease expiry。
- claim/renew/commit/fail/cancel 原子 RPC。
- Conversation Worker 数据库扫描与 Redis best-effort 唤醒。
- WebSocket progress 的 fencing 持久化。
- `conversation_deliveries` Outbox 与独立 delivery lease。
- 媒体 TaskCompletionService 的 callback/poll 互斥。
- tool audit、credit transaction、scheduled task runs。
- Workspace + OSS Artifact 实体能力。

Actor 默认参数：

| 参数 | 当前值 |
|---|---:|
| Generation lease | 90 秒 |
| renew interval | 30 秒 |
| 连续 renew failure | 2 |
| max attempts | 3 |
| Worker concurrency | 5 |
| scan batch | 100 |
| shutdown wait | 10 秒 |
| Delivery lease | 120 秒 |

`commit_generation_turn` 已把输出消息、Task、Conversation revision、积分扣减和 owner
释放放入同一数据库事务；终态触发 Outbox，外部通知在提交后执行。这是目标架构的
正确基础，应保留。

### 4.2 当前断层

1. `tasks` 同时承载 Chat、图片、视频和多种恢复字段，继续加入 Goal/Skill/SubRun
   会失控。
2. ToolCall/Action 没有统一的持久实体；`tool_audit_log` 是审计，不足以恢复执行。
3. Artifact 多存在于 message/task JSON 和 Workspace，缺少统一 lineage。
4. Goal、SkillRun、SubRun 尚不存在持久模型。
5. Chat progress 是 Task JSON 投影，没有统一 Runtime Event 流。
6. Parent wake-up、UI delivery 和业务 Outbox 尚未统一事件信封。
7. 不同 Worker 使用数据库、Redis lock 或进程内 lock，所有权语义不一致。
8. Message 内容同时承担用户展示、模型上下文和产物引用，版本演进边界不清晰。

## 5. 目标事实模型

```text
Conversation
  └─ Turn
      └─ Run
          ├─ Goal / SkillRun / SubRun
          ├─ Action
          │   └─ ActionAttempt
          ├─ Artifact
          └─ RuntimeEvent
```

### 5.1 Conversation / Turn / Message

- Conversation：用户可见会话、当前 closed revision。
- Turn：一次用户输入到一个或多个 Run 的逻辑边界。
- Message：用户和 Assistant 可展示内容，引用 Run/Artifact。
- ContextSnapshot：`conversation_id + base_revision + through_message_id` 的不可变读取
  合同，不必复制全部历史。

现有字段先保留，后续用 view/RPC 兼容，不进行一次性表重写。

### 5.2 Run

统一一次 Agent 推理生命周期：

```text
run_id / conversation_id / turn_id
parent_run_id / goal_id
run_type: chat | goal_worker | skill | subagent | scheduled
status
agent/model/tool_catalog/skill revisions
context_anchor
permission_mode / policy_snapshot_ref
budget / usage
lease_token / lease_expires_at / attempt
terminal_reason
```

现有 Chat `task` 第一阶段可一对一映射 Run；媒体 Task 不强制迁移成 Run，它是 Action
的专业任务记录。

### 5.3 Action / Attempt

Action 表示模型/计划决定做什么；Attempt 表示某次实际调用：

```text
Action
  action_id / run_id / step_id
  capability_id / normalized_arguments_hash
  risk / authorization_grant_id
  idempotency_key
  status: planned | authorized | running | accepted | completed |
          rejected | failed | cancelled | unknown

ActionAttempt
  attempt_id / action_id / attempt_no
  executor/provider/connection
  request_ref / external_operation_id
  started_at / lease / terminal_at
  result_ref / error_class / cost
```

重试创建 Attempt，不创建新的逻辑 Action。是否可重试取决于 Action 状态和 Provider
幂等能力；`unknown` 必须先 reconcile。

### 5.4 Artifact

```text
artifact_id / org_id / owner_scope
kind / mime_type / storage_uri
checksum / size / metadata
created_by_action_id / source_artifact_ids[]
status / retention / sensitivity
```

Message 只保存 `ArtifactPart{artifact_id, display metadata}`。URL 是可刷新 Delivery
属性，不是 Artifact identity。

### 5.5 Goal / SkillRun / SubRun

各自保留专业状态，但共享 `run_id/parent_run_id/status/budget/events`：

- Goal：objective、contract、gap、verification、continuation。
- SkillRun：skill version/hash、current step、step records。
- SubRun：parent step、child conversation、result/evidence。

不建设一个包含所有字段的万能 `tasks` 表。

## 6. Runtime Event

采用 append-only 事件作为时间线和集成边界，不从事件计算全部当前状态：

```text
event_id / org_id
aggregate_type / aggregate_id
aggregate_version
event_type / schema_version
run_id / parent_run_id / correlation_id / causation_id
payload / payload_ref
created_at
```

事件示例：

- `run.started`
- `model.response.completed`
- `action.accepted`
- `artifact.created`
- `subrun.completed`
- `goal.waiting`
- `run.completed`

同 aggregate 使用唯一 `(aggregate_id, aggregate_version)`；生产状态变更和事件 append
在同一事务。大 payload 只存 ref。

UI progress 高频 token delta 不必全部永久落库。可分：

- Durable events：状态转换、Action、Artifact、权限、错误、终态。
- Ephemeral stream：token/chunk/动画进度，可短期缓存。
- Checkpointed progress：用于刷新恢复的最近合并文本和步骤。

## 7. Transactional Outbox

统一信封：

```text
outbox_id / event_id
destination: ui | parent_run | wecom | webhook | indexer
partition_key
payload_ref
status / attempt / next_attempt_at
lease_token / lease_expires_at
delivery_checkpoint
last_error_class
```

核心事务：

```text
更新 aggregate state
+ append RuntimeEvent
+ insert Outbox rows
= 单一 PostgreSQL transaction
```

Consumer 使用 `FOR UPDATE SKIP LOCKED`、lease/fencing、指数退避和终态 CAS。目标系统
无业务幂等 ACK 时只能提供可审计 at-least-once，文档必须明确。

Parent Goal 唤醒也走 Outbox，不能依赖进程内 callback。

## 8. Lease、Fencing 与幂等

统一所有 Worker 所有权：

```text
claim → attempt + 1, issue lease_token
renew → token/current status match
commit → token match + lease valid + terminal CAS
```

数据库锁解决 claim 竞态，fencing token 解决旧 Worker 迟到提交。Redis 只做：

- best-effort wake。
- 非权威缓存。
- 可丢失限流提示。

不能让 Redis lock 成为唯一业务终态保护。

幂等层级：

| 层级 | Key |
|---|---|
| User request | client request/message ID |
| Run | turn + run type + branch key |
| Skill step | skill_run + step ID |
| Action | run + step + normalized intent |
| Provider | action ID/Provider idempotency key |
| Outbox | event + destination |
| Artifact | content checksum + owner scope |

## 9. Checkpoint 与恢复

Checkpoint 是加速器，不是唯一事实源：

```text
checkpoint_id / aggregate_type/id
through_event_version
schema_version
state_blob / state_hash
created_at
```

恢复顺序：

1. 加载当前状态表。
2. 验证 lease/terminal 状态。
3. 可选读取兼容 Checkpoint。
4. 应用 Checkpoint 后 Durable events。
5. 对 running/accepted/unknown Action 做 reconcile。
6. 重建 ContextSnapshot/Goal continuation。
7. 通过 Outbox 恢复 UI 和 Parent wake。

未知 Checkpoint schema：忽略并从状态/事件恢复；未知运行状态：转
`recovery_paused`，绝不默认 active。

## 10. Rewind、Fork 与重试

- Rewind：创建新 branch/head，不物理删除旧事实和已发生外部副作用。
- Fork：共享只读历史 revision，新的 Conversation/Run 使用独立 branch。
- Retry model response：新 Run，引用原 input/context anchor。
- Retry Action：原 Action 下新增 Attempt。
- Regenerate media：若用户要求新结果，创建新 Action，不是假装旧 Attempt 重试。

UI 隐藏 dead branch 不等于删除审计。积分和外部动作不能随聊天 Rewind 自动撤销，
除非对应 Executor 明确支持补偿。

## 11. Schema 与兼容

所有持久信封包含 `schema_version`。升级策略：

- Additive 字段先 nullable/default。
- Writer 先双写，Reader 兼容新旧。
- 回填后切读。
- 指标确认后停止旧写。
- 最后另行删除旧字段。

事件 payload 按 event type 独立版本；不使用一个全局 JSON schema 版本控制所有事件。
Projection 可重建，事实表迁移必须有 rollback/forward-fix。

## 12. 数据保留与安全

- org_id 必须存在于所有事实、事件、Artifact 和 Outbox。
- RLS/RPC 成员校验与 Worker service role 分离。
- Prompt、Tool arguments/result 可能敏感，日志和 Event 默认保存 hash/ref。
- Credential 永不进入 Event/Checkpoint。
- Artifact retention 和 Message retention 分离。
- Audit 事件不可被普通会话删除操作级联清除。
- 用户导出/删除请求必须处理 transcript、Artifact、Memory 和索引副本。

## 13. 边界场景

| 场景 | 处理 |
|---|---|
| 状态提交成功、WS 失败 | Outbox 重投，业务终态不回滚 |
| Provider 成功、Worker 崩溃 | external ID/idempotency key reconcile |
| Provider 无法确认 | Action=unknown，禁止盲重试 |
| Outbox 重复发送 | destination 幂等；否则明确 at-least-once |
| Worker lease 到期后迟到 | fencing 拒绝 commit |
| Checkpoint 损坏 | 忽略并从状态/事件恢复 |
| Event payload 过大 | Artifact/payload ref |
| Projection 落后 | 返回 staleness，不当作无数据 |
| 父 Goal 已取消、Child 完成 | 保存 Child 事实，不自动继续 Goal |
| Artifact 上传成功、DB 失败 | orphan sweeper 按 checksum/action 清理或认领 |
| DB 成功、OSS 上传未完成 | Artifact=pending，不能展示 completed |
| 双重退款/扣费 | ledger 唯一业务键 + 原子 RPC |
| Migration 中混合版本 | capability/version gate，旧 Worker 不 claim 新类型 |

## 14. 与 Grok 的取舍

直接采用：

- 状态 Projection 与 append timeline 分离。
- Checkpoint schema/version 与降级恢复。
- 损坏记录隔离。
- rewind/fork 不混淆。
- 搜索索引是可重建 Projection。
- 子 Agent 独立持久空间和父引用。

保留本项目更优部分：

- PostgreSQL 事务、Actor queue、lease/fencing。
- Context revision。
- 原子消息/积分/Task/Conversation commit。
- Delivery Outbox 和跨通道 Worker。
- OSS/Workspace Artifact。

不照搬：

- JSONL 作为多节点业务事实源。
- 进程内活跃状态为唯一真相。
- 本地 Manifest 代表后台任务恢复。
- 纯事件重放所有状态。

## 15. 分阶段迁移

本轮只形成设计，不修改数据库或运行代码。最终重构建议：

1. 建立统一 ID、状态和 Event/Outbox 信封规范。
2. 先新增 RuntimeEvent 观测双写，不改变现有执行。
3. 新增 Artifact Registry，现有 Message/Task 保持兼容引用。
4. 新增 Run 映射层，Chat Task 双写 Run。
5. 新功能 Goal/SkillRun/SubRun 直接使用新模型。
6. 新增 Action/Attempt，先迁移 MCP 和长流程，媒体逐步适配。
7. Parent wake/UI/企微统一 Outbox 类型。
8. 验证恢复与回放后，才收缩 `tasks` 的万能职责。

下一层进入 Protocol / UI：核验 ACP/WebSocket 事件如何映射 RuntimeEvent、前端如何展示
Run/Action/Artifact/Permission/Goal/SubRun 状态，以及断线重连和降级。
