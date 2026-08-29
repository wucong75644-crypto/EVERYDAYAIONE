# 定时任务可靠投递

## 目标

定时任务执行成功不能以 Redis 发布成功或 WS 当时在线作为判断条件。执行结果、
推送目标和待投递状态必须先成为数据库事实；企微恢复连接后仍能继续投递。

## 运行链路

`ScheduledTaskExecutor` 在 `complete_scheduled_task_success` RPC 内同时：

1. 完成 `scheduled_task_runs` 和任务下次调度状态；
2. 冻结每个企微目标与结果内容，写入 `scheduled_task_deliveries`；
3. 把运行记录置为 `queued`，而不是伪称 `pushed`。

`everydayai-wecom` 里的 `ScheduledTaskDeliveryWorker` 以数据库租约领取投递。
WS 未连接、发送超时和发送错误会指数退避重试；达到尝试上限进入 `dead`。
投递成功才把运行记录收敛为 `pushed`。多目标分别记录，最终状态可为
`pushed`、`partial` 或 `push_failed`。

企微协议未提供可用于本系统的最终业务回执，因此语义是“至少一次交给企微
连接发送”；连接在发送后断开时允许极低概率重复，绝不静默丢弃。

## 并发与兼容

- 周期扫描继续使用 `claim_due_tasks`。
- “立即执行”通过 `claim_scheduled_task_now` 原子领取，和扫描器互斥。
- 手动执行暂停或异常任务后，成功和失败都会恢复原有暂停/异常调度状态。
- 一次性任务恢复不读取空的 `cron_expr`；以 `run_at`（过期时立即）重新领取。
- Redis 直推仍服务其他历史 best-effort 通道，但定时任务结果和失败告警不再经过它。

## 发布与回滚

先执行 `242_scheduled_task_delivery_outbox.sql`，再重启后端和
`everydayai-wecom`。回滚只应在尚不需要保留投递审计数据时执行对应 rollback；
否则保留 Outbox 数据并回滚应用代码会使待投递记录无人消费，因此不允许这样做。

## 验证

- WS 未连接时记录转入 `retry_scheduled`，恢复后才能 `delivered`；
- 进程重启不丢失 pending/delivering（租约过期）记录；
- 同一运行和目标只能有一条 Outbox 记录；
- 立即执行与扫描器竞争时只有一个领取者；
- 一次性任务恢复不调用 cron 计算。
