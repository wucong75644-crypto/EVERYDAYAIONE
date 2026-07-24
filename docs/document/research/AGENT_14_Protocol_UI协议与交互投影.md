# AGENT 14：Protocol / UI 协议与交互投影

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：运行时事件、流式传输、重放、交互请求、UI Projection、Artifact 展示和通道降级
> 后续专项：Observability / Config、Testing / Operations、端到端链路继续核验

## 1. 结论摘要

Protocol / UI 不是“后端多发几个 WebSocket type”，而是把 Agent Runtime 的事实可靠地
投影成用户可理解、可恢复、可操作的界面。目标链路是：

```text
Runtime state transaction
  ├─ State tables / Message / Artifact
  └─ RuntimeEvent + Outbox
             ↓
       Event Projector
             ↓
   WebSocket / HTTP replay / channel adapter
             ↓
          UI Projection
```

推荐保留当前 `ContentPart[]` 和 WebSocket 基础设施，新增版本化 Runtime Event
信封、每个 Run 的单调序号、可持久交互和 Snapshot + Replay 恢复。高频 token/progress
是可丢弃或可合并的体验数据；Action、Interaction、Artifact、Run terminal 是可重放事实。

最重要的设计原则：

1. UI 不从自然语言猜测工具状态。
2. WebSocket 不是事实源，断线后从数据库 Snapshot 与 Event 续接。
3. 每条可操作请求必须有稳定 `interaction_id` 和持久状态。
4. 不向用户展示模型内部推理；只展示标准化活动、依据和结果。
5. Web、企微和未来 App 共用事实协议，各自使用 Channel Adapter 降级。
6. `message_done` 只能在终态事务提交后发送，`stream_end` 只是体验信号。

## 2. Grok Build 的协议结构

### 2.1 ACP 主协议与 xAI 扩展

Grok 以 ACP `SessionNotification` 作为基础协议，用 xAI extension 增加 Goal、Subagent、
Hook、Compaction、Background Task 和交互状态。标准与扩展事件都绑定 `sessionId`。

事件 metadata 可携带：

```text
eventId
agentTimestampMs
promptId
streamStartMs
turnStartMs
updateType
updateParams
chunkId
isReplay
totalTokens
```

`promptId` 是 Turn 相关键，`eventId` 用于客户端顺序和去重，`isReplay` 告知消费者这是
恢复流而不是新动作。Grok 的 UI 协议不是模型消息原样转发，而是大量明确事件类型。

### 2.2 高频事件与一次性事件分轨

Grok 将事件分为两条发送路径：

- 高频：Agent message chunk、thought chunk、ToolCall arguments delta。
- 低频：Retry、Hook result、Compaction、Goal、Subagent、Turn terminal。

高频事件进入 `ReplayBuffer` 合并并 debounce。默认参数：

| 参数 | 默认值 |
|---|---:|
| `max_items` | 100 |
| `max_bytes` | 2 KiB |
| `max_duration_ms` | 10 ms |

缓冲器只合并同一 Session、同一事件种类和兼容 metadata 的相邻 chunk；跨事件类型时先
flush 旧事件，保证顺序。达到条数、字节或时间阈值立即发送。取消、重连和 Turn 结束前
必须显式 flush，避免最后一段文字滞留。

### 2.3 持久、瞬态和“只持久”事件

Grok 明确区分：

- 持久 + 广播：Agent message、最终 ToolCall、TurnCompleted 等。
- 仅实时：ToolCallDeltaChunk、部分 cosmetic cleanup。
- 只持久：CompactionCheckpoint、RewindMarker。
- fire-and-forget：PendingInteraction、InteractionResolved 等。

Tool arguments delta 不持久；完成后的 canonical ToolCall 才是重放事实。TurnCompleted
是 durable terminal，即使查看者中途重新连接，也能结束“等待中”状态。

这说明“所有 WebSocket 都落库”和“所有 WebSocket 都不落库”都不正确。事件必须按
恢复价值分类。

### 2.4 Event ID 与顺序

Grok 在事件入 FIFO 管道时生成 event ID。源码特别说明：如果某个 mode update 绕过
FIFO 直接发送，它可能得到更大的 ID 却先到达，客户端按序去重后会错误丢弃随后到达的
文本 chunk。因此所有需要全序的事件必须从同一个排序点获得序号。

