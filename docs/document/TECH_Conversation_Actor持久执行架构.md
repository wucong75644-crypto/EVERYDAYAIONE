# Conversation Actor 持久执行架构

> 状态：分阶段实施中（数据库队列、执行权与原子终态协议已完成）
> 日期：2026-07-17
> 取代范围：`TECH_Turn快照与显式媒体协议治理.md` 中“串行/分支执行、恢复与最终验收”部分
> 不取代：显式媒体协议、Turn/revision、ContextSnapshot、v2 闭合历史缓存

## 1. 目标与非目标

### 1.1 目标

- 普通 Chat 在同一 conversation 内按提交顺序串行执行，不丢弃用户后续输入。
- Web 与企业微信共用一套持久化任务状态机和 ContextSnapshot 机制。
- `branch` 仅供可信内部 Agent 显式创建，并行任务拥有独立输入、回复关系和固定 revision。
- 数据库是队列、执行权、Turn 和结果的唯一事实来源。
- Redis 仅负责低延迟唤醒；Redis 丢消息或不可用时仍能从数据库恢复。
- 服务重启、Worker 丢失、租约过期和重复回调不能造成双提交、双扣费或重复推进 revision。
- 将模型执行与任务生命周期从超长 `ChatHandler._stream_generate` 中分离。

### 1.2 非目标

- 图片、视频、电商图继续使用现有异步任务链，不进入 Chat serial Actor。
- 本次不引入 Kafka、Celery、Temporal 等新基础设施。
- 不修改普通消息内容协议和现有 `/messages/generate` 成功响应结构。
- 不允许公网请求直接指定 `execution_mode=branch`。
- 不在 Actor 中保存完整 LLM messages；上下文仍由 ContextSnapshot 构造。

## 2. 项目上下文

### 2.1 架构现状

当前 FastAPI 单体通过 Supabase/PostgreSQL 保存 messages、tasks 和 conversations，
Chat 使用进程内 `asyncio.create_task` 启动流式执行。Web 和企业微信已经统一使用
Turn/revision 与 ContextSnapshot，但任务创建后立即执行，同一 conversation 仍可能同时
存在多个 Chat 任务。Redis 已从共享 messages 状态降级为带 revision 的闭合历史缓存，
因此执行顺序必须由数据库任务状态机治理。

### 2.2 可复用模块

- `message_generation_requests`：请求幂等、指纹和响应重放。
- `TaskLimitService`：全局与单会话任务容量控制。
- `bind_generation_turn` / `close_generation_turn`：输入锚点和 revision 事务基础。
- `ContextSnapshot`：固定任务历史边界。
- `taskRestoration.ts`、`/tasks/pending`：前端 pending/running Chat 恢复。
- WebSocket task subscription：任务开始、chunk、完成和错误推送。
- `recover_orphan_tasks`：服务启动恢复入口。
- `RedisClient`：通知加速；不作为所有权事实。
- 企业微信 `MessageGateway` 和主动发送能力。

### 2.3 设计约束

- PostgreSQL RPC 使用 `SECURITY INVOKER`、固定 `search_path` 和 org/user 范围校验。
- 所有状态迁移必须是幂等事务；不能由 Python 分步猜测状态。
- 任何模型调用前必须已经获得数据库执行权并绑定 ContextSnapshot。
- 所有 DB 核心结果提交必须先于 WebSocket/企业微信等外部投递。
- 日志至少包含 `org_id/conversation_id/task_id/turn_id/base_revision/execution_token`。
- 新模块和函数必须满足 500/120/15/4 结构阈值。

### 2.4 潜在冲突

- 当前 Chat task 创建即为 `running`，需改为 `pending` 后由 Worker 认领。
- `ChatHandler._stream_generate` 同时承担执行、持久化、重试和后置任务，必须拆分。
- `close_generation_turn` 目前不能原子覆盖积分、消息结果、task 终态和执行权释放。
- Smart Model 递归重试未继续传递 `context_anchor`。
- 现有 orphan recovery 会直接把部分 running task 标记 completed，不符合重新认领语义。
- 企业微信当前持有临时 `reply_ctx` 同步等待，不能支持重启后延迟投递。

## 3. 核心不变量

1. 一个 conversation 最多存在一个拥有有效执行权的 serial Chat task。
2. `pending` task 没有 `base_context_revision`；认领成功时才绑定最新基线。
3. `execution_token` 每次认领重新生成；完成事务只接受当前 token。
4. Worker 失去租约后必须停止生成，且旧 token 永远不能提交数据库终态。
5. serial task 的 user/assistant 消息只在原子完成事务中进入新 revision。
6. branch 不占用 serial owner，但仍必须通过 token 提交自己的 Turn。
7. Redis 通知失败只增加认领延迟，不能改变任务结果。
8. DB 事务完成前不发送 message_done 或企业微信最终回复。
9. 任务完成、失败、取消和中断都是显式终态，不允许隐式覆盖。
10. 相同 idempotency key 只创建一个 pending task。

