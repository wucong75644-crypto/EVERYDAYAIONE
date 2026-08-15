# Agent Runtime Owner 调用图（S3-R/S4-A）

更新时间：2026-08-12

## 结论

当前主线已经为 Runtime-owned 任务提供独立的提交、claim、ActionLoop、事实恢复和 WeCom delivery/reconcile 路径；但历史 scheduled task 仍允许在没有 Runtime execution profile 时由 legacy scheduler 执行。两条路径按数据库事实分流，不是同一任务的并行副作用 Owner。

因此当前可以验证“Runtime profile 任务没有回退到旧 Owner”，但不能宣称“所有历史任务已经完成 Owner 切换”。后者需要为 profileless 历史任务建立可回滚的 adoption/data migration 合同，不能通过删除 Python 旧类替代。

## 入口与 Owner

| 入口 | 判定 | 实际 Owner |
|---|---|---|
| Web Runtime ingress | `message_chat_preparation` 调用 `RuntimeIngress(require_runtime_owner=True)` → `runtime_submit_ingress_v6_required`; Runtime gate/command 失败时 fail-closed 隔离 prepared task，不恢复旧 Actor | Runtime command claim → ModelLoop/ActionLoop |
| WeCom ingress（文本/语音/图片/混合） | `services/wecom/wecom_ingress_mixin.py` 统一调用 Runtime facade；`services/wecom/actor_enqueue.py` 固定调用 `enqueue_wecom_runtime_turn_v6` | Runtime；定义事实、RPC 或 owner readback 不可用即 fail-closed |
| WeCom 文件 ingress | `services/wecom/wecom_ingress_mixin.py` 先暂存附件，再进入唯一 Runtime facade | Runtime；不可用时失败关闭，文件暂存语义由 `aa583e40` 保留 |
| 定时任务手动执行 | 有 `runtime_action_id` 时调用 `request_agent_runtime_scheduled_execution_v1`；无该事实时保留旧 route | Runtime submission 或 legacy executor |
| 定时扫描 | `worker_claim_due_scheduled_executions_v1` 返回 `owner_kind=runtime` 时 Scanner 不再启动旧 Executor；`owner_kind=legacy` 才进入 `ScheduledTaskExecutor` | Runtime submission 或 legacy scheduler |
| Runtime scheduled command | Runtime Worker 领取 command，后续由 Runtime ActionLoop 和 scheduled finalizer 收敛 | Runtime |
| Scheduled WeCom delivery | `ScheduledRuntimeWecomWorker` 处理 prepared/readback/reconcile facts；普通 delivery worker 只属于既有 transport/outbox 兼容链 | Runtime delivery 或 legacy transport，不能混同为 Action Owner |
| Conversation Actor | `conversation_actor_worker_enabled` 为 false 时拒绝启动；开启时仍构造 `ChatGenerationExecutor` | 历史 Conversation Actor，尚未完成 Owner cutover |

## 已验证的防旁路条件

- Runtime profile 存在时，`worker_assert_scheduled_task_legacy_owner_v1` 拒绝旧 Owner。
- Runtime claim 返回 `_execution_owner=runtime` 时，Scanner 不创建 `ScheduledTaskExecutor`。
- Runtime 手动执行要求稳定 `Idempotency-Key`，Owner readback 不是 legacy fallback。
- Runtime scheduled submission、terminal finalization、WeCom dispatch/readback/reconcile 均有 request/version/fence 合同。
- `UNKNOWN`/`ACCEPTED` 只进入 readback/reconcile，不允许普通重派。
- Runtime、legacy worker 和 WeCom worker 的生产 flags 当前保持关闭。

## 仍未完成的 Owner 收敛

1. 盘点生产/历史 `scheduled_tasks` 中没有 `agent_runtime_scheduled_execution_profiles` 的任务。
2. 决定这些历史任务是继续兼容，还是通过可回滚 adoption 为每项创建 Runtime profile。
3. 只有 adoption 证据完整后，才能关闭 `ScheduledTaskAgent`、`ScheduledTaskExecutor` 和 Scanner 的 legacy 分支。
4. Conversation Actor 需要单独完成 ingress drain、旧任务 drain 和 Runtime claim gate；不能因为 Runtime ingress 代码存在就删除 Actor。
5. Web/WeCom 关闭历史入口前，必须验证生产 flags、部署版本和任务事实，不在本地代码批次中假设生产已切换。

## 下一批

S4-B 先做本地可验证的 Owner gate/adoption preflight：只读列出 profileless task、拒绝不完整 adoption、验证 Runtime profile 任务不回退，并保持历史兼容执行可用。它不删除旧 Owner、不执行生产数据迁移、不改变生产开关。
