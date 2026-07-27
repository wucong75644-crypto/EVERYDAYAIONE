# AR-11 ModelAttempt 与唯一计费结算

## 范围

AR-11 在 AR-08/09/10 基础上增加 ModelAttempt 持久化、unknown/reconcile、
非工具终态、取消后的迟到 receipt，以及按 ModelStep 唯一的积分结算。
本阶段只提供 domain、port、PostgreSQL RPC/Repository 和 Model adapter 的窄扩展，
不接入生产 Coordinator，不切换事务 Owner。

## 生命周期

允许的 ModelAttempt 状态迁移：

- `prepared -> dispatching | failed | cancelled`
- `dispatching -> completed | failed | unknown | cancelled`
- `unknown -> completed | failed | cancelled | unknown`
- `completed | failed | cancelled` 无生命周期出边

`failed-before-dispatch` 表示为 `status=failed` 且
`dispatch_phase=prepared`。只有它或 Provider 给出的类型化安全证据可以标记
`retry_safe`；新 Attempt 复用 request hash 并递增 attempt number。

## 原子边界

Provider I/O 位于数据库事务之外：

1. `prepare_model_attempt` 持久化 Attempt 并按 ModelStep 唯一预留积分。
2. `start_model_attempt_dispatch` 在网络请求前提交 request-start。
3. 首个响应块先调用 response-start observer；observer 失败立即停止消费并返回
   typed unknown，不重新 dispatch。
4. `final/structured_final` 在一个 RPC 中完成 Attempt、ModelStep、usage、
   settlement 和 runtime event。
5. Tool Calls 只返回 `handoff_tool_calls`，不改变 Attempt、ModelStep 或积分。
6. 明确失败原子提交 Attempt/ModelStep；安全的 dispatch 前失败只终结 Attempt，
   保留 ModelStep 与原预留供新 Attempt 使用。

所有 mutation 使用固定锁序：
Session → Run → ModelStep → ModelAttempt → settlement，并校验 Run lease、
Run/Attempt fencing token、state version、request hash 和 worker scope。

## unknown、reconcile 与迟到 receipt

429、502/503/504、timeout、断流在没有类型化安全证据时不重试。
默认 unknown 为 `reconcile_only`；Provider 无 readback 能力时为 `forbidden`。
reconcile claim 产生独立 token，resolve 必须同时持有有效 Run token 与
reconciliation token。公开 terminal RPC 与 reconcile 共用 owner-only terminal
helper；helper 分别校验 Run token 与 Attempt ownership token，任何非终态结果
都不会预写或替换 reconciliation token。

Run 取消会释放预留并将活动 Attempt/ModelStep 取消。迟到 receipt 只能追加
provider request id、response receipt/hash、usage、ambiguity evidence 和
late outcome，不复活任何生命周期实体、不发布用户终态、不重新调用 Provider。

## 唯一计费

`agent_model_credit_settlements.model_step_id` 唯一：

- reserve：扣减余额、写入负向 `credits_history`，并写入既有
  `credit_transactions` pending lock。
- settle：确认 lock，按实际用量返还差额并写入既有 ledger。
- cancel/failure：通过既有原子退款 RPC 释放预留。
- late adjustment：只能由 `record_late_model_receipt` 调用 owner-only 内部
  adjustment helper；迟到 receipt 的 outcome、provider request id、response
  receipt/hash、usage、ambiguity evidence 与 actual credits 共同构成完整重放身份。
  adjustment pending 还要求稳定 adjustment key、金额与 response hash 全部一致，
  才能在余额恢复后继续；任一事实不同均返回冲突且零 mutation。
- unknown 本身不结算。

每次余额变化与对应 `credits_history` 位于同一数据库事务；reservation 记录负向
`conversation_cost`，settlement/cancel 记录合法退款类型，late adjustment 记录
负向 `conversation_cost`。幂等重放不新增 history。

普通 terminal 重放同样按持久事实判等：completed 比较 response
receipt/hash、stop/provider reason、usage、actual credits、effective attempt 与
settlement key/amount；failed 比较 error code、retry disposition 和 ModelStep
终态事实。只有完全一致才返回 `already_completed` / `already_failed`，否则
`terminal_conflict` 且 Attempt、ModelStep、settlement、event/outbox 和余额流水
均不变化。

## 迁移与回滚

实际词法 apply 顺序：

1. `217_01_agent_runtime_model_attempt_foundation.sql`
2. `217_02_agent_runtime_model_attempt_credits.sql`
3. `217_03_agent_runtime_model_attempt_lifecycle.sql`
4. `217_04_agent_runtime_model_attempt_reconciliation.sql`

rollback 严格按 04 → 03 → 02 → 01。存在 Attempt 或 settlement 业务事实时，
destructive rollback 失败关闭。两张新增表启用并强制 RLS，业务角色无表直权，
worker 只获得窄 RPC 的 EXECUTE。

## 集成边界

AR-11 不修改共享 `domain/transitions.py`、PostgreSQL `parsing.py`、共享
`__init__.py` 或索引文档。Model adapter 只依赖 observer port，不依赖
PostgreSQL Repository；每次 `complete` 最多一次 `stream_chat` dispatch。
生产 Coordinator、Action/Tool Calls 终态和 Owner 切换由后续任务完成。