## 4. 状态机

### 4.1 Task 执行状态

```text
pending
  ├─ claim → running
  ├─ cancel → cancelled
  └─ terminal validation failure → failed

running
  ├─ commit success → completed
  ├─ commit failure → failed
  ├─ user cancel → cancelled
  ├─ partial recovery → interrupted
  └─ lease expired → pending (attempt 未超限)

pending/running
  └─ attempts exhausted → failed
```

`branch` 使用同一状态机，但认领条件不检查 conversation 的 serial owner。

### 4.2 执行权状态

```text
unowned
  → claimed(task_id, token, lease_until)
  → renewed(token, lease_until)
  → committed/released

claimed
  → lease expired
  → 新 Worker 使用新 token 重新认领
  → 旧 token 被所有提交 RPC 拒绝
```

## 5. 数据库设计

### 5.1 tasks 新增字段

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `queue_sequence` | BIGINT | identity/非空 | conversation 内稳定排队依据 |
| `execution_token` | UUID | NULL | 当前认领 fencing token |
| `lease_expires_at` | TIMESTAMPTZ | NULL | 当前执行租约 |
| `execution_attempt` | INTEGER | NOT NULL DEFAULT 0，CHECK >= 0 | 认领次数 |
| `started_at` | TIMESTAMPTZ | NULL | 首次开始执行时间 |
| `delivery_context` | JSONB | NOT NULL DEFAULT `{}` | 企业微信等持久化投递目标，不保存密钥 |
| `terminal_reason` | TEXT | NULL | 失败/取消/中断的稳定原因码 |

现有 `status` 继续使用 `pending/running/completed/failed/cancelled/interrupted`。
迁移前必须确认数据库现有 status CHECK；若约束缺少新值，迁移同步扩展。

索引：

- `(conversation_id, execution_mode, status, queue_sequence)`
- `(status, lease_expires_at) WHERE status = 'running'`
- `(conversation_id, status) WHERE status IN ('pending', 'running')`

