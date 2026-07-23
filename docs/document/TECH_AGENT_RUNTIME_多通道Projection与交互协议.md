# Agent Runtime 多通道 Projection 与交互协议

> 状态：总体设计 / 第一轮冻结
> 日期：2026-07-18
> 范围：Runtime 事实如何可靠投影到 Web、企微及未来通道

## 1. 结论

```text
Runtime transaction
  ├─ State / Message / Artifact
  └─ RuntimeEvent + Projection Outbox
          -> Projector
          -> Web Snapshot/Replay + WebSocket
          -> WeCom Delivery Outbox
          -> Channel-specific UI
```

核心规则：

1. WebSocket 不是事实源，断线后从数据库 Snapshot + Replay 恢复。
2. Command、Event、Snapshot 分开；发送 Command 不代表执行成功。
3. 同一 Run 的 durable event 使用单调 sequence 和单一排序点。
4. `stream.closed` 只是流体验结束；只有 `message.committed/run.terminal` 是业务终态。
5. UI 不从自然语言猜测 Action、Artifact、Goal、SubRun 或 Interaction 状态。
6. 高频 delta 可合并或丢弃，Action/Interaction/Artifact/terminal 不可丢。
7. Web、企微共用事实协议，通过 ChannelCapability 做展示和交互降级。
8. 不展示模型隐藏思维链，只展示标准化 activity、依据和结果。

## 2. 项目上下文

### 2.1 现状

项目已有 ContentPart 判别联合、前端 Zod 校验、WebSocket 重连、Redis 跨 Worker广播、Actor accumulated checkpoint、数据库终态后的 `message_done`、企微 Transactional Outbox 和逐项投递 checkpoint。结构化图片、视频、文件、图表已不依赖文本 URL marker。这些基础保留。

当前 WS 信封缺少 protocol version、event ID、sequence、run/action/interaction/artifact ID、causation、durability 和 aggregate version。`last_index/current_index` 已失效；前端按到达顺序拼 chunk，无法去重或发现缺口。`stream_end` 早于数据库终态却会结束 UI streaming。危险工具确认是进程内 60 秒 Event，前端只有一个全局 confirm 状态。

### 2.2 可复用

- ContentPart 与各专业 Renderer。
- ActorWebSink accumulated snapshot。
- WebSocket subscription/reconnect。
- Redis best-effort live relay。
- `conversation_deliveries` 企微 Outbox。
- Message/Task/Artifact 持久数据。
- RuntimeEvent 与 Projection Outbox 数据库设计。

### 2.3 约束和冲突

- 旧客户端必须在迁移窗口继续消费 `message_chunk/message_done`。
- 同一会话可存在多个并行 Run/Interaction。
- 企微不支持完整富 UI 和持续双向交互。
- 消息展示顺序和 Action 完成顺序不同。
- signed URL 不能作为 Artifact identity。
- 前端 optimistic message 不能与终态 Snapshot 重复合并。

## 3. Command、Event、Snapshot

Command：

```text
runtime.subscribe
interaction.answer
run.cancel
run.steer
goal.pause/resume
artifact.retry_delivery
```

Event：

```text
run.created/started/waiting/completed/failed/cancelled
stream.delta/checkpoint/closed
activity.updated
action.planned/authorized/started/accepted/progress/
       completed/failed/cancelled/unknown
interaction.requested/answered/expired/cancelled
artifact.created/ready/failed/superseded
goal.updated
subrun.spawned/progress/completed
usage.updated/settlement.completed
message.committed
```

Snapshot：某个 Run 在 sequence N 的完整用户投影，包括 Run、Actions、Interactions、Artifacts、Goal/SubRuns、累计文本和 terminal。

Command 使用现在时意图，Event 使用过去式事实；Snapshot 不混入尚未提交的临时推断。

## 4. RuntimeEvent Envelope

```json
{
  "protocol_version": 1,
  "schema_version": 1,
  "event_id": "uuidv7",
  "sequence": 42,
  "event_type": "action.completed",
  "durability": "durable",
  "org_id": "uuid",
  "conversation_id": "uuid",
  "turn_id": "uuid",
  "run_id": "uuid",
  "aggregate": {
    "type": "action",
    "id": "uuid",
    "version": 3
  },
  "correlation_id": "uuid",
  "causation_id": "uuid",
  "occurred_at": "timestamp",
  "payload": {}
}
```

不变量：

- event ID 全局幂等。
- sequence 在 Run stream 内严格递增。
- aggregate version 用于 Projection CAS。
- org 从认证和事实记录派生，不信任客户端。
- payload 由 `event_type + schema_version` 封闭校验。
- 所有需要全序的事件从数据库 Event Writer 分配 sequence。
- 并行 Action 用 aggregate version 表达内部版本，不假设 completion 顺序。

