# AR-13 Command Claim 与 Coordinator 骨架

## 1. 范围

AR-13 将 pending Session Command 的扫描、领取、Run 创建、续租与终态统一放入
PostgreSQL。Coordinator 仅负责编排这些 RPC；本阶段不接 Provider、Action Executor、
Projection、Ingress、旧 Conversation Actor 或生产 startup。

Redis 只允许作为缩短下一次扫描等待的提示。提示丢失或 Redis 不可用时，Coordinator
仍按固定周期扫描 PostgreSQL。

## 2. 持久化合同

`agent_session_commands` 是 pending Command 的唯一事实源。
`agent_command_claims` 以 `command_id` 为主键，保存：

- Session、组织、用户及 Runtime Scope 身份；
- `worker_id`、`fencing_token`、lease 与 attempt_number；
- 唯一 Run 身份；
- claimed/completed/failed/attempts_exhausted 生命周期；
- finished_at、outcome 与 error_class 恢复事实。

表启用并强制 RLS，只存在 everydayai_owner policy。Runtime、Worker、WeCom 和 PUBLIC
均无表直权。

## 3. Run envelope

非 cancel Command 必须在提交时携带不可变 `run_envelope`：

```json
{
  "run_envelope": {
    "run_kind": "user",
    "context_receipt": {"revision": "v1"},
    "config_snapshot": {"revision": "v1"},
    "capability_snapshot": {"revision": "v1"},
    "request_identity": {
      "session_id": "<uuid>",
      "idempotency_key": "<stable-key>"
    }
  }
}
```

四类业务字段必须是非空 JSON object，整个 envelope 上限 256 KiB。Session 与
idempotency identity 必须和 Command 行一致。Coordinator 不在 claim 后补写 snapshot。

cancel 使用最小稳定合同：`target_run_id` 为 UUID，`reason` 可选，并携带同样的
run_envelope。目标 Run 已存在时调用既有 `cancel_agent_run`；尚不存在时原子创建
cancelled Run，形成持久取消事实。

## 4. 事务与锁

调用链：

```text
periodic scan
→ lock Session
→ lock Command with SKIP LOCKED
→ recheck pending eligibility
→ validate Scope and envelope
→ lock/upsert CommandClaim
→ create/read UNIQUE(command_id) Run
→ return typed receipt
```

候选扫描本身不建立正确性事实。拿到 Session、Command 锁后再次检查 eligibility，
避免两个 Worker 等待同一 Session 后重复选择已经领取的 Command。稳定锁序为
Session → Command → CommandClaim → Run。

已有 `result_entity_id` 时必须在 Run 锁下复核关联和状态：

- queued 与 lease 已过期的 running 可领取；running 的执行接管仍只能调用既有
  `claim_agent_run` 签发新 token 和 RunAttempt；
- 有效 lease 的 running 不进入候选，避免第二执行 Owner；
- waiting_actions、waiting_interaction、paused 已完成本条 Command 的推进，不重新
  执行 handler，分别等待 Action、Interaction 或显式 resume；
- completed、failed、cancelled 不再签发 active CommandClaim；
- waiting/paused/terminal 建立非 active 回执并返回 `already_processed`，后续扫描
  不再重复选择，也不修改 Run 或追加事件；
- Run 缺失，或 Command、Session、org/user 关联不一致时返回
  `association_rejected` 并持久失败关闭，禁止创建替代 Run。

cancel 在排序中优先于普通 pending Command。Run 插入依靠现有
`UNIQUE(command_id)`；冲突后读取并核对 envelope hash，冲突失败关闭。

Run `request_hash` 与 migration 213 的 `create_agent_run` 使用同一规范对象：
`command_id`、`run_kind`、`context_receipt`、`config_snapshot` 和
`capability_snapshot`。`request_identity` 只校验 Command envelope，不进入 Run hash，
因此旧、新两条合法创建路径可交叉 readback。

219 仅在实际插入新 Run 时追加一次 `run.created`。cancel-before-start 先创建 queued
Run 并追加 `run.created`，再原子推进 cancelled 并追加 `run.cancelled`；两个事件均通过
`append_agent_runtime_event` 产生连续 sequence 和 web_runtime/audit outbox。

Command claim 达到最大 attempts 时，在同一 Session → Command → CommandClaim → Run
锁序事务中把非终态 Run 推进为 failed，清除 lease/token、关闭未结束 RunAttempt，
依次追加 `run.failed` 与 `command.attempts_exhausted`。已终态 Run 返回
`terminal_conflict`，不会被反向覆盖。

## 5. RPC 与结果

Worker-only SECURITY DEFINER RPC：

- `claim_pending_agent_command_and_ensure_run`
- `get_agent_command_run_claim`
- `renew_agent_command_claim`
- `finish_agent_command_claim`

结果使用明确 outcome，包括 claimed/found/not_found/already_claimed、
ownership_lost、lease_expired、attempts_exhausted、scope_rejected、
idempotency_conflict、terminal_conflict、already_processed、
association_rejected、renewed、completed 与 failed。

连接在提交后断开时，adapter 先以本次唯一 worker identity 找回最近 claim，再使用
`command_id + worker_id` 精确 readback；不会解析异常文本，也不会把不确定提交当作
普通 retry。

## 6. Coordinator

`RuntimeCoordinator` 一次只处理一个 Command：

1. PostgreSQL claim/恢复；
2. 后台续租；
3. handler 完成后写入 claim 终态；
4. lease 丢失时取消 handler，禁止旧 token 写终态；
5. shutdown 停止后续扫描并终止续租；
6. 可选 wakeup 失败后回退到 PostgreSQL 定时扫描。

进程内不保存恢复所需事实；重启通过已过期的 PostgreSQL claim 重新 fencing 领取。

## 7. 回滚

完整身份按以下顺序应用：

1. `219_01_agent_runtime_command_claim_foundation.sql`
2. `219_02_agent_runtime_command_claim_lifecycle.sql`
3. `219_02a_agent_runtime_command_claim_terminal_compatibility.sql`
4. `219_sync_wecom_employee_capability_access.sql`

回滚按精确身份反序。lifecycle 先删除 RPC；foundation 在存在 claim 事实时抛出
`AGENT_COMMAND_CLAIM_ROLLBACK_FACTS_PRESENT`，禁止破坏性删除。

## 8. 验证边界

真实 PostgreSQL 测试覆盖 Run 唯一性、多 Worker 并发、续租、过期重领、旧 token
fencing、attempt exhaustion durable event、Scope fail-closed、cancel 优先及目标
不存在的取消事实、历史 queued/running/waiting/paused/terminal Run eligibility、
错误关联、RLS/FORCE RLS、权限矩阵，以及 apply/rollback/reapply。

adapter/Coordinator 单元测试覆盖严格 typed receipt、提交响应丢失 readback、
Redis 不可用回退和 lease-lost 停止写入。生产接线与完整 Model/Action 循环留给后续
已规划阶段。
