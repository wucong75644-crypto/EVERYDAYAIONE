# Agent Runtime S4-B：历史定时任务 Adoption

## 当前边界

历史 `scheduled_tasks` 不是可由字段回填直接切换的旧数据。Runtime profile 必须绑定来源
Action、Attempt、Run、模型/工具集/权限快照、request hash 与租户/Provider/Capability epoch。
没有这些事实的任务继续由 legacy scheduler 执行；删除 `ScheduledTaskAgent` 或放宽 profile
校验都会制造重复副作用或破坏任务语义。

## 第一批：只读 adoption preflight

`227_59_agent_runtime_scheduled_adoption_preflight.sql` 新增 owner-only、read-only RPC：

`read_agent_runtime_scheduled_adoption_plan_v1(org_id, include_inactive)`

返回稳定排序的分类、数量、task semantics hash、delivery target hash 和有限 reason code，
不返回 prompt、push target、Secret、Provider payload，也不写 `scheduled_tasks`、Runtime
Session/Command/Run/Action/Attempt。分类为：

- `runtime_owned`：已有 profile，继续走 Runtime Owner；
- `candidate_runtime_source_required`：active 且任务结构有效，但仍缺 Runtime 来源事实；
- `preserve_paused` / `preserve_error`：保持现状，不自动唤醒或重派；
- `blocked_running`：存在进行中执行，必须先 drain/readback；
- `blocked_partial_runtime_facts`：已有不完整 Runtime 身份，禁止猜测补齐；
- `blocked_invalid_task` / `blocked_unknown_status`：保持 legacy 或 unavailable，等待人工处理。

离线导出使用 `backend/scripts/dry_run_scheduled_runtime_adoption.py`，只读取 JSON 导出，
输出与 SQL preflight 相同的脱敏分类和哈希报告；默认不连接 PostgreSQL。

## 后续真实 adoption 门禁

用户已确认最终方向：所有历史 `scheduled_tasks` 都纳入 Runtime，旧
`ScheduledTaskAgent`/`ToolLoopExecutor` Owner 不作为长期兼容路径保留。

下一批采用全量、失败关闭的 adoption：

- 新增 additive adoption facts，并为每个任务生成不可变 Runtime profile；不新建第二套模型、ERP、文件或查询来源。
- model snapshot、catalog/toolset、scope、budget、Provider/Capability facts 从当前 Runtime 事实和原有任务定义生成。
- 每个任务逐条加锁；任何任务无法生成完整 profile 时，整个 migration 失败，不产生部分切换，也不静默标成功。
- adoption 后 `worker_claim_due_scheduled_executions_v1` 对所有任务只返回 Runtime Owner；旧 Scanner、
  `ScheduledTaskExecutor` 与 legacy RPC 仅保留到 disposable 验收结束，随后删除。
- rollback 只在没有 Runtime submission、Run、delivery 或 projection facts 时允许；一旦产生执行事实，必须失败关闭，
  改走 Runtime reconcile/cancel。

非生产验收覆盖：任务数量和分类、task/payload/delivery hash、Runtime profile/readback、一次完整 scheduled Run、
ActionLoop、finalizer、Projection/Delivery、accepted/unknown/reconcile、旧 Owner 零命中，以及
apply/readback/rollback/reapply。

缺少 Runtime facts、租户/目标不一致或未知状态时，adoption 整体失败；不伪造普通执行历史、不普通重派，
也不允许部分 adoption 后删除旧 Owner。

## 本批：adoption provenance/profile 最小契约（227_60）

本批只建立数据契约，不执行真实历史任务迁移，也不删除 legacy Owner。

- `agent_runtime_scheduled_adoption_provenance` 记录任务来源、adoption request、迁移前状态和脱敏语义/投递哈希。
- `agent_runtime_scheduled_adoption_profiles` 记录 Runtime 所需的 definition、catalog/toolset、model、scope、budget、Provider/Capability 快照。
- 两张表均启用并强制 RLS，身份事实不可更新或删除。
- `adopt_agent_runtime_scheduled_tasks_v1(facts, request_id)` 要求 facts 集合与所有未 adoption 任务精确相等；active、paused、error 都是候选，running、非法状态、运行中 execution、部分 Runtime 身份或缺失事实均整体失败。
- apply 不创建 `agent_runtime_sessions`、`agent_session_commands`、`agent_runs`、`agent_actions` 或 `agent_action_attempts`，因此 adoption 不会伪装成普通 completed 执行历史。
- `read_agent_runtime_scheduled_adoption_v1` 可回读 profile 快照；`rollback_agent_runtime_scheduled_adoption_v1` 仅在没有 Runtime submission/binding/delivery 副作用时删除本批事实。迁移 rollback 另有“事实存在即拒绝”保护。

## Owner 收敛门禁（227_61）

- `agent_runtime_scheduled_adoption_control` 默认是 `pending`；未完成 adoption 时不自动停掉 profileless 历史任务。
- `complete_agent_runtime_scheduled_adoption_v1(request_id)` 只有在每一条 `scheduled_tasks` 都已有 Runtime execution profile 时才允许切换为 `complete`，并提供 control readback。
- 切换完成后，worker 对异常 profileless 任务直接 fail-closed，不再返回 `legacy`；`worker_assert_scheduled_task_legacy_owner_v1` 也拒绝旧 `ScheduledTaskAgent/ToolLoopExecutor`。
- 227_61 rollback 仅允许在 `pending` 状态执行；完成切换后必须通过 Runtime recovery/reconcile 处理，不能回开旧 Owner。

Python service 只负责无 Secret 的事实绑定、候选全集校验和 rollback gate；数据库 RPC 负责最终哈希、状态、快照安全和原子写入。