## 5. Durability

| 类别 | 示例 | 处理 |
|---|---|---|
| `durable` | terminal、Interaction、Artifact ready | Event + State + Outbox |
| `checkpoint` | 累计文本、完整 tool input、阶段状态 | 覆盖保存，可重建 |
| `ephemeral` | token delta、spinner、平滑 progress | 合并发送，可丢 |
| `internal` | Policy trace、Provider raw | 审计，不投用户 |

Tool arguments delta 不持久；完整 canonical Action input 才是恢复事实。原始 thinking 不发送，映射为 `activity.updated{code,display_text}`。

## 6. Subscribe、Replay 与缺口恢复

客户端：

```json
{
  "type": "runtime.subscribe",
  "payload": {
    "run_id": "uuid",
    "after_sequence": 41,
    "projection_version": 1
  }
}
```

服务端：

- `contiguous`：从 42 replay，再切 live。
- `compacted`：发送 sequence N Snapshot，再从 N+1 replay。
- `terminal`：最终 Snapshot/terminal，不建立无意义 live。
- `forbidden/not_found`：typed error。

订阅先登记 live buffer，再读取 Snapshot/Event，最后按 sequence 合并，消除“查完历史、加入 live 前”的丢失窗口。

前端每 Run 保存：

```text
last_sequence
seen_event_ids LRU
projection_version
connection_epoch
resync_state
```

- `sequence <= last_sequence`：忽略。
- `sequence == last_sequence + 1`：应用。
- `sequence > last_sequence + 1`：暂停增量，触发 replay/snapshot。
- terminal Snapshot 覆盖 ephemeral/optimistic 状态。

`event_id` 负责重复，sequence 负责缺口，两者不能互相替代。

## 7. 流式参数与背压

| 参数 | 初值 |
|---|---:|
| 服务端 delta 合并窗口 | 10–20 ms |
| 单包文本上限 | 4 KiB |
| 前端 render 合并窗口 | 16 ms |
| activity/progress 最快频率 | 1 Hz |
| SubRun progress | 2 秒 |
| checkpoint | 1 秒或 20 chunk |
| seen event LRU | 每 Run 512 |
| 单连接活跃 Run | 32 |
| 单连接待发送软上限 | 1 MiB |
| hard 上限 | 4 MiB |

队列满：

- delta 合并/丢中间项，保留最新 checkpoint；
- progress last-write-wins；
- terminal、Interaction、Artifact 永不丢；
- 超 hard limit 先发 `resync_required`，再断开；
- 慢客户端不能反压数据库 Worker。

## 8. Projection 模型

前端不再只有 Message Store，目标读模型：

```text
ConversationProjection
  ├─ Messages
  ├─ Runs
  │   ├─ Activities
  │   ├─ Actions
  │   ├─ Interactions
  │   ├─ Artifacts
  │   ├─ Goal
  │   └─ SubRuns
  └─ Deliveries
```

Message 只负责用户/Assistant 可见内容。Action 卡展示计划、执行、等待、失败、Unknown 和 retry eligibility；Artifact 卡展示产物；Interaction 卡可恢复并回答；Goal/SubRun 有独立状态。

Projection reducer 必须是纯函数：

```text
reduce(snapshot|event, projection) -> projection
```

同一个 Fixture 可用于后端投影、前端 reducer 和 replay 测试。

## 9. Interaction

持久字段：

```text
interaction_id / run_id / action_id
kind: permission | question | plan_approval | form
status: open | resolved | expired | cancelled
request_payload / allowed_responses
required_actor / requested_by
deadline_at
answered_by / answer / answered_at
policy_snapshot_ref / version
```

回答：

1. 客户端提交 ID、expected version、answer。
2. 后端校验身份、org、required actor、状态、deadline 和 schema。
3. CAS first-valid-answer-wins。
4. 同事务写 Grant/RuntimeEvent/wake Outbox。
5. 任意 Worker 继续 Run。
6. 重复回答返回当前状态，不重复执行。

浏览器倒计时只展示，过期以数据库时间为准。

Web 可展示多个 Interaction card。企微根据 ChannelCapability：

- 支持按钮/表单时映射原交互；
- 不支持时发送带短码的文本指令；
- 高风险且通道无法可靠认证回答人时，要求回 Web；
- 回答仍进入同一 Interaction CAS。

## 10. ChannelCapability

```json
{
  "channel": "wecom_bot",
  "revision": 2,
  "supports": {
    "text_stream": true,
    "image": true,
    "video": true,
    "file": true,
    "chart": false,
    "diagram": false,
    "form": false,
    "interaction": "text_reply",
    "message_update": true
  },
  "limits": {
    "text_chars": 4000,
    "attachments": 9
  }
}
```

Channel Adapter 只负责 Projection，不改变 Runtime 事实：

