# Agent Runtime 数据库 RPC 与原子边界

> 状态：总体设计第三阶段，待方案评审
> 日期：2026-07-18
> 主文档：`TECH_AGENT_RUNTIME数据库模型.md`
> 本文范围：RPC、锁顺序、状态 CAS、Event/Outbox、回调 Inbox、迁移兼容

## 1. 原则

应用层不得直接 UPDATE Runtime 状态。所有状态推进通过 SECURITY INVOKER RPC：

```text
validate input
→ lock roots in canonical order
→ verify tenant/scope
→ verify status + state_version + fencing token
→ write state/result/cost
→ allocate sequence
→ append RuntimeEvent
→ append projection wake/outbox
→ commit
```

RPC 返回闭合 `outcome`，重复请求返回 receipt，不依赖异常文本判断幂等。

## 2. 锁顺序

统一：

```text
agent_runtime_sessions
→ agent_goals
→ agent_runs
→ agent_model_steps
→ agent_actions
→ agent_interactions
→ credit_transactions/users
→ messages/tasks compatibility rows
```

同层多行按 UUID 升序锁。Artifact 先按 content hash 幂等创建，再在聚合事务中关联；不在
持有 Session 锁时上传 OSS。

## 3. Session 与 Command RPC

### `ensure_agent_runtime_session`

输入：

```text
conversation_id, org_id, scope_kind, scope_id,
created_by_user_id, agent_definition_id/revision
```

行为：一对一创建；已存在则严格比较 scope，返回 `created/already_exists/conflict`。

### `claim_session_continuation`

输入：

```text
session_id, expected_state_version,
owner, source_id, source_version, lease_id
```

唯一键由 `session + owner + source + source_version` 的 command 先去重。只有 owner=none
或同一 receipt 可成功。返回 `claimed/already_claimed/busy/stale_version`。

### `release_session_continuation`

必须匹配 lease_id；清空 owner/source/lease，追加事件。旧 lease 返回 `ownership_lost`。

## 4. Run RPC

### `create_agent_run`

一次事务：

1. 校验 Session/Goal/parent scope。
2. 按 `(session_id,idempotency_key)` 幂等。
3. 创建 queued Run，冻结 config/capability/context receipt。
4. 兼容模式创建 legacy mapping。
5. 追加 `run.created`。

Run 大 JSON 限 256 KB；input 正文使用 message/artifact 引用。

### `claim_agent_run`

参数：

```text
run_id? / session_id?
lease_seconds DEFAULT 90 CHECK 15..300
max_attempts DEFAULT 3 CHECK 1..20
worker_id
```

使用 `FOR UPDATE SKIP LOCKED`。queued 或 running+expired 可 claim；expired 不先回退 queued。
创建 RunAttempt、递增 attempt、签发 token。达到上限不在 claim 中直接 failed，返回
`attempts_exhausted`，由恢复器调用 fail RPC，避免扫描器隐式决定业务终态。

### `renew_agent_run`

匹配 run_id/token/running/live lease；续租并可提交 bounded progress receipt。进度 JSON
最大 64 KB。

### `set_agent_run_waiting`

输入目标 waiting_actions/waiting_interaction/paused、expected counts/version/token。
锁 Run 后重新 count 实际 blockers，禁止调用方伪造计数。

### `wake_agent_run`

只允许：

- waiting_actions 且 blocking active Action=0。
- waiting_interaction 且 open Interaction=0。
- paused 且有合法 resume command。

转 queued，不直接 running。

### `complete_agent_run`

锁序：

```text
Session → Goal? → Run → latest ModelStep → blocking Actions/Interactions
→ Artifact/result → compatibility message/task
```

校验 Completion Gate、无 blocker、token/version。原子：

- Run completed。
- RunAttempt completed。
- final Artifact link。
- legacy message/task 投影（迁移阶段）。
- RuntimeEvent + projection outbox。

重复相同 `result_hash` 返回 `already_completed`；不同 hash 返回 `terminal_conflict`。

### `fail_agent_run` / `cancel_agent_run`

fail 需要 token 或系统 recovery grant。cancel 需要用户/Goal scope，先使 token 失效，再将
未 dispatch Action 取消；accepted/unknown Action 保留并安排 reconcile。

## 5. ModelStep RPC

### `create_model_step`

锁 Run；必须 running 且 token 有效。`step_number = max + 1` 在锁内计算。记录模型、Prompt、
Catalog revision 和 request receipt，追加 `model_step.created`。

### `claim/renew_model_step`

