# Agent Runtime Sandbox Job Controller 与专业 Executor

## 边界

222 migration 建立持久 Sandbox Job 事实与窄 RPC；后续代码在不接 production
startup/ingress 的前提下增加独立 Worker、受限 Capability、内容寻址 Workspace
对象和 `code_execute` 专业 Executor。API Backend 与 Conversation Actor 不再启动
旧 Kernel Owner，旧 ToolLoop `code_execute` 明确返回
`CODE_EXECUTE_REQUIRES_ACTION_RUNTIME`，不能直接执行代码。

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
Worker 不持久化 stdout/stderr 的用户可控正文；摘要固定为空，仅保留原始长度、
SHA-256与截断标记。数据库继续作为第二层拒绝边界。

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
- `222_03_agent_runtime_sandbox_job_recovery_rpcs.sql`：完整binding readback、
  可证明未启动Job的execution recovery scanner，以及不重派的reconciliation
  scanner。
- rollback 使用精确同名 `_rollback.sql`；按 222_03、222_02、222_01 逆序回滚。
  222_03存在非终态Job时失败关闭；仅终态历史允许回滚且仍由222_02读取。
- 只要存在任何 Sandbox Job，222_01 rollback 以
  `AGENT_SANDBOX_JOB_ROLLBACK_HAS_FACTS` 失败关闭。

## Worker、Capability 与 Workspace

- `SandboxJobWorker` 只消费 `everydayai_sandbox_worker` scoped Job Port。领取前运行
  Linux、nsjail、cgroup v2 controller 探针，任一缺失时不领取。
- 每个 Job 使用唯一进程组和 nsjail invocation；started 后失败或身份不确定写
  `unknown`，不重派。cancel accepted 后仍需进程组不存在的证明才能写 cancelled。
- Worker 周期性 query Job 并续租；cancel、terminal 与 lease/version 竞争均通过
  222 RPC fencing。unknown 仅走 launcher query 与 reconciliation RPC。
- Executor 只获得 attempt-scoped `SandboxJobCapability`，不能取得数据库连接、
  Workspace 根路径、Redis、Secret Provider、OSS 凭证或任意网络客户端。
- Capability由ActionLoop在Dispatch Gate成功后调用受信Issuer签发；Executor不能选择
  TTL、operation或obligation。dispatch与reconcile capability集合分离。
- Action 代码先按 Action ID 与 SHA-256 写入不可变输入对象；输入 Artifact 按
  allowlist、稳定引用、size 与 hash 复核后进入只读 input mount。
- 输入staging按Action/Attempt隔离；已知create拒绝或readback not_found立即精确
  清理，submit结果不明时保留到readback，terminal前必须证明清理成功。
- Job 只能写专属 output mount。成功输出逐文件拒绝 symlink/hardlink/traversal，
  再原子写入 `workspace-object:sha256:<digest>`；重复 materialize 返回同一对象。
- 失败、超时和取消输出进入普通浏览不可见的 quarantine。数据库 deadline 最大
  24 小时；清理不能证明完成时立即记录稳定错误码告警，并保持 unknown 等待处理。
- materialize/quarantine后、删除临时输出前，Worker先在受限Workspace写入按
  `sandbox_job_id`定位的无正文terminal checkpoint（终态、receipt及hash）。
  数据库terminal提交成功后才删除checkpoint；清理或提交窗口崩溃时，新Worker由
  PostgreSQL领取reconcile后使用checkpoint恢复，不依赖进程内handle。
- 若checkpoint含partial而数据库仍为空，reconciler必须先通过222_03窄RPC在相同
  reconciliation token/version下冻结完整manifest；相同事实幂等，任何差异冲突。
  只有冻结readback成功后才允许清理quarantine。

## 三层验证门禁

1. macOS 本地只验证状态机、PostgreSQL、Capability、Workspace、Executor、Owner
   退出和隔离能力缺失时 fail-closed；Stub 不证明 Linux 隔离。
2. 远程合同使用手动触发、无生产 Secret 的托管临时 Ubuntu runner。Job 只检出候选
   commit，构建固定 nsjail，验证 namespace、seccomp、cgroup v2、mount、默认无网络、资源
   限制、进程树终止与零残留后自动销毁。workflow 的提交、推送和执行留待最终集成
   一次批准；托管 runner 权限不足只记录环境缺口。
3. production composition 获批后，在真实服务器运行只读能力探针；nsjail 版本、
   cgroup controllers、namespace/seccomp、mount 和 Worker 最小权限任一不满足，
   `code_execute` 保持关闭。

真实 Linux 合同尚未执行前，不得宣称生产隔离安全，不接 startup/ingress，也不得
部署或启用 `code_execute`。
