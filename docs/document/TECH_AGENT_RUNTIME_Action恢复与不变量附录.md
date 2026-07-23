# Agent Runtime Action 恢复与不变量附录

> 状态：总体设计第二阶段，待方案评审
> 日期：2026-07-18
> 主文档：`TECH_AGENT_RUNTIME核心状态机.md`
> 本文范围：ActionAttempt、重试、幂等、终态所有权、恢复、映射和不变量

## 1. ActionAttempt

### 1.1 AttemptStatus

```text
claimed
dispatching
accepted
completed
failed
unknown
cancelled
```

ActionAttempt 保存：

```text
attempt_number
executor_id / executor_revision
idempotency_key
execution_token / lease
provider_request_id / external_task_id
request_hash
started_at / accepted_at / ended_at
error_class / retry_disposition
```

### 1.2 重试分类

| disposition | 行为 |
|---|---|
| retry_safe | 同 Action 创建下一 Attempt |
| retry_after_reconcile | 先查询外部结果 |
| retry_requires_user | 创建 Interaction |
| non_retryable | Action failed |
| compensate | 先创建显式 Compensation Action |

只有 `retry_safe` 可以自动创建下一 Attempt。HTTP 超时不自动等于 retry_safe；外部动作
必须根据 idempotency capability 判断。

### 1.3 幂等键

建议：

```text
action_id + logical_operation_version
```

多次 Attempt 使用同一个业务 idempotency key；Provider 若不支持，则本地保存 request
fingerprint 和 external receipt，并默认采用 reconcile-first。

## 2. 单终态所有权

### 2.1 Owner 规则

| 事实 | 唯一 owner |
|---|---|
| Run terminal | Run Coordinator RPC |
| ModelStep terminal | Model Runtime commit |
| Action terminal | Action Coordinator/Reconciler RPC |
| Cost reservation/settlement | Cost Ledger transaction |
| Artifact canonical record | Artifact Service |
| Message/ContentPart | Projection |
| WeCom delivered/dead | Delivery Worker |

Executor 只能返回/提交结果，不能直接把 Run、消息和积分分别改终态。

### 2.2 原子提交顺序

同步 Action：

```text
validate result
→ persist Artifact
→ settle cost
→ Action terminal + RuntimeEvent
→ Run wake event
```

Run completion：

```text
verify no blockers
→ persist final Artifact/result
→ Run terminal + RuntimeEvent + projection outbox
```

如果不能放在一个数据库事务中，使用 Transactional Outbox；禁止先发 WS 再写事实。

## 3. 恢复与 Reconciliation

### 3.1 Run 恢复

- running 且 lease 未过期：其他 Worker 不接管。
- running 且 lease 过期：创建新 RunAttempt，状态不回退。
- waiting_actions：由 Action terminal event 唤醒。
- waiting_interaction：由 Interaction resolution 唤醒。
- paused：只能显式 resume 或受控系统策略恢复。
- terminal：重复 command 返回 receipt。

### 3.2 Action 恢复

扫描候选：

```text
queued without live lease
running with expired lease
accepted past next_reconcile_at
unknown past next_reconcile_at
```

建议初始参数：

| 参数 | 初值 |
|---|---:|
| inline Action lease | 90s |
| external submit lease | 60s |
| accepted 首次 reconcile | 30s |
| reconcile backoff | 30s, 2m, 10m, 30m |
| 自动 reconcile 最长窗口 | 24h |
| unknown 人工告警阈值 | 30m |

Provider callback 到达时按 `provider + external_task_id` 幂等关联。callback 早于 accepted
提交时先进入 callback inbox，等待 Action 关联，不丢弃。

## 4. 当前状态映射

| 当前对象 | 当前状态 | 目标映射 |
|---|---|---|
| Chat task pending | pending | Run queued |
| Chat task running | running | Run running + RunAttempt |
| Chat task completed/failed/cancelled | 同名 | Run terminal |
| Tool step running | running | Action running 的 Projection |
| 媒体 task pending before submit | pending | Action queued/running |
| 媒体 task provider accepted | pending/running | Action accepted |
| 媒体 task completed/failed | 同名 | Action completed/failed |
| Message pending/completed/failed | 同名 | Message Projection |
| Outbox pending/delivering/delivered/dead | 同名 | Delivery Projection |

迁移期 `tasks` 继续存在，新增 runtime ID 外键映射；不能直接改变旧 status 含义。

## 5. 不变量

必须由数据库约束/RPC和状态机测试共同保证：

1. 一个 Run 同时最多一个有效 RunAttempt lease。
2. 一个 Action 同时最多一个有效 ActionAttempt lease。
3. state_version 单调递增。
4. 终态不可逆。
5. completed Action 必须有 ActionResult。
6. accepted/unknown 必须有 external receipt 或 ambiguity evidence。
7. rejected Action 不得存在已 dispatch Attempt。
8. cancelled Run 不阻止其 accepted/unknown Action 后台对账。
9. Run completed 时 blocking Action 和 Interaction 数为零。
10. 同一 Action 的 Cost settlement 至多一次。
11. Projection 失败不能回滚业务终态。
12. stale fencing token 不能写 progress、terminal 或费用。

## 6. 边界场景

| 场景 | 状态结果 |
|---|---|
| 同一消息重复入队 | 返回原 Run，不创建第二个 |
| 多段提示词生成 4 张图 | 1 Run + 4 Action，各自独立 Attempt |
| 第 2 张失败 | 其 Action failed，其余可完成；模型决定总结/补偿 |
| Provider 接收后 HTTP 超时 | Action unknown，不盲重试 |
| 用户此时取消 | Run cancelled，Action 保持 unknown 并对账 |
| 回调随后成功 | Action completed，标记 completed_after_run_cancel |
| Policy deny 一个工具 | Action rejected，结果回模型可换方案 |
| 用户确认超时 | Interaction expired；Action rejected/cancelled，Run pause |
| Worker 在提交终态前崩溃 | 新 Attempt 根据 receipt/reconcile 推进 |
| Artifact 上传成功、DB 前崩溃 | 通过 content hash 幂等关联，不重复 Provider |
| Goal Run 完成 | Run completed，Goal Verifier 决定是否继续 |

## 7. 架构风险

| 风险 | 等级 | 控制 |
|---|---:|---|
| 旧 task 与新 Run/Action 双写漂移 | 高 | shadow validator、单 owner、checksum |
| Run cancel 后 Action 后完成引发用户困惑 | 中 | 明确 UI 标签和通知策略 |
| unknown 长期堆积 | 高 | reconcile SLA、人工队列、Provider contract |
| 状态数量增加开发复杂度 | 中 | 闭合 reducer、禁止业务层直接 UPDATE |
| 事件与状态非原子 | 高 | 同事务 RuntimeEvent/Outbox |
| Action 粒度过细写放大 | 中 | chunk 合并只用于流事件，不省略 Action |

状态机本身存在高实施风险，但没有未决架构分歧；实现前仍需数据库 RPC 方案和多角色评审
共同确认，当前不进入编码。