一般复用 Run Worker token，不再签发独立 Worker；只有未来独立 Model Worker 才启用 Step
token。首期字段保留 NULL，避免双 lease。

### `complete_model_step`

输入 response receipt、stop reason、usage、Tool Call descriptors。单事务：

1. 完成 Step。
2. Tool Call 按 `action_index` 幂等创建 requested Actions。
3. 写 usage entries。
4. 更新 Run blocker count。
5. 追加 Step/Action events。

不在本 RPC 调 Policy 或 Executor。

### `fail_model_step`

provider attempts 耗尽后完成 Step failed。Run 是否 failed 由 Run Coordinator 再决定。

## 6. Action RPC

### `decide_agent_action`

锁 Run→Action。输入 PolicyDecision receipt：

- allow：requested → queued。
- deny：requested → rejected，并写最小 ActionResult。
- require_interaction：创建 open Interaction，Action → awaiting_authorization，
  Run blocker 增加。

PolicyDecision 自身应存 `agent_policy_decisions`：

```text
id, run_id, action_id, org_id, decision, reason_codes JSONB,
policy_revision, input_hash, evaluated_at
```

### `claim_agent_action`

参数 lease 默认：

- inline/worker `90s`。
- external submit `60s`。

只 claim queued 或 running+expired 且 retry disposition 安全的 Action。创建 Attempt，签发
token。concurrency_key 使用 PostgreSQL advisory xact lock 只辅助 claim；真实冲突仍查询
active Action partial index。

### `mark_action_accepted`

必须匹配 token、request_hash 和 idempotency key。原子写：

- Attempt accepted + external receipt。
- Action accepted/accepted_at/next_reconcile_at。
- Cost reservation reference。
- Run waiting_actions（若本轮无其他可运行工作）。
- RuntimeEvent。

external key unique 冲突时加载已有 Attempt；scope/hash 相同返回幂等，否则安全告警。

### `complete_agent_action`

校验 result schema/hash、Artifact 已存在、费用 settlement receipt。原子：

- ActionResult insert。
- Attempt/Action completed。
- usage/cost link。
- Artifact links。
- Run blocking count 重算。
- `action.completed` + `run.wake_requested`。

不直接创建下一 ModelStep；Wake consumer 先 claim continuation。

### `fail/reject/cancel_agent_action`

都按状态表闭合推进。rejected 不能有 dispatching Attempt。accepted 的 cancel 只有 Provider
确认后才可 cancelled，否则 unknown。

### `mark_action_unknown`

要求 ambiguity evidence 非空，保存 next_reconcile_at/deadline。禁止自动生成下一 Attempt。

### `resolve_unknown_action`

仅 Reconciler/管理员能力可调用，输入外部查询 receipt，推进 completed/failed/cancelled；
人工裁决必须记录 actor、证据和 reason code。

## 7. Interaction 与 Goal RPC

### Interaction

- `create_agent_interaction`：通常由 decide Action/Goal review 同事务创建。
- `resolve_agent_interaction`：校验 responder、schema hash、expires_at 和 CAS；创建 Grant，
  推进 Action/Run 并追加事件。
- `expire_agent_interactions`：`FOR UPDATE SKIP LOCKED` 批量最多 100；按 interaction policy
  reject/pause。
- `cancel_agent_interaction`：Run/Goal cancel 时调用。

### Goal

- `create_agent_goal`：Session partial unique 确保一个非终态 Goal。
- `activate/pause/resume/cancel_goal`：锁 Session→Goal，管理 continuation owner。
- `begin_goal_round`：active Goal 且无 active Run 时创建 Round/Run。
- `record_goal_verdict`：必须绑定 verifier Run receipt；推进 complete/continue/blocked。
- `record_goal_usage`：由 usage entry 汇总，超预算原子 budget_exhausted。

Goal restart recovery 调 `pause_goal(reason=infrastructure)`，不自动 resume。

## 8. Callback Inbox

新增 `agent_callback_inbox`：

```text
id UUID PK
provider TEXT NOT NULL
external_task_id TEXT NOT NULL
event_id TEXT NULL
payload JSONB NOT NULL
payload_hash TEXT NOT NULL
signature_verified BOOLEAN NOT NULL
received_at TIMESTAMPTZ NOT NULL
status TEXT CHECK pending/processing/applied/rejected/dead
action_attempt_id UUID NULL
attempt_count INTEGER DEFAULT 0
next_attempt_at TIMESTAMPTZ DEFAULT NOW()
last_error_code TEXT NULL
```

唯一：

- event_id 非空：`(provider,event_id)`。
- event_id 为空：`(provider,external_task_id,payload_hash)`。

