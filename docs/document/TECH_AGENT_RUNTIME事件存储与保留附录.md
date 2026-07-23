# Agent Runtime 事件存储与保留附录

> 状态：总体设计第三阶段，待方案评审
> 日期：2026-07-18
> 主文档：数据库模型与数据库 RPC
> 本文范围：RuntimeEvent 表、事件信封、Projection Outbox、Snapshot、索引和保留

## 1. `agent_runtime_events`

字段：

| 字段 | 类型/约束 |
|---|---|
| id | UUID PK DEFAULT gen_random_uuid() |
| session_id | UUID NOT NULL FK session RESTRICT |
| sequence | BIGINT NOT NULL CHECK >0 |
| org_id | UUID NULL |
| event_type | TEXT NOT NULL |
| event_version | INTEGER NOT NULL DEFAULT 1 CHECK >0 |
| durability | TEXT CHECK durable/ephemeral_compacted |
| run_id/model_step_id/action_id/interaction_id/goal_id | UUID NULL |
| causation_event_id | UUID NULL FK event SET NULL |
| correlation_id | UUID NOT NULL |
| actor_type | TEXT CHECK user/system/model/executor/reconciler/admin |
| actor_id | TEXT NULL |
| payload | JSONB NOT NULL DEFAULT '{}' |
| payload_hash | TEXT NOT NULL |
| occurred_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() |
| trace_id/span_id | TEXT NULL |
| redaction_revision | TEXT NOT NULL |

约束：

- `UNIQUE(session_id, sequence)`。
- payload 是 object，RPC 最大 256 KB。
- 至少一个实体关联，Session lifecycle 事件除外。
- payload_hash 基于规范化且脱敏后的 payload。

## 2. 事件分类

Durable lifecycle：

```text
session.created / command.accepted
run.created / claimed / waiting / resumed / completed / failed / cancelled
model_step.created / completed / failed
action.requested / policy_decided / claimed / accepted / unknown
action.completed / failed / rejected / cancelled
interaction.opened / resolved / expired / cancelled
goal.created / activated / paused / blocked / completed / budget_exhausted
artifact.created / linked
cost.reserved / confirmed / refunded
projection.requested
```

可合并流事件：

```text
model.text_delta
model.thinking_delta
action.progress
```

terminal、Policy、费用、授权和外部 receipt 永远 durable，不得压缩成只剩 UI 文本。

## 3. 流事件合并

Worker 内按：

```text
同 session + run + step + event_type
250ms 或 4KB，先到者 flush
单 payload 最大 32KB
```

flush 后仍获得 sequence。断线导致最后缓冲丢失时，最终 ModelStep receipt 和 Artifact 是
事实；UI 可少最后一小段过程动画，但不能缺终态。

## 4. Projection Outbox

`agent_projection_outbox`：

```text
id UUID PK
event_id UUID NOT NULL
session_id UUID NOT NULL
projection_kind TEXT CHECK web_message/web_runtime/wecom/audit/search
status TEXT CHECK pending/processing/delivered/dead
attempt_count INTEGER DEFAULT 0
next_attempt_at TIMESTAMPTZ DEFAULT NOW()
lease_token UUID NULL
lease_expires_at TIMESTAMPTZ NULL
checkpoint JSONB DEFAULT '{}'
last_error_code TEXT NULL
created_at/updated_at/delivered_at TIMESTAMPTZ
UNIQUE(event_id, projection_kind)
```

不是每个事件都生成每种 Projection；业务 RPC 按 routing policy 插入。Web 实时发送失败
不把 outbox 直接 dead，Snapshot/Replay 仍可恢复。

## 5. Snapshot

`agent_session_snapshots`：

```text
id, session_id, org_id,
through_sequence BIGINT,
snapshot_version INTEGER,
state JSONB,
state_hash TEXT,
created_at
UNIQUE(session_id, through_sequence)
```

state 只包含：

- active/nonterminal Run/Action/Interaction/Goal 摘要。
- latest Message/Artifact projection refs。
- continuation owner。
- protocol/catalog/config revisions。

不复制完整历史正文。客户端恢复：

```text
GET snapshot
→ apply through_sequence
→ replay events where sequence > through_sequence
→ live subscribe from last sequence
```

生成条件初值：

- 每 200 个 durable events。
- 任一 Goal/Run terminal。
- Snapshot 超过 1 MB 时拒绝写入并告警。

## 6. 索引

`agent_runtime_events`：