| Artifact | Web | 企微降级 |
|---|---|---|
| Image/Video/File | 专业组件 | 原生媒体/文件 |
| Chart | ECharts | 格式化 JSON/表格 |
| Diagram | Mermaid | DSL/文本说明 |
| Form | 表单 | 编号问题或回 Web |
| Goal/SubRun | 状态卡 | 摘要+状态更新 |
| Unknown Action | 对账卡 | 状态文本+通知 |

执行前可以根据 capability 告知用户交付降级，但不能因通道不支持富展示就丢 Artifact。

## 11. Message 与终态

目标语义：

- `stream.delta`：体验文本。
- `stream.checkpoint`：当前累计可恢复文本。
- `stream.closed`：Provider 流不再产生 delta。
- `message.committed`：消息已在数据库提交。
- `run.completed/failed/cancelled`：Run 事实终态。

前端不得在 `stream.closed` 标记 completed。只有 committed/terminal 或最终 Snapshot 可以结束业务等待。

Message ContentPart 引用 Artifact ID；签名 URL 是 Delivery 属性，可刷新。旧 `message_done` 第一阶段由新 `message.committed` Adapter 生成。

## 12. 安全与隐私

- 不向客户端发送隐藏 CoT、完整 Policy trace、Secret、Provider raw response。
- activity 使用平台 code 和短文本，不转发模型 analysis。
- Tool 参数按 ToolPolicyMetadata 脱敏。
- 群聊 Projection 不展示个人 Memory、余额或私有 Artifact。
- Snapshot/Replay 同样执行 org/user/channel scope，不能因为历史事件绕权。
- 下载 URL 短期签名，Artifact identity 保留稳定 ID。
- 企微 delivery payload 不持久化临时 access token。

## 13. 边界场景

| 场景 | 处理 |
|---|---|
| 重复 Redis event | event ID/sequence 幂等忽略 |
| 乱序到达 | gap recovery |
| Snapshot 后旧 delta 到达 | sequence 忽略 |
| terminal 后 progress | aggregate version/CAS 拒绝 |
| 多 Interaction 并行 | 独立 card，ID/version 回答 |
| 多端同时回答 | first valid CAS wins |
| 企微投递成功、checkpoint 前崩溃 | at-least-once，外部标识/审计 |
| Web 离线期间 Action 完成 | Replay/Snapshot 恢复 |
| Artifact ready、Message 未提交 | Artifact 可恢复，Projection 等事务/Outbox |
| 前端协议版本旧 | server adapter 或要求刷新 |
| 慢连接 | resync required，不阻塞 Worker |
| Channel 不支持类型 | 确定性降级，不丢事实 |

## 14. 方案与影响

| 方案 | 结论 |
|---|---|
| 继续按 WS 到达顺序拼接 | 无去重/缺口恢复，不采用 |
| 所有 WS 都持久化 | 高频写放大，不采用 |
| RuntimeEvent + Snapshot/Replay + Ephemeral delta | 推荐 |

| 维度 | 风险 | 应对 |
|---|---|---|
| 前后端协议迁移 | 中 | 旧/new Adapter 双发 |
| 事件写放大 | 中 | delta 不落、checkpoint 合并 |
| 多通道差异 | 中 | ChannelCapability |
| Interaction 安全 | 高 | DB CAS + required actor |
| 回滚 | 低 | 旧 WS 保留窗口 |

## 15. 计划文件与迁移

计划路径：

- `agent_runtime/projection/event_projector.py`
- `agent_runtime/projection/snapshot_builder.py`
- `agent_runtime/projection/channel_capabilities.py`
- `agent_runtime/interactions/commands.py`
- `api/routes/runtime.py`
- `frontend/src/runtime/projectionReducer.ts`
- `frontend/src/runtime/runtimeStore.ts`
- `frontend/src/runtime/components/`
- WebSocket v1 adapter、企微 adapter

迁移：

1. 新事件 shadow write，不改变旧 WS。
2. 建 Snapshot/Replay API 和 sequence 对账。
3. 前端只读 reducer shadow 比较旧 Message 状态。
4. 先切 terminal、Artifact，再切 Action/Interaction。
5. 切 delta/checkpoint 和 gap recovery。
6. 企微从 Runtime Projection Outbox 消费。
7. 删除 `last_index/current_index` 和进程内 confirm。
8. 稳定一个回滚窗口后退出旧事件。

验收：

- 断线任意时点恢复到同一 terminal Projection。
- duplicate/reorder/gap Fixture 结果一致。
- stream.closed 不提前完成。
- 多个 Interaction 不覆盖。
- Web/企微对同一 Artifact 保留同一 identity。
- terminal/Interaction/Artifact 不因背压丢失。
- 企微降级是确定性的。
- 旧客户端迁移期行为不破坏。
