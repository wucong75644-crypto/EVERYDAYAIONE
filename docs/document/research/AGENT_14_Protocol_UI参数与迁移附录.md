# AGENT 14 附录：Protocol / UI 参数与迁移

> 主文档：[AGENT_14_Protocol_UI协议与交互投影.md](AGENT_14_Protocol_UI协议与交互投影.md)
> 内容：UI Projection、通道能力、参数、边界、差距矩阵、迁移顺序与验证

## 7. UI Projection 目标

### 7.1 组件层级

```text
Conversation
  └─ Turn
      ├─ User Message
      └─ Run Card
          ├─ Activity / Goal summary
          ├─ Action rows
          ├─ Interaction cards
          ├─ SubRun cards
          ├─ Artifact cards
          └─ Assistant Message
```

普通问答可折叠 Run Card，只显示最终消息；工具多步、长任务、等待确认和失败时展开。
用户看到“系统在做什么、为什么等我、产物在哪里”，而不是内部状态机术语。

### 7.2 Action Card

最小字段：

- 用户可读标题。
- queued/running/waiting/completed/failed/unknown。
- elapsed/progress。
- 成本预估与实际值（付费动作）。
- 输入摘要，敏感参数脱敏。
- 输出摘要和 Artifact 引用。
- cancel/retry/inspect capability，由 Policy 决定。

`unknown` 必须明确展示“正在核对 Provider 状态”，不能直接出现“重试”按钮造成重复
副作用。

### 7.3 Artifact Card

Artifact 是稳定实体，ContentPart 是展示 Projection：

```text
Artifact(kind, status, version, lineage, access)
                  ↓
ContentPart(image/file/chart/diagram/video/...)
```

同一 Artifact 可以产生 Web 富卡片、企微文本/媒体、模型上下文引用和下载链接。URL
过期时按 artifact ID 重新签名，不把临时 URL 当事实主键。

### 7.4 Goal 与 SubRun

- Goal Panel：objective、phase、预算、交付物、验证状态、暂停原因。
- SubRun Card：角色、任务、能力限制、运行时间、工具数、结果摘要。
- Child 内部 token stream 默认不混入 Parent 对话；只显示节流后的进度和 terminal。
- Parent 必须先投影 spawn 关系，再投影 Child event，避免孤立卡片。

### 7.5 错误与降级

错误分三层：

- 用户可行动：缺权限、余额不足、需要输入。
- 可自动恢复：Provider retry、网络抖动、Projection resync。
- 系统故障：执行未知、持久化失败、协议不兼容。

UI 文案与内部 error code 分开；保留 correlation ID 供支持排查，但不泄漏 Provider
密钥、原始堆栈和敏感参数。

## 8. Channel Capability 与降级

每个 Channel 注册能力：

```text
supports_stream
supports_update_in_place
supports_interaction
supports_rich_artifact[kind]
max_text_length / max_files / max_bytes
delivery_semantics
```

推荐降级矩阵：

| 事实 | Web | 企微/弱通道 |
|---|---|---|
| token delta | 实时流 | 合并或终态文字 |
| Goal/SubRun | 可展开卡片 | 关键阶段摘要 |
| permission | 内嵌 Interaction | 支持按钮则按钮，否则安全链接 |
| image/video | 富预览 | 原生媒体或签名链接 |
| chart/diagram | 交互渲染 | 摘要 + 数据/源码文件链接 |
| file | Workspace 卡片 | 文件或受控下载链接 |
| progress | 节流更新 | 仅长任务关键阶段 |

弱通道不支持安全确认时，不能把“回复 1”当高风险授权；应发一次性认证链接回 Web，
或暂停并要求用户在可信界面继续。

## 9. 边界场景

| 场景 | 处理策略 |
|---|---|
| 空 payload/未知 event type | 协议隔离并上报，不污染 Projection |
| chunk 重复 | event ID + sequence 幂等 |
| chunk 缺口 | 暂停增量，Snapshot + Replay |
| terminal 先于旧 progress | aggregate version 阻止回退 |
| 多端同时确认 | 数据库 CAS，first valid answer wins |
| 确认超时瞬间收到回答 | 以数据库事务先提交者为准 |
| Provider 成功但状态未知 | Action unknown，reconcile 后再投影 |
| Artifact URL 过期 | 用 artifact ID 重新签名 |
| 前端断线很久 | compacted Snapshot，不回放全部 token |
| Redis 重复广播 | event ID 去重 |
| Redis 丢消息 | durable event replay 补齐 |
| 慢连接 | 合并 ephemeral，terminal 不丢，必要时 resync |
| 用户取消后晚到完成 | Action/Run version 与终态优先级裁决 |
| Schema 新于客户端 | 要求刷新或使用兼容 Snapshot |
| 企微无交互能力 | 安全链接或暂停，不 fail-open |
| 大 ToolOutput | Artifact 引用 + 摘要，不塞 WebSocket |

## 10. 方案对比

| 维度 | A：继续扩展现有 WS type | B：统一 Runtime Event + Adapter | C：纯 Event Sourcing |
|---|---|---|---|
| 实现速度 | 快 | 中 | 慢 |
| 恢复能力 | 低 | 高 | 高 |
| 现有侵入 | 低 | 可分阶段 | 极高 |
| UI 一致性 | 持续分叉 | 高 | 高 |
| 多通道 | 各自适配 | 统一事实、分通道投影 | 统一 |
| 运维复杂度 | 中且持续增长 | 中 | 高 |
| 迁移风险 | 短期低、长期高 | 可控 | 高 |