Grok event ID 能服务单 Session 客户端去重，但本地 JSONL 没有数据库级 aggregate
version。SaaS 目标应使用明确的整数 `sequence`，不要依赖 timestamp 排序。

### 2.5 Tool 与 Run 投影

Grok 对 UI 暴露结构化 ToolCall/ToolCallUpdate，状态和 content 分开。扩展事件还包括：

- `SubagentSpawned`：父 Session、父 Prompt、Child Session、角色、模型、能力模式。
- `SubagentProgress`：耗时、Turn、ToolCall、Token、上下文占用和错误数；约每 2 秒。
- `SubagentFinished`：completed/failed/cancelled、统计、输出和错误。
- `GoalUpdated`：Goal 状态、phase、预算、进度、当前角色、验证状态和暂停原因；进度
  最多约每秒一次。
- `TaskBackgrounded`：ToolCall、Task、command、cwd、output file 和描述。

这些事件允许客户端直接构造状态卡片，不需要解析 Assistant 文本。

### 2.6 阻塞交互

Grok 将权限、询问用户和 Plan 审批统一为 blocking reverse-request：

```text
Permission | Question | PlanApproval
```

进程内 `PendingInteractions` 以稳定 `tool_call_id` 为键；RAII guard 创建时广播 pending，
销毁时幂等移除并广播 resolved。先移除者获胜，达到 first-answer-wins。

但普通 Permission/Question 不持久，仅 Plan Approval 有额外持久 gate。进程崩溃或
Session unload 后，普通等待不能可靠恢复。这个做法适合本地交互，不适合
EVERYDAYAIONE 的多 Worker SaaS。

### 2.7 Grok 的优点与局限

优点：

- 协议事件细且语义明确，Goal/Subagent/Tool 不靠文本猜测。
- 高频合并、一次性直发、持久终态分工明确。
- stable event ID、replay 标记和 terminal 解决中途加入问题。
- ToolCall delta 与 canonical ToolCall 分离。
- UI 收到 SubagentSpawned 后才可能收到 Child 事件，规避映射竞态。

局限：

- 部分交互请求只在内存，恢复语义不足。
- 本地 Session fan-out 不等于多租户可靠 Outbox。
- 事件种类很多，但不是统一业务 Event Envelope。
- UI 协议包含本地 cwd/command 等信息，不能无筛选暴露给 SaaS 用户。
- thought chunk 的存在不代表产品应展示内部思维链。

## 3. EVERYDAYAIONE 当前实现

### 3.1 已有强项

项目已有以下可复用能力：

- 后端 `ContentPart` 判别联合：text、thinking、tool_step、tool_result、form、image、
  video、audio、file、chart、diagram、ecom_plan。
- 前端 Zod 协议边界与不同 ContentPart 专业组件。
- WebSocket task subscription、Redis 跨 Worker广播、自动重连和心跳。
- Actor Web Sink 使用 fencing token，每 20 个文本 chunk 或结构块时保存进度。
- `tasks.accumulated_content/blocks` 支持刷新恢复。
- `message_done` 从数据库终态 Message 格式化后 best-effort 投递。
- 企微 `conversation_deliveries` Transactional Outbox、lease、fencing、逐项检查点。
- 前端 chunk 首包立即渲染，后续以 16 ms 窗口批量更新。
- 文件/图片/图表等结构化产物不再依赖文本 URL marker。

这些能力说明目标不是废弃现有消息系统，而是给它补上统一 Runtime Protocol。

### 3.2 当前 WebSocket 信封

当前基础格式：

```json
{
  "type": "message_chunk",
  "payload": {},
  "timestamp": 0,
  "task_id": "...",
  "conversation_id": "...",
  "message_id": "..."
}
```

已有类型覆盖 message 生命周期、Agent Step、Tool、Form、File、Credits 和连接管理。
问题是信封缺少：

- protocol/schema version。
- event ID。
- 单调 sequence。
- run/action/attempt/interaction/artifact ID。
- correlation/causation。
- durability。
- aggregate version。
- replay/snapshot 元信息。

客户端接口仍有 `last_index`，服务端订阅确认固定返回 `current_index=-1`，注释明确“不再
使用索引”。所以当前“断点续传”实际是 accumulated snapshot，不是有序事件续传。

### 3.3 流式状态

Actor Sink：

1. `message_start`。
2. 每个文本 chunk 立即 WebSocket 发送。
3. 每 20 chunk 持久化全文；结构块立即持久化。
4. flush 后发送 `stream_end`。
5. 数据库原子终态后发送 `message_done`。

