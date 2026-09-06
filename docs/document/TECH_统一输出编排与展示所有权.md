# 统一输出编排与展示所有权

## 目标

同一份业务事实只能有一个主展示出口。结构化内容优先由现有
`content_blocks` 协议交给原生组件渲染；不支持原生结构化内容的渠道才使用
既有 Markdown fallback。该层不负责表格、图表、流程图、图片或文件渲染。

## 位置

编排层位于 `AgentResult/emit_payloads` 转换为现有 block 之后、各渠道 sink
之前；最终持久化和企微投递前再次使用同一个 canonical 化函数，保证历史恢复
和异步投递不会重新产生重复内容。

```text
AgentResult / emit_payloads
          │
          ▼
build_block_from_payload()
          │  现有 ContentPart / content_blocks
          ▼
OutputOrchestrator  ← 本次新增：指纹识别、展示所有权、流式候选缓冲
     ┌────┼──────────────┬──────────────────┐
     ▼    ▼              ▼                  ▼
   Web  Actor       MessageGateway      Scheduled result
 native block       native block       canonical persist
     │    │              │                  │
     └────┴──────────────┴──────────────────┘
                              ▼
                    WecomDeliverySender
                    existing Markdown fallback
```

## 选择规则

1. `table/chart/diagram/image/file` block 是结构化事实的主展示出口。
2. 最终文本中的 Markdown 表格、图表/流程图 fenced block、相同媒体链接，只有
   与结构化 block 指纹完全匹配时才会被抑制。
3. 总结、结论、解释和不匹配的 Markdown 原样保留。
4. 没有对应结构化 block 的普通 Markdown 表格不做修改。
5. 无法可靠解析或指纹比对失败时 fail-open，保留原文本。

## 渠道行为

- Web / Actor：沿用 `content_block_add` 和现有原生组件；编排层只阻止重复
  的文本出口，不改变 WebSocket cancel 监听、Actor lease、checkpoint 或终态。
- 企微：先 canonicalize 已持久化 content，再调用现有
  `WecomDeliverySender` / `_structured_fallback`；结构化表格、图表、流程图
  仍由既有 fallback 降级为 Markdown。
- 定时任务：`ScheduledTaskResult` 在持久化和 outbox 组装前 canonicalize，保留
  结构化 block，企微 worker 继续使用同一份 content 和既有 fallback。

## 流式和恢复

普通文本继续逐 chunk 发送。仅从检测到疑似结构化 Markdown 的位置开始缓冲；
收到对应结构化 block 时先去重再交付，若最终没有结构化 block 则在 flush 时
原样放行。因此取消、超时、重试、provider `close()`、Actor ownership/lease
和 checkpoint 仍由原有执行内核负责。

## 明确不做

- 不新增任何表格、图表、文件或流程图 renderer。
- 不删除所有 Markdown 渲染。
- 不改变工具 timeout、cancel/retry 分类、Actor 状态机或 deploy 机制。
- Web→企微纯文本 PushDispatcher 不新增图片/文件 URL 外发；图片/文件继续
  遵循原有安全投递链路。