Webhook 首先验证签名并插 Inbox，然后立即 2xx；consumer 再关联 Attempt。这样 callback
早于 `mark_action_accepted` 时不会丢失。

RPC：

- `claim_callback_inbox(batch_size<=100, lease=60s)`。
- `apply_callback_inbox(inbox_id, receipt_hash)`。
- `reject_callback_inbox(reason)`。

Inbox payload 默认保留 30 天；敏感 Provider 按 schema redaction 后存。

## 9. RuntimeEvent 原子追加

内部函数 `append_agent_runtime_event(...)` 只能被业务 RPC 调用：

1. Session row 已锁。
2. 读取并递增 `next_event_sequence`。
3. 插入 event `(session_id,sequence)`。
4. 必要时插 projection outbox。

应用不能自行提供 sequence。一个业务 RPC 可追加多事件，按调用顺序分配连续 sequence。

事件 append 失败必须回滚业务状态；遥测 sink 失败不回滚。

## 10. 返回协议

所有 RPC 返回：

```json
{
  "outcome": "claimed",
  "entity_id": "uuid",
  "state_version": 3,
  "event_sequence": 42,
  "receipt": {}
}
```

闭合 outcome：

```text
created / already_exists / claimed / renewed / transitioned
completed / already_completed / cancelled / already_cancelled
ownership_lost / lease_expired / stale_version
not_ready / busy / attempts_exhausted / terminal_conflict
scope_mismatch / invalid_transition
```

权限/参数错误仍使用 SQLSTATE：

- `22023` 参数无效。
- `42501` scope/权限。
- `P0002` 不存在。
- `23505` 业务冲突。
- `55000` 状态前置条件不满足。

## 11. 事务边界与外部 IO

数据库事务内禁止：

- 调模型/Provider/MCP。
- 上传 OSS。
- 发 WebSocket/企微。
- 等待用户。

正确模式：

```text
claim transaction
→ external IO
→ persist Artifact if needed
→ terminal transaction + event/outbox
→ projection transport
```

外部 IO 返回和 terminal transaction 之间崩溃，由 Attempt receipt、idempotency key、
callback Inbox 和 reconciliation 恢复。

## 12. 兼容迁移

### 阶段 A：Shadow

- 新表/RPC additive。
- 旧 Actor 执行仍是 owner。
- compatibility adapter 写 Run/Step/Action shadow，不写新终态副作用。
- 每次旧 task 终态后比较 status/result hash/usage/cost。

### 阶段 B：Read-only Action canary

- 新 Runtime 成为只读 Tool Action owner。
- legacy tool_step 仅由 Projection 写。
- 失败可按 org flag 回旧 Tool loop。

### 阶段 C：Media

- 旧 media task 继续 Provider 查询事实。
- ActionAttempt 保存 legacy task mapping。
- TaskCompletionService 只调用 `complete_agent_action`，不再分别写消息/积分。
- 稳定后 Runtime Action 成为 terminal owner。

### 阶段 D：Run/Projection

- 新 Run commit 成为 task/message/事件 single owner。
- 旧 Actor RPC 保留 N-1 兼容窗口。
- 最后才 contract 旧字段/函数。

## 13. 回滚

- Schema 全部 additive，不在启用版本删除旧字段。
- 关闭 org runtime flag 后，新输入回旧链。
- 已由新 Runtime 创建的 Run/Action 继续由同版本 Worker drain，不能交给旧链重复执行。
- 回滚应用前先关闭新 claim；等待或暂停 active Run。
- accepted/unknown Action 永远由 Reconciler 继续处理。
- event/projection 可停止消费，不删除事实。
- down migration 只允许在从未启用生产流量且表为空时执行；有生产事实后使用正向兼容，
  不 DROP 数据表。

## 14. 风险

| 风险 | 等级 | 控制 |
|---|---:|---|
| RPC 数量膨胀 | 中 | 按聚合用例，不做通用 update RPC |
| 锁顺序死锁 | 高 | 固定顺序 + 并发真实 PG 测试 |
| Shadow 写影响旧事务 | 高 | 首期 outbox 异步 shadow，不能拖慢主终态 |
| callback payload 攻击 | 高 | 签名、大小、schema、redaction |
| result 与 cost 非原子 | 高 | terminal RPC 引用已准备 settlement receipt |
| rollback 重复执行 | 高 | 关闭 claim、drain、旧链不得接管现有 Run |

下一附录冻结 RuntimeEvent schema、事件分类、Snapshot/Replay、索引和保留参数。