### 5.2 conversations 新增字段

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `active_serial_task_id` | UUID | FK tasks(id) ON DELETE SET NULL | 当前 serial owner |
| `actor_updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | Actor 状态观测 |

不在 conversations 保存 token；token 属于 task 认领尝试，避免复制状态。

### 5.3 queue_sequence

- 使用数据库全局 identity/sequence，排序键为 `(queue_sequence, id)`。
- 不使用 `created_at` 单独排序，避免相同时间戳和客户端时间污染。
- 不要求 conversation 内连续，取消或失败可留下间隙。

## 6. 数据库 RPC

### 6.1 `enqueue_generation_turn`

职责：原子插入 pending task、校验输入/输出消息归属并保存 Turn 关系。

输入：

```text
conversation_id, task_id, input_message_id, output_message_id,
turn_id, execution_mode, org_id, delivery_context
```

规则：

- serial/branch 均不在 enqueue 时绑定 revision。
- 相同 task/turn 的重复调用返回原记录。
- 公共 API 固定传 serial；branch 只能由内部服务调用。

### 6.2 `claim_next_serial_generation_turn`

职责：认领 conversation 中最早的 serial task，并同时绑定 ContextSnapshot 基线。

serial 事务：

1. `SELECT conversation FOR UPDATE`。
2. 检查 `active_serial_task_id`。
3. owner 仍 running 且租约有效：不认领。
4. owner 租约过期：将旧 attempt 退回 pending 或标记 failed。
5. 按 `queue_sequence` 取最早 pending serial task `FOR UPDATE SKIP LOCKED`。
6. 写入新 token、running、lease、attempt。
7. 写入 conversation owner。
8. 保存当前 revision/through-message。
9. 返回 task、token 和 ContextAnchor。

### 6.3 `claim_branch_generation_turn`

职责：按 task_id 精确认领内部 branch task。

规则：

- 锁定 conversation 后再锁定 task，锁顺序与 serial claim 一致。
- 不读取/修改 serial owner。
- 在认领时绑定当前 revision。
- 有效租约返回 busy；过期租约可重新认领，超过最大次数进入 failed。

### 6.4 `renew_generation_lease`

- 条件：task 为 running、token 完全一致、未进入终态。
- 只向未来延长固定租约窗口。
- 返回 `renewed/ownership_lost/terminal`。
- Worker 连续续约失败后取消本地模型执行，不再尝试提交。

### 6.5 `commit_generation_turn`

职责：在一个事务中完成核心数据库副作用。

输入：

```text
task_id, execution_token, output_message_id, result_content,
usage, credits_cost, tool_digest
```

事务：

1. 锁定 task 和 conversation。
2. 校验 token、running 状态、input/output/turn/reply 关系。
3. 幂等检查：已由相同 token 完成则返回首次结果。
4. 原子扣除 Chat 积分并写 credits history；余额/组织范围复用现有规则。
5. 更新 assistant 消息内容、usage、状态。
6. 推进 conversation revision，标记 user/assistant 消息。
7. 更新 task completed 和完成时间。
8. serial task 清除 conversation owner；branch 不修改 owner。
9. 返回标准 CommitResult。

事务提交后 Python 才发送 WebSocket、企业微信消息、建议问题和摘要任务。

### 6.6 `fail_generation_turn`

- 校验 running 状态和 token 后写 failed。
- Chat 无预扣时不产生退款。
- serial 清除 owner。
- 不推进 revision；部分内容和 interrupted 内容进入闭合 revision 的规则在阶段 7 固定。
- 重复调用返回首次终态。

### 6.7 `cancel_generation_turn`

- pending/running：用户与租户范围校验后立即写 cancelled。
- running 同时清空 token 和 lease；旧 Worker 无法再提交 completed。
- cancel 与 commit 共用 conversation → task 锁顺序；先写入数据库的终态获胜。
- 取消信号只负责尽快停止本地执行，不决定数据库终态。

## 7. Worker 与唤醒

### 7.1 Worker

`ConversationWorker` 是现有 FastAPI 服务内的后台组件，不新增独立部署单元：

- 启动时扫描 pending 和过期 running Chat tasks。
- 收到 Redis conversation 通知后立即尝试认领。
- 无通知时按固定间隔扫描数据库兜底。
- 每个本地执行任务有独立续约协程和取消事件。
- 服务 shutdown 停止认领，等待有限时间后释放本地资源；不伪造完成。

### 7.2 Redis

建议通知键：

```text
actor:wakeup:{org_id}:{conversation_id}
```

Redis payload 只含 conversation/task 标识，不含上下文、token、用户内容或结果。

### 7.3 公平性

- conversation 内严格按 queue_sequence。
- conversation 之间由 Worker 并发上限和现有 TaskLimitService 约束。
- branch 使用独立并发配额，防止绕过全局成本限制。

## 8. Web 时序

```text
POST /messages/generate
  → request idempotency claim
  → task slot acquire
  → create user/assistant messages
  → enqueue_generation_turn(status=pending)
  → return existing GenerateResponse
  → Redis wakeup(best effort)

Worker
  → claim + bind latest revision
  → ContextSnapshot
  → GenerationExecutor
  → commit_generation_turn
  → message_done
  → wake next serial task
```

前端：

- 复用现有 pending Chat 占位符。
- 新增可选 `task_queued` WS 事件只改善文案，不作为正确性依赖。
- 刷新后继续通过 `/tasks/pending` 恢复 pending/running。
- 取消 pending task 时调用统一 cancel RPC。

## 9. 企业微信时序

```text
incoming message
  → 持久化 user/assistant placeholder
  → 保存 delivery_context
  → enqueue serial task
  → 立即回复“已收到/排队中”

Worker
  → 与 Web 相同的 claim/snapshot/executor/commit
  → 从 delivery_context 重建主动投递目标
  → MessageGateway 发送最终结果
  → 记录 delivery result
```

要求：

- `delivery_context` 只保存 corp/channel/user/chat 等标识，不保存 access token。
- 重启后可重新读取并主动发送。
- 发送失败不回滚已提交的 Turn；进入独立投递重试。
- 同一 task 的最终投递使用幂等 delivery key。

## 10. Chat 执行器拆分

目标结构：

```text
handlers/chat/
  executor.py          # 执行入口，返回 GenerationOutcome
  stream_session.py    # provider 流式读取和 usage
  tool_loop.py         # 工具循环编排
  outcome_builder.py   # content blocks/result/tool digest
  retry.py             # 同一 ContextSnapshot 下的 provider retry