- unique `(session_id, sequence)`。
- `(run_id, sequence) WHERE run_id IS NOT NULL`。
- `(action_id, sequence) WHERE action_id IS NOT NULL`。
- `(org_id, occurred_at DESC)`。
- BRIN `(occurred_at)`。
- `(event_type, occurred_at DESC)` 仅用于运维，避免为每种类型建索引。

Outbox：

- `(status,next_attempt_at,created_at) WHERE pending/processing`。
- `(lease_expires_at) WHERE processing`。

Snapshot：

- `(session_id, through_sequence DESC)`。

## 7. 分区决策

首期不分区，原因：

- `(session_id,sequence)` 全局唯一约束简单可靠。
- 当前真实 event 规模未知。
- 月分区会使跨月 Session replay 和唯一约束复杂化。

触发重新评估任一条件：

- 事件达到 5000 万行。
- 主表超过 100 GB。
- vacuum/index maintenance 连续超过 SLO。
- 30 天热查询 P95 超过 200ms 且索引/归档不能解决。

届时优先方案：按 hash(session_id) 分 32 个 partition，保持单 Session 局部性和唯一键可
包含 partition key；不是按月 range。

## 8. 保留与归档

| 数据 | 热存 | 归档/删除 |
|---|---:|---|
| lifecycle durable event | 1 年 | OSS 压缩归档，按合规期限 |
| text/thinking delta | 30 天 | 合并 transcript receipt 后删除/归档 |
| action progress | 90 天 | terminal receipt 后归档 |
| Snapshot | 最近 10 个/Session | 旧 snapshot 删除 |
| callback inbox | 30 天 | applied/rejected 脱敏归档 |
| projection outbox | delivered 30 天 | 删除；dead 保留 180 天 |
| audit/cost/auth event | 按审计策略 | 不随普通事件提前删除 |

删除必须基于 durable watermark：对应 Session Snapshot/Artifact/terminal receipt 已存在，
且所有需要的 Projection 已越过 sequence。

## 9. 隐私与内容 Gate

默认禁止 payload 包含：

- 完整 Prompt/回复。
- Tool 大结果。
- 文件正文。
- Provider secret/token。
- 企微敏感标识明文。

Event 保存 hash、长度、类型、Artifact ID 和受控摘要。调试内容采集需要组织级 opt-in、
短 TTL、显式 redaction revision，并且 Policy 只能关闭不能被远程配置打开。

## 10. Replay 不变量

1. sequence 对同 Session 严格递增且无重复。
2. 客户端按 sequence 去重，不按 occurred_at 排序。
3. Snapshot through_sequence 对应已提交状态。
4. durable event 不可更新；纠错追加 correction event。
5. Projection checkpoint 不能大于已处理 event sequence。
6. 事件 schema 未知时客户端保留 cursor、跳过展示，不中断整个恢复。
7. terminal event 重放多次结果相同。
8. Event Store 不作为余额或权限的唯一读模型。

## 11. 初始容量估算

假设：

```text
10 万 Run/日
平均 3 ModelStep
平均 2 Action
平均 20 durable/compacted event
≈ 200 万 event/日
```

若平均行 1 KB，约 2 GB/日（未计索引），因此内容 gate 和 delta 合并是硬要求。上线前必须
用现有消息量校准；若实际仅其十分之一，不提前引入分区和独立 Event 服务。

每个 Tool Turn 的结构化写放大约为：

```text
1 ModelStep + N Action + N Attempt + N Result
+ 5~20 durable/compacted event
```

Run/Action/Interaction/Goal 状态长期保留；Model request/response 原文默认不落库，只保存
receipt/hash。必要调试必须经过显式内容 gate。

## 12. 数据模型风险与冻结项

| 风险 | 等级 | 控制 |
|---|---:|---|
| 表数量多 | 中 | 按聚合边界，不为每个 Tool 建表 |
| 多租户冗余 org_id 漂移 | 高 | 所有写入 RPC 校验父子 scope |
| 新旧双写不一致 | 高 | mapping + shadow validator |
| JSONB 失控 | 高 | 类型 CHECK、RPC size、Artifact 外置 |
| Event 写放大 | 中 | delta 合并、BRIN、归档 |
| 多态 Artifact link 无 FK | 中 | RPC-only write、审计修复 |

建议冻结：

- 新增 `agent_*` 表，不改变旧 task status 语义。
- 状态表 + append-only event，不做纯 Event Sourcing。
- Session 一对一扩展 conversation。
- 每个 Tool Call 都有 Action。
- Artifact 大内容外置。
- Usage ledger 与积分余额事实分离。
- 初期不做 event partition，达到实测阈值再迁移。
