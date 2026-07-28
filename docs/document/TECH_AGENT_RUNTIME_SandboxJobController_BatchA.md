# Agent Runtime Sandbox Job Controller Batch A

## 边界

Batch A只建立持久 Sandbox Job 事实、窄 RPC 和类型化 PostgreSQL Port。它不启动
Worker、不运行代码、不注册 `code_execute` Executor，也不接 startup、ingress、
Workspace materialize 或生产 Owner。既有 ToolLoop Sandbox 行为不变。

## 权威身份与数据

`agent_sandbox_jobs` 一行绑定一个 Action、ActionAttempt 和 DispatchIntent：

- `external_idempotency_key` 全局唯一；相同完整 binding 返回原 Job，不同 binding
  返回 `idempotency_conflict` 且不修改事实。
- binding 包含 Action/Attempt/DispatchIntent、request hash、Executor/runtime
  revision、Workspace scope、代码 SHA-256、输入 manifest 和资源限制。
- 代码正文继续使用不可变 Action 参数，Job 仅保存
  `agent-action:<action_id>:arguments.code` 引用和 SHA-256。
- 输出 manifest 只冻结内容寻址对象合同；路径 materialize 与 OSS 均为后续派生。
- stdout/stderr 各只保留最多 8 KiB 脱敏摘要、原始长度、SHA-256 和截断标记。
- partial effect 只保存脱敏 manifest。清理 deadline 不超过记录时间后 24 小时；
  清理未证明完成时保持 `unknown`。

表启用 `RLS` 与 `FORCE RLS`，只有 `everydayai_owner` policy；应用角色没有表权限。
receipt、manifest 和 evidence 拒绝代码/prompt/路径/文件名/凭证/异常正文等字段及
明显敏感值；manifest 与 evidence 使用字段 allowlist，负向扫描仅为第二道防线。
stdout/stderr 的业务脱敏仍必须由 Batch B Worker 在提交前完成，数据库再拒绝常见
凭证、JWT、长编码串和宿主路径模式。

## 状态与恢复

```text
prepared → queued → claimed → starting → running
                      │          │          │
                      └──────────┴──────────┴→ failed|timed_out|unknown
queued|claimed|starting|running → cancel_requested
cancel_requested → succeeded|failed|timed_out|cancelled|unknown
unknown → reconciliation claim → terminal|still_unknown
```

- create 与入队在同一 RPC 事务内完成。
- create 对 DispatchIntent 全局唯一键再取得事务级 advisory lock，跨 Action 竞态
  也只会返回同一 Job 或 `idempotency_conflict`。
- claim 生成随机 token，递增 fencing token 和 state version。
- 只有尚未进入 `starting`、无产物/partial/cancel accepted 事实的过期 `claimed`
  Job 可安全回到 `queued`；`starting/running/cancel_requested` 过期转 `unknown`。
- renew、start、finish、unknown 都要求当前 claim token、fencing token 和版本。
- cancel requested/accepted 不等于 cancelled；仅记录完整进程树终止证明后，未来
  Worker 才能提交已启动 Job 的 `cancelled`。仍为 queued 且无 claim/start/partial
  事实的 Job 由数据库原子写 `CANCELLED_BEFORE_START`，证明从未创建进程树。
- terminal 首次写入唯一；相同 status/receipt hash 可幂等 readback，其他 late
  receipt 返回 `terminal_conflict`。数据库以确定性 `jsonb::text` 的 SHA-256
  重新计算并验证 receipt hash；queued cancel 也生成规范 receipt，不复用 request hash。
- `unknown` 只能由独立 reconciliation token/lease 处理，不能普通重派。
- reconciliation 不能替换已冻结的 partial manifest；存在 partial 时，必须先由
  cleanup RPC 持久化 allowlisted proof 并达到 `completed`，才能写 terminal。
- execution 或 reconciliation lease 过期后，late finish/unknown/cancel/cleanup
  一律失败关闭；Runtime readback 不返回 Worker token、owner 或 ambiguity evidence。

## 锁序与 RPC

所有按 Job 操作的 RPC 通过 `_lock_agent_sandbox_job` 使用固定顺序：

```text
Session → Run → Action → ActionAttempt → DispatchIntent → SandboxJob
```

create 在同一顺序下锁定关联事实，再锁外部幂等键命中的 Job。RPC 会重新验证关联、
request hash、DispatchIntent 的 `reconcile_only` 恢复模式及 tenant/user scope。
冲突采用稳定 outcome 或 SQLSTATE，冲突分支零 mutation。

Runtime 仅可执行：

- `create_or_get_sandbox_job`
- `get_sandbox_job`
- `request_sandbox_job_cancel`

专属 `everydayai_sandbox_worker` 仅可执行 get、claim、renew、start、lease recovery、
cancel signal、finish、unknown、reconciliation 和 cleanup RPC。角色由数据库
bootstrap 创建，不在业务 migration 内创建；它 `NOINHERIT`，不继承普通 Worker，
没有 Sandbox 表直权。PUBLIC、WeCom、Sync、普通 Worker 均无 RPC 权限。

## Migration 与回滚

- `222_01_agent_runtime_sandbox_job_foundation.sql`：helper、表、约束、索引和 RLS。
- `222_02_agent_runtime_sandbox_job_rpcs.sql`：窄 RPC、锁序和最小角色授权。
- rollback 使用精确同名 `_rollback.sql`；先回滚 222_02，再回滚 222_01。
- 只要存在任何 Sandbox Job，222_01 rollback 以
  `AGENT_SANDBOX_JOB_ROLLBACK_HAS_FACTS` 失败关闭。

## 后续门禁

Batch B 之前仍需专用临时 Linux VM 上的真实 nsjail 与 cgroup v2 合同证据，包括
宿主文件、网络、DB/Redis/Secret、mount/symlink、进程树和 CPU/内存/磁盘/timeout
隔离。缺能力时必须拒绝领取，禁止裸 Python 降级。Batch B 还需实现唯一 Worker、
job-scoped 进程树、drain 与崩溃 scanner；Batch C 才实现不可变 Workspace 对象、
partial 清理和 materialize。