前端按到达顺序直接拼接 chunk，没有 event dedupe 或 gap detection。重复 Redis 投递、
网络重连边界或跨 Worker 乱序可能造成重复/缺字。`subscribed.accumulated` 能恢复当时
持久化的全文，但客户端无法知道哪些后来到达的 chunk 已包含在 snapshot 中。

`stream_end` 当前会把消息标记 completed，并结束 streaming；真正的数据库事实
`message_done` 可能尚未到达。它适合作为 `stream.closed` 体验事件，不应等价于
`run.completed` 或 `message.committed`。

### 3.4 Tool 与确认

当前有：

- `tool_call`。
- `tool_result`。
- `content_block_add` 中持久 `tool_step` running/completed/error。
- `tool_confirm_request/response`。

确认等待由 `WebSocketManager._pending_confirms` 进程内 Event 实现，默认超时 60 秒。
请求不是持久实体；处理请求与接收响应必须落在同一进程内映射。重启、Worker 切换、
响应重复或用户多端同时答复都没有数据库 first-answer-wins。

前端只有一个全局 `toolConfirmRequest`，并发两个 Run 或多个确认会覆盖；刷新后请求
消失。它还没有稳定 interaction status、deadline、回答人和 policy decision。

### 3.5 UI Projection

前端当前主要围绕 Message Store：

- streaming message。
- optimistic message。
- content blocks。
- thinking。
- 单一 agent step hint。
- 单一 confirmation modal。

Tool/Action 大部分被压入 Message content，Goal、SubRun、Background Action 和
Interaction 没有独立 Projection。`agent_step` 只转成短提示；复杂运行过程刷新后无法
恢复为同一结构。

### 3.6 多通道

Web 支持富 ContentPart 和交互；企微 Outbox 已具备可靠投递和逐项 checkpoint。企微
发送器按通道支持将内容转换为文本、图片、视频等项目，图表/diagram 已采用文本降级。

当前缺少正式的 Channel Capability Contract。每个新增 Artifact/Interaction 仍可能
在具体发送器中临时决定如何处理，无法在执行前判断“这个通道是否支持确认、预览、
下载或进度更新”。

## 4. 目标协议分层

### 4.1 Command、Event、Snapshot 分开

```text
Command  客户端意图：subscribe、answer、cancel、steer、approve
Event    已发生事实：action.started、artifact.ready、run.completed
Snapshot 某 aggregate 在 sequence N 的完整投影
```

Command 可以被拒绝，不应以发送成功视为执行成功。Event 使用过去式和稳定 schema。
Snapshot 用于首次加载、gap recovery 和 Projection schema 升级。

### 4.2 Runtime Event Envelope

建议 v1 信封：

```json
{
  "protocol_version": 1,
  "event_id": "uuidv7",
  "sequence": 42,
  "event_type": "action.completed",
  "durability": "durable",
  "org_id": "...",
  "conversation_id": "...",
  "turn_id": "...",
  "run_id": "...",
  "aggregate": {"type": "action", "id": "...", "version": 3},
  "correlation_id": "...",
  "causation_id": "...",
  "occurred_at": "...",
  "payload": {}
}
```

约束：

- `event_id` 全局幂等。
- `sequence` 在 Run stream 内严格递增。
- 同一 aggregate 的 `version` 用于 Projection CAS。
- `correlation_id` 串联一次用户 Goal/Turn。
- `causation_id` 指向触发本事件的 Command/Event。
- payload 按 `event_type + protocol_version` 校验，禁止任意 dict 演进。
- org 从认证上下文和事实记录确定，不能信任客户端 payload。

### 4.3 Durability 分类

| 类别 | 示例 | 存储/恢复 |
|---|---|---|
| durable | Run/Action/Interaction terminal、Artifact ready | Event + state + Outbox |
| checkpoint | 文本累计、工具完整输入、阶段进度 | 定期覆盖，可被 Snapshot 重建 |
| ephemeral | token delta、spinner、平滑进度 | 合并发送，可丢弃 |
| internal | Policy trace、原始 Provider 数据 | 审计可见，不直接推用户 |

`thinking_chunk` 不继续作为用户可见原始思维链。目标只发布 `activity.updated`，包含
“正在搜索/分析文件/等待图片”等标准化状态；必要的决策依据写入结果摘要和审计。