推荐 B。它与第十三层的混合持久化一致：状态表仍是业务事实，RuntimeEvent 服务审计、
恢复和 UI 增量；旧 WS type 通过 Adapter 兼容。不要为了协议统一引入纯事件溯源。

## 11. 模块边界

目标模块：

```text
runtime/protocol/
  envelope        Event/Command/Snapshot schema
  event_writer    sequence + transaction
  projector       event → UI projection
  replay          after_sequence / snapshot
  interactions    persistent reverse request
  channel         Web/WeCom capability adapters

frontend/runtime/
  protocol        Zod schema + version gate
  projection      deterministic reducers
  transport       subscribe/replay/gap recovery
  components      Run/Action/Interaction/Artifact/Goal/SubRun
```

第一阶段不创建上述全部文件。最终文件清单要在全项目差距矩阵和总体设计完成后，结合
现有目录膨胀情况确定，避免边调研边搭空框架。

## 12. 与其他层的契约

- Session Actor：保证同一 Run 命令串行、分配事件顺序。
- Model Loop：发布结构化 activity/action，不发布原始推理。
- Policy：生成 Interaction 和授权结果；UI 不能自行放权。
- ToolBridge/Executor：发布 Action lifecycle 和结构化 ToolRunResult。
- Goal/SubRun：发布父子关系、阶段进度和 terminal。
- Persistence：状态 + Event + Outbox 原子提交。
- Artifact：稳定 ID、版本、lineage 和访问策略。
- Observability：event lag、gap、replay、projection error 和慢客户端指标。

## 13. 第一轮差距矩阵

| 能力 | Grok | 当前项目 | 目标 |
|---|---|---|---|
| 结构化事件 | ACP + extension | 多个 WS type | 版本化 Runtime Event |
| 高频合并 | 10ms/2KiB/100 | 前端 16ms；后端逐 chunk | 双端合并 + checkpoint |
| Event ID | 有 | 无 | UUIDv7 |
| Sequence | event ID 隐含 | `last_index` 已停用 | Run 单调整数 |
| Replay | updates JSONL | accumulated snapshot | Snapshot + Event replay |
| Durable terminal | TurnCompleted | DB terminal + best-effort done | 事务 Event + Outbox |
| Interaction | 多数进程内 | 全部进程内 | 持久 CAS |
| Tool UI | 结构化更新 | ToolStep/Result | Action Projection |
| Goal/SubRun UI | 有 | 无 | 独立卡片 |
| Artifact | Tool content/文件 | ContentPart + Workspace | Artifact → ContentPart |
| 多通道 | TUI/IDE/Web ACP | Web + 企微分支 | Channel capability adapter |
| 思维展示 | 支持 thought chunk | thinking part | 标准化 activity |

## 14. 实施约束与迁移顺序

1. 先冻结 Runtime Event v1 与 ID/sequence 语义。
2. 在现有 Actor 原子终态旁写 RuntimeEvent + Outbox。
3. 先迁移 terminal、Interaction、Artifact 等低频关键事实。
4. 增加 HTTP Snapshot/Replay，再让前端实现 gap recovery。
5. 迁移 Action/Goal/SubRun Projection。
6. 最后迁移 token/progress，保留旧 WS adapter 观察一个发布周期。
7. 企微从同一 Projection 输入生成 delivery items，不直接消费内部事件。
8. 指标确认新旧一致后，再删除旧 `last_index/current_index` 和临时恢复分支。

迁移期间禁止：

- 同一事件在两个 Writer 独立分配 sequence。
- 把 Redis Pub/Sub 当 durable queue。
- 让前端通过文案决定 Action terminal。
- 在 terminal 事务前发布不可撤回的 completed。
- 用 Plugin/Skill/模型输出直接构造已授权 Interaction answer。

## 15. 验证清单

后续实现必须覆盖：

- 同 Run 1 万事件顺序、重复和 gap。
- 查询历史与切 live 之间无丢失。
- Redis 重复、延迟、乱序和暂时不可用。
- WebSocket 中断前后文本无重复/缺字。
- terminal 与晚到 progress 的版本竞争。
- 多端 Interaction first-answer-wins。
- Interaction 超时、重启和 Worker 转移。
- Action accepted 后响应丢失进入 unknown。
- Snapshot compaction 与旧客户端版本。
- 大 ToolOutput 不进入事件 payload。
- Web/企微对同一 Artifact 的一致 lineage。
- 慢客户端背压和内存上限。
- 租户/org 路由与事件 replay 越权检查。

## 16. 本轮结论

EVERYDAYAIONE 的展示基础并不弱：ContentPart、前端专业组件、Actor 进度恢复和企微
Outbox 都应保留。当前真正缺失的是“运行时事实到 UI 的统一、可排序、可重放协议”。

目标不是复刻 Grok 的全部 ACP event，而是吸收它的关键机制：

```text
结构化事件
+ 高频合并
+ canonical terminal
+ stable identity
+ Replay
+ Goal/SubRun/Tool 专业 Projection
```

并用 SaaS 更强的持久 Interaction、数据库 sequence、Transactional Outbox、租户隔离和
Channel Capability 补足 Grok 本地实现的边界。
