# Agent Runtime AR-14～AR-16 授权恢复与 Dispatch Gate

## 决策与边界

唯一执行链为 `ActionExecutorResolver → gate_agent_action_dispatch → ExecutorPort`。
Registry 是 action kind 到 descriptor/Executor 的唯一 SSOT。批准只创建 Grant；Worker
重新求值 Policy 并持久化 PolicyReceipt 后才把 Action 激活为 queued。数据库 gate
提交即取得本 Attempt 的执行所有权；外部副作用不与数据库伪装成原子事务，而由
fencing token、一次 GrantUse 和持久 DispatchIntent 收敛。

## 状态合同

| awaiting_authorization 输入 | Action | terminal reason | Interaction/Grant | Run |
|---|---|---|---|---|
| approve | 保持 awaiting_authorization；receipt 激活后 queued | 无 | resolved + active Grant | 等待授权，激活后统一重算 |
| deny | rejected | authorization_denied | resolved，无 Grant | blocker 精确减一并重算 |
| expire | rejected | authorization_expired | expired | blocker 精确减一并重算 |
| revoke（gate 前） | rejected | authorization_revoked | Grant revoked | claimed Attempt cancelled、token/lease 清空，blocker 减一 |
| cancel | cancelled | Run cancel reason | Interaction cancelled、Grant revoked | Run 保持 cancelled |

gate 的 `grant_revoked`、`grant_expired`、`grant_replay_conflict`、
`receipt_expired`、scope/revision 冲突属于永久拒绝，并在同事务关闭尚未取得 gate
所有权的 Attempt/Action；ownership/stale/数据库异常不被转换为 rejected。

## 原子 gate 与恢复

`gate_agent_action_dispatch(attempt_id, execution_token, expected_version,
request_hash, receipt_id, executor_type, executor_revision, policy_revision,
recovery_mode)`按 Session→Run→Action→ActionAttempt→Interaction→Grant→GrantUse→
PolicyReceipt→DispatchIntent 加锁并重新校验 scope、hash、revision、有效期和状态。
成功时一次写 GrantUse、DispatchIntent，并将 Attempt 置 dispatching；相同绑定重复调用
返回原 intent，不同绑定失败关闭。Action grant 只能用于绑定 Action；workflow grant
必须显式，且每个 Action 各有唯一 GrantUse。

gate 后调用前崩溃，过期 dispatching+intent 转 unknown 后只进入 reconcile。调用后、
结果前崩溃同样依赖 Executor query/idempotency 能力 reconcile；旧 execution 或
reconciliation token 均不得提交。Registry 拒绝“有副作用且既无幂等也无查询恢复”
Executor。revoke/expire 在 gate 提交后只阻止未来 gate，不撤销当前 Attempt。

## 数据库与权限

220_24 增加 owner-only、ENABLE/FORCE RLS 的
`agent_action_dispatch_intents`及 Worker 专属 gate/readback，并撤销 Worker 对旧
`mark_agent_action_dispatching`的执行权。220_25增加 Worker 专属授权恢复 RPC，
Runtime/WeCom 仅保留 resolve/revoke/cancel 窄能力。所有 SECURITY DEFINER 函数固定
`search_path = pg_catalog, public`；PUBLIC、Sync 和普通数据库角色无执行权或直表权限。

rollback 必须按 220_25→220_24 逆序执行；存在 Interaction/Grant/Receipt/GrantUse 或
DispatchIntent 业务事实时失败关闭。生产 composition 前仍需独立解决 Projection
Outbox dead stream 恢复 P1，并完成 startup/ingress 与专业 Executor 接线。
