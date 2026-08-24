# 流式交付恢复设施

## 目标

Conversation Actor 继续使用 PostgreSQL claim/lease/fencing 和 ReplayCheckpoint。
本设施只负责“已经生成的内容如何可靠交给页面”，解决暂停、继续、刷新、断线重连和多 Worker
情况下的旧流污染与最终一次性显示。

## 三层事实

| 层 | 事实 | 负责人 |
| --- | --- | --- |
| Execution | pending/running/paused/completed/failed/cancelled、execution_token、execution_attempt | PostgreSQL Actor Runtime |
| Delivery | stream_id、delivery_seq、快照、回放事件、交付状态 | conversation_delivery_sessions/events |
| Message | messages 的最终内容和终态 | message commit RPC |

`ReplayCheckpoint` 只给模型恢复使用；Delivery Snapshot/Event 只给页面恢复使用，不能互相代替。

## 协议

Actor 的 `message_start`、`message_chunk`、`thinking_chunk`、`content_block_add` 和 `stream_end`
携带 `stream_id`、`execution_attempt` 和 `delivery_seq`。客户端按以下规则处理：

- 旧 execution attempt 或旧 stream_id 的事件丢弃；
- 已处理的 delivery_seq 幂等丢弃；
- 发现序号间隙，按当前序号重新 subscribe；
- subscribe 返回数据库快照和快照之后的事件；
- `stream_end` 只表示模型流结束，`message_done` 才表示数据库 commit 完成。

Redis Pub/Sub 只承担低延迟通知，不承担断线恢复；PostgreSQL 是 Delivery 的恢复事实源。

## 状态流程

```text
RUNNING + PAUSE command
  -> Runtime safe point
  -> save task progress + ReplayCheckpoint + Delivery Snapshot
  -> PAUSED

PAUSED + RESUME command
  -> new execution_attempt + new stream_id
  -> ReplayCheckpoint restores model context
  -> Delivery Snapshot restores page content
  -> new delivery_seq starts at 1
```

取消仍然是终态；暂停不会创建新消息，继续仍然复用原 task/message 的逻辑身份。

## 运行约束

- 正常运行中交付事件先持久化，再通过 WebSocket/Redis 推送；
- progress snapshot 在安全点和周期性进度刷盘时更新；
- 新 execution attempt 会清理旧 stream 的事件，防止旧流被恢复；
- 终态保留最后快照，清理事件，避免事件表无限增长；
- 交付存储暂时不可用时，任务仍可依赖现有 tasks 快照继续运行，并记录告警；这属于降级路径，不能宣称完全实时回放。

## 验收

1. 输出中暂停，刷新后 partial 保留且状态为 paused；
2. 继续后页面立即显示新增 chunk，不等最终完成；
3. 继续过程中刷新，按 snapshot/seq 恢复，不重复、不丢失；
4. WebSocket 断线后重连，客户端携带 last_delivery_seq 回放；
5. 旧 attempt、重复 seq、跨 Worker Redis 重复投递不会污染当前消息；
6. Worker 崩溃后新 attempt 能从 ReplayCheckpoint 恢复，旧 token 不能提交；
7. 完成只由 message_done 收口，暂停/取消不会误显示 completed。