### 4.4 建议事件族

```text
run.created / started / waiting / completed / failed / cancelled
stream.delta / stream.checkpoint / stream.closed
action.planned / authorized / started / accepted / progress /
       completed / failed / cancelled / unknown
interaction.requested / answered / expired / cancelled
artifact.created / ready / failed / superseded
goal.updated
subrun.spawned / progress / completed
usage.updated / settlement.completed
message.committed
```

旧 `message_*` 和 `content_block_add` 第一阶段通过 Adapter 双发，前端逐类切换，不要求
一次重写。

## 5. 可靠传输与恢复

### 5.1 唯一排序点

同一 Run 的 durable/checkpoint event 必须在数据库事务或单一 Event Writer 中分配
sequence。不能在不同 Web Worker 内各自用 timestamp 排序。Outbox relay 只负责传输，
不能重新决定顺序。

并行 Action 使用各自 aggregate version；Run stream 记录它们的可观察顺序。UI 不应
假设 Action 完成顺序等于计划顺序。

### 5.2 Subscribe 协议

客户端：

```json
{
  "type": "runtime.subscribe",
  "payload": {"run_id": "...", "after_sequence": 41}
}
```

服务端响应三种情况：

- contiguous：从 42 开始 replay，再切 live。
- compacted：发送 sequence N 的 Snapshot，再从 N+1 replay。
- terminal：发送最终 Snapshot/terminal，不保留无意义 live subscription。

订阅建立过程必须避免“查询历史后、加入 live 前”的窗口丢事件。可使用同一 relay
cursor 或先注册 live buffer、读取 snapshot、再按 sequence 合并。

### 5.3 前端去重与缺口

每个 Run 保存：

```text
last_sequence
seen_event_ids（有界 LRU）
projection_version
connection_epoch
```

- `sequence <= last_sequence`：幂等忽略。
- `sequence == last_sequence + 1`：应用。
- `sequence > last_sequence + 1`：暂停该 Run 增量，触发 replay/snapshot。
- terminal Snapshot 覆盖 ephemeral UI，不能与旧 optimistic block 再拼接。

`event_id` 防止相同序号异常重复；sequence 检测缺口。二者不能互相替代。

### 5.4 流式参数

建议首版：

| 参数 | 建议值 |
|---|---:|
| 服务端文本合并窗口 | 10–20 ms |
| 服务端单包上限 | 4 KiB |
| 前端 render 合并窗口 | 16 ms |
| progress 最快频率 | 1 Hz |
| SubRun progress | 2 秒 |
| stream checkpoint | 1 秒或 20 chunk |
| seen event LRU | 每 Run 512 |

当前 20 chunk checkpoint 可保留，同时增加最长 1 秒时间门限，避免低速输出长期不落盘。
阈值最终应由 Observability 数据校准，不做租户可任意调整的产品配置。

### 5.5 Backpressure

- token delta 合并，队列满时丢中间 delta、保留最新 checkpoint。
- progress 采用 last-write-wins。
- terminal、Interaction、Artifact 绝不因背压丢弃。
- 单连接限制订阅 Run 数和待发送字节数。
- 慢客户端先收到 resync required，再断开；不能无限占内存。

## 6. 可持久 Interaction

目标 Interaction：

```text
interaction_id / run_id / action_id
kind: permission | question | plan_approval | form
status: pending | answered | rejected | expired | cancelled
request_payload / allowed_responses
requested_by / required_actor
deadline_at
answered_by / answer / answered_at
policy_snapshot_ref
version
```

回答流程：

1. 客户端发送 `interaction.answer` Command，带 interaction ID、version 和 answer。
2. 后端重新校验身份、组织、状态、deadline 和 allowed response。
3. 数据库 CAS `pending → answered/rejected`，first valid answer wins。
4. 同事务写 RuntimeEvent + wake Outbox。
5. 任意 Worker claim 对应 Run 继续执行。
6. 重复回答返回当前结果，不重复执行 Action。

刷新后 pending Interaction 从 Snapshot 恢复。超时由数据库时间和 Worker 扫描决定，
不能信任浏览器倒计时。

UI Projection、通道能力、参数、边界、差距矩阵、迁移顺序与验证清单见
[AGENT_14_Protocol_UI参数与迁移附录.md](AGENT_14_Protocol_UI参数与迁移附录.md)。