```

`GenerationOutcome`：

```text
result_parts
usage
tool_digest
accumulated_content
accumulated_blocks
model_id
status
```

约束：

- 执行器不写 task/messages/conversation revision。
- 执行器可持续写 task 的 accumulated 内容，但写入必须带 token 条件。
- provider retry 复用同一 ContextSnapshot 和 execution token。
- Worker 负责调用 commit/fail RPC 和外部投递。

## 11. 摘要与缓存

- 摘要只读取闭合 revision，不读取 pending/running task。
- `summary_revision` 不得超过当前 conversation revision。
- serial task 完成后可触发摘要更新；branch 完成同样按 closed revision 更新。
- v2 Redis 闭合历史缓存继续精确匹配 revision + through-message。
- commit 后无需同步双写缓存；下一 revision 自动 cache miss。

## 12. 边界场景

| 场景 | 处理策略 |
|---|---|
| 用户快速连续发送 | 全部持久化为 pending，按 queue_sequence 串行 |
| 相同请求自动重试 | message_generation_requests 重放同一 task |
| Redis 不可用 | API 仍 enqueue；Worker DB 扫描兜底 |
| DB enqueue 失败 | 不返回已接受；幂等请求记录失败并释放 task slot |
| Worker claim 后崩溃 | 租约过期，新 token 重新认领 |
| 旧 Worker 恢复 | token 不匹配，所有写入和 commit 被拒绝 |
| Provider 超时 | 同 attempt 内按现有策略重试；ContextSnapshot 不变 |
| 多次 Provider 失败 | fail RPC 进入稳定终态并释放 owner |
| pending 取消 | 不调用模型，直接 cancelled |
| running 取消 | 取消信号 + token 失效，禁止 completed |
| commit 成功但 WS 失败 | DB 已完成；刷新/恢复读取事实状态 |
| 企微投递失败 | Turn 不回滚，独立幂等重试投递 |
| branch 与 serial 同时完成 | reply_to 保持归属，revision 按 commit 顺序 |
| 分支大量并发 | 独立 branch 配额 + 全局任务槽位 |
| 积分不足发生在排队后 | claim 后执行前校验；失败终态，不阻塞后续 |
| 无 ContextAnchor 的历史 task | 仅恢复为 interrupted/failed，不进入新 Actor 重跑 |
| 服务滚动部署 | 老版本停止认领，新版本扫描数据库继续 |

## 13. 连锁修改清单

| 改动点 | 影响文件/模块 | 同步内容 |
|---|---|---|
| Actor 字段/RPC | migration 121/122 + rollback | enqueue、claim、renew、commit、fail、cancel |
| Task 构造 | `base.py`、`turn_binding.py` | pending 入库，不提前绑定 |
| 协调器 | 新增 `conversation_execution.py` | RPC 类型化出口 |
| Worker | 新增 `conversation_worker.py` | 扫描、认领、续约、唤醒 |
| 原子完成 | 新增 `turn_finalization.py` | commit/fail 后外部事件 |
| Chat 拆分 | `chat_handler.py` + `handlers/chat/*` | 纯执行器、Outcome |
| Web API | `message.py`、generation helpers | enqueue 后立即返回 |
| task API | `task.py`、schemas | queued/running 恢复与取消 |
| WebSocket | builders/types/frontend routing | 可选 queued/claimed 事件 |
| 前端 | task restoration/message item | 排队文案与取消 |
| 企业微信 | message service/turn lifecycle/gateway | 持久 enqueue 与主动投递 |
| 恢复 | `task_recovery.py` | 过期租约重新认领 |
| 摘要 | summary manager | 只处理闭合 revision |
| 测试 | backend/frontend | 状态机、并发、崩溃、恢复、投递 |
| 文档 | overview/index/issues/architecture | 新模块和取代关系 |

## 14. 架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 生命周期从 Handler 移至 Actor/Worker | 中 | Outcome 接口隔离 |
| 数据流 | DB 成为队列和执行权 SSOT | 低 | Redis 仅通知 |
| 扩展性 | 10 倍流量下按索引认领 | 中 | SKIP LOCKED、批量扫描上限 |
| 耦合度 | Web/企微统一，减少双链路 | 中 | delivery adapter 解耦 |
| 一致性 | 核心完成改为单 RPC | 低 | token + 幂等结果 |
| 可观测性 | 新增队列和租约指标 | 低 | 统一日志与告警 |
| 可回滚性 | 数据库为兼容新增 | 中 | feature flag、分阶段切流 |

最高风险是原子完成 RPC 涉及积分与消息写入。实施时必须先建立与现有积分逻辑等价的
数据库测试，不能在缺少真实 schema 验证时切换生产。

## 15. 方案对比结论

| 方案 | 正确性 | 用户体验 | 改动 | 恢复 |
|---|---|---|---|---|
| 忙时 409 | 强 | 差 | 小 | 简单 |
| Redis 长锁队列 | 中 | 好 | 中 | 有双执行风险 |
| DB Actor + fencing + 原子完成 | 强 | 好 | 大 | 完整 |

采用第三种。它是唯一同时满足“不丢请求、可恢复、可并行分支、无双提交”的方案。

## 16. 实施与验收

可观测性、部署回滚、任务拆分和完整测试矩阵见
`TECH_Conversation_Actor实施与验收附录.md`。主设计与附录共同构成本架构的实施基线。
