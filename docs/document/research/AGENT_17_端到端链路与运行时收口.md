# AGENT 17：端到端链路与运行时收口

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：Web、企微、普通聊天、工具、多 Action、Skill、Goal、Subagent、MCP、
> 图片/视频、文件、ERP、持久化、恢复和展示
> 配套文档：`AGENT_17_全项目差距矩阵与优先级.md`

## 1. 结论摘要

用户描述的“内部工具都能用，但链路是断层的”与源码现状一致。

EVERYDAYAIONE 已经拥有一条较成熟的主干：

```text
Web / 企微
  → Conversation Actor
  → 固定 ContextSnapshot
  → execute_chat
  → Tool Loop
  → ToolExecutor
  → GenerationOutcome
  → 数据库原子终态
  → WS / 企微 Outbox
```

同时已有图片、视频、ERP、文件、沙盒、图表和知识库等专业能力。但这些能力还没有共同
归属于一个可持久化、可恢复的 Agent Runtime：

- 普通 Chat 工具在 Turn 内执行。
- 图片/视频 Provider 使用独立 `tasks + webhook/polling` 生命周期。
- ERPAgent 是工具内部的第二层 Agent Loop。
- Skill、Goal、通用 Subagent、MCP Runtime 尚未进入产品主链。
- 前端展示以 Message/ContentPart 为中心，没有统一 Run/Action/Interaction 投影。

因此目标不是推倒现有 Actor，而是在它上面补齐统一 Session/Run/Action 外壳，把已有
执行器接入，形成：

```text
Ingress
  → Session Command
  → Run
  → Model Step
  → ActionRequest
  → Policy / Interaction
  → Executor
  → ActionResult / Artifact
  → RuntimeEvent
  → Projection / Persistence / Recovery
```

## 2. Grok Build 的端到端主链

### 2.1 Prompt 进入 Session Actor

Grok 的 `SessionActor::handle_prompt` 不是直接调用模型。它依次处理：

1. 记录 prompt 长度、prompt_id、Turn active 状态和 telemetry。
2. 清理上一 Turn 的 active skill，识别 retry/regeneration。
3. 处理直接 Bash 和 slash command。
4. 解析 Skill slash invocation，记录 `SkillDispatched/skill.activated`。
5. 处理 `/goal set`、`/goal resume` 等 Session command。
6. 建立 Turn event、模型/模式/权限事实。
7. 组装 Prompt 并进入采样循环。

这里的关键不是 slash command，而是所有入口都先变成 Session command，再由同一个
Actor 持有 Turn、取消、Skill、Goal、MCP、Plugin、配置和持久化状态。

### 2.2 Model Step 与 Tool Loop

模型返回无 Tool Call 时，Grok 先经过：

- TodoGate。
- pending interjection drain。
- Goal completion/continuation。
- Turn bookkeeping。
- structured output validation。

模型返回 Tool Call 时：

```text
Tool call delta
  → 标准 ToolCallResponse
  → PhaseChanged(ToolExecution)
  → execute_tool_calls
  → prepare_tool_call
  → dispatch
  → Tool result 回填 conversation
  → 下一 Model Step
```

`max_turns` 在每轮工具后检查；下一轮推理前检查 preflight overflow，并可触发 compaction。

### 2.3 多 Tool Call

`execute_tool_calls` 先逐个 prepare，再执行已批准集合：

1. ToolCall 先以 `Pending` 事件注册，UI 立即可见。
2. 参数标准化、JSON 解析和 ToolBridge typed parse。
3. Plan mode gate。
4. pre-tool Hook。
5. permission/approval。
6. 解析 read-only 和写路径。
7. 同文件写操作共享互斥锁。
8. 已批准调用形成 futures 并发执行。
9. 结果、失败、取消和 follow-up 重新进入模型循环。

这意味着“模型一次给出多段提示词并多次生图”在框架层不是特殊功能，而是同一 Model
Step 产生多个独立 Tool Call。每个调用必须有独立 `tool_call_id`、参数、权限决策和结果。

### 2.4 Skill 链路

Grok Skill 有两条触发路径：

- 用户显式 `/skill-name args`。
- 模型通过 Skill Tool/引用加载。

Session 会记录 active skill 和来源，Skill 内容成为本 Turn 的指令上下文；之后仍走普通
Model → Tool → Result 循环。Skill 不直接绕过 Policy，也不拥有额外权限。

这一点决定了我们的 Skill Runtime 应是“能力装配和工作说明”，不是另一套 Executor。

### 2.5 Goal 链路

Grok 的 Goal 仍由 Session Actor 持有：

```text
create/resume goal
  → Planner
  → Implementer Model Loop
  → Completion Classifier / Skeptic Verifier
  → Stall detection
  → Strategist
  → Continue / Pause / Block / Complete
```

后台工具完成通知在 Goal loop 活跃时不会自动插入普通 wake prompt，防止两个 continuation
控制器互相竞争。Goal 是 Session 上的长期控制循环，不是一个普通 Tool。

### 2.6 Subagent 与后台任务

Grok 在 Tool Call 注册阶段识别 `task/Task/spawn_subagent`，把 background 属性写入 Tool
metadata。子 Agent 拥有独立上下文和执行生命周期，完成事件通过 notification bridge
进入父 Session。

父 Session 决定：

- 前台等待。
- 后台继续。
- 完成后自动唤醒普通 Turn。
- Goal 活跃时抑制自动唤醒。
- 用户显式 wait/kill 后不重复注入。

所以 Subagent 的关键不是并行调用模型，而是父子 Run、通知消费和 continuation 所有权。

### 2.7 MCP 链路

MCP Tool 名先解析 server/tool。调用时：

- managed MCP 过期则刷新。
- Blocking 策略等待初始化。
- Progressive 策略在工具未就绪时返回“先搜索工具”的结构化失败。
- MCP 仍经过 ToolBridge parse、Hook、Permission 和事件记录。
- 完成事件记录 duration、timeout、error、reconnect 和 auth retry。

MCP 是 Tool Catalog 的动态来源和远程 Executor adapter，不是绕过 Tool Runtime 的旁路。

### 2.8 持久化与 UI

Grok 的通知同时进入：

- ACP/UI notification。
- `PersistenceMsg`。
- typed Session event/observability。

`PersistenceMsg` 包含 Update、Chat、Plan、Goal、ContentChunk、Rewind、Compaction、
Signals、Feedback 等多类事实。它不是仅在 Turn 结束时保存最终文本，因此重连和恢复能
重建过程状态。

## 3. EVERYDAYAIONE 当前端到端链路

### 3.1 Web Chat

当前 Web Actor 路径：

```text
消息请求
  → 原子 enqueue user/assistant/task/turn
  → Worker 扫描或 Redis 唤醒
  → serial/branch claim
  → GenerationClaim + fencing token
  → ChatGenerationExecutor
  → 恢复输入 ContentPart + ContextAnchor
  → execute_chat
  → GenerationOutcome
  → commit/fail RPC
  → ActorTerminalDelivery + WS
```

`ConversationExecutionService` 已有：

- `lease_seconds=90`。
- Actor runtime 续约间隔 `5s`。
- 默认最大续约失败 `2` 次。
- 默认最大 attempt `3` 次。
- ownership lost 时取消本地执行。
- 数据库确认终态后才发送 best-effort 外部通知。

这是目标 Runtime 最重要的可复用底座。

### 3.2 企微

企微链路已与 Web 共用 Actor 和 `execute_chat`：

```text
回调/WS 入站
  → 消息规范化
  → 用户与 channel conversation 解析
  → FILE 暂存或内容入队
  → Actor
  → 相同 ChatGenerationExecutor
  → 原子终态创建 Outbox
  → WecomDeliveryWorker
  → 文本/图片/视频发送或图形文本降级
```

群聊通过数据库派生 `ExecutionScope`，个人 Memory/积分/敏感工具与 channel Workspace
分离。这比简单复刻本地 CLI Session 更适合多租户 SaaS，应保留。

### 3.3 Chat Model Loop

`execute_chat` 已完成通道无关化：

- `prepare_chat_stream` 构造历史、Adapter、Tool Catalog、预算和 RuntimeState。
- 每轮 `prepare_tool_turn` 选择工具。
- Adapter 流式返回 text/thinking/tool_calls。
- Tool Call 按 ID 累积后排序。
- 无调用则完成；有调用则进入 `_execute_tools`。
- 工具结果回填 messages，压缩旧 Tool result，再进入下一轮。
- 最后把 block 转为 ContentPart，返回 usage、积分、digest 和 data evidence。

这已经是可复用的 Model Worker，但它还不是完整 Agent Runtime：Run、Action、
Interaction、SkillRun、SubRun 和 RuntimeEvent 仍未成为统一持久对象。

### 3.4 多 Tool Call

`ChatToolMixin._execute_tool_calls` 会按 `is_concurrency_safe` 分批：

- 安全只读工具使用 `asyncio.gather`。
- 写操作逐个执行。
- 文件 ID 在执行前解析为受控路径。
- 结果统一经过 `ChatToolResultMixin` 分类并回填。
- `AgentResult.emit_payloads` 聚合到最终 ContentPart。

当前不足：

- 并行/串行依据主要是静态工具安全标记，不是显式资源锁和 Action conflict key。
- 每个 Tool Call 缺少跨进程持久 Action/Attempt。
- Turn 中途 Worker 崩溃后，无法精确知道哪些外部调用已被 Provider 接受。
- 一次多图虽可产生多个 Tool Call，但没有统一 batch/parent Action 投影和部分成功恢复。

### 3.5 图片与视频

模型可调用 `generate_image/generate_video`，ToolExecutor 分发给 `MediaToolMixin`。内部已有
模型选择、积分预留、Provider task、Webhook/轮询、OSS、确认扣费和退款。

但它与 Chat Actor 是两层生命周期：

```text
Chat Tool Call
  → 创建媒体 task / 返回处理中
  → Provider 异步运行
  → TaskCompletionService
  → handler.on_complete/on_error
  → 消息/WS 更新
```

因此媒体不是普通同步 ToolResult，也还不是统一 Action `Accepted → Running → Completed`。
`TaskCompletionService` 使用 Redis 完成锁 `300s`、每 `60s` 续期，并用 DB version 二次
幂等；这些机制可迁移到 Action Executor，但不能继续作为旁路状态机永久存在。

### 3.6 文件、代码与 ERP

文件链路已经具备较强的数据边界：

```text
ResourceManifest/file_id
  → file_analyze
  → staging Parquet
  → code_execute/data_compute
  → AgentResult
  → Artifact evidence / emit payload
```

ERPAgent 则作为 ToolExecutor 内的专业 Agent，接收 task/context，运行自己的 Tool Loop，
共享父预算并返回 AgentResult。这是“专业 Executor 内部可有 Worker Agent”的有效实现。

断点在于父 Chat 只看到一次 `erp_agent` Tool Call，内部步骤没有统一 SubRun/Action
事件、权限继承和恢复记录。

### 3.7 当前缺失的运行时链路

源码未发现进入产品主链的以下通用模块：

- Skill Catalog/Loader/SkillRun。
- Goal Orchestrator/持久 Goal 状态机。
- 通用 Subagent spawn/wait/cancel/resume。
- MCP Server lifecycle、动态 Catalog 和 MCP Tool adapter。
- Plugin manifest/trust/install/runtime。

现有 DepartmentAgent/ERPAgent 属于静态业务编排，不能等同通用 Subagent Runtime。

### 3.8 恢复与展示

当前恢复分为：

- Conversation Actor：DB claim/lease/fencing 恢复执行。
- Chat 前端：pending task + WS 重新订阅。
- 媒体：task pending/running + webhook/polling。
- 企微：事务 Outbox lease/checkpoint。
- 消息展示：ContentPart + Message store。

这些恢复机制各自有效，但前端无法从一个 Run Snapshot 得知完整状态，也没有统一
sequence/event cursor。用户看到的是消息、媒体占位、Tool step 和企微 stream 的不同投影。

## 4. 十条目标链路

### 4.1 仅聊天

```text
UserMessage → Run → ModelStep → FinalText Artifact
            → RunCompleted → Message Projection
```

无 Tool Call 也必须有 Run 和配置/上下文 receipt，便于审计与回放。

### 4.2 单个同步工具

```text
ModelStep → ActionRequested → PolicyAllowed
          → ActionRunning → ActionCompleted
          → ToolOutput 回填 → ModelStep → Final
```

### 4.3 多 Action

模型一次产生 N 个 ActionRequest，每个独立校验。调度器根据：

- `side_effect_class`。
- `concurrency_key`。
- `resource_locks`。
- `cost_reservation`。
- executor capacity。

决定并行或串行。部分失败只影响对应 Action，模型根据全部结果决定重试、降级或总结。

### 4.4 只写提示词与执行生图

```text
“帮我写提示词” → FinalText，不创建 Action
“用这些提示词生成” → N 个 GenerateImage Action
```

执行授权来自用户意图和 `AuthorizationGrant`，不来自 AI 回复中是否出现提示词。

### 4.5 Skill

```text
Skill selected
  → resolve version
  → create SkillRun
  → inject instruction/workflow state
  → ordinary Model/Action loop
  → step checkpoint
```

Skill 请求的工具集合还要经过 EffectiveToolset 和 Policy，不形成特权。

### 4.6 Goal

```text
GoalCreated → Plan → Run rounds
  → Verify → Continue / Strategize / Pause / Complete
```

Goal controller 是唯一 continuation owner；普通后台完成事件只更新状态，不直接插入
第二条 continuation。

### 4.7 Subagent

```text
Parent Run → SubRunRequested → Policy
           → isolated ContextPlan + capability subset
           → SubRun
           → structured SubRunResult/Artifact
           → parent continuation
```

### 4.8 MCP

```text
Plugin/MCP config → Gateway connection → Tool Catalog delta
  → EffectiveToolset → ordinary ActionRequest
  → Policy → MCP adapter → ToolOutput
```

MCP 的连接权限、工具业务权限和一次执行授权必须分离。

### 4.9 长时媒体

```text
ActionRequested → cost reservation → Provider accepted
  → ActionAccepted(external_id)
  → callback/poll reconciliation
  → Artifact persisted
  → cost confirmed/refunded
  → ActionCompleted/Failed/Unknown
  → Run continuation or final projection
```

Chat Worker 不阻塞几十秒等待，但 Run 可以处于 `waiting_actions`。

### 4.10 多通道恢复

```text
RuntimeEvent log + current state
  → Snapshot(sequence)
  → channel projection
  ├─ Web: rich ContentPart/Interaction
  └─ WeCom: text/media/card degradation
```

通道只改变展示和交互能力，不改变 Run/Action 业务事实。

## 5. 统一收口点

第一阶段只需要六个核心对象：

| 对象 | 责任 |
|---|---|
| Session | 会话身份、命令入口、活动 Run/Goal |
| Run | 一次用户目标或自动 continuation |
| ModelStep | 一次确定模型请求与响应 |
| Action | 工具/Provider/外部动作的持久生命周期 |
| Artifact | 文本、图表、媒体、文件、数据证据 |
| RuntimeEvent | 持久事实与 UI/通道投影输入 |

SkillRun、SubRun、Interaction、Goal 可以在第二阶段作为专用对象接入，但协议从第一阶段
预留 correlation/type/version，避免再次形成旁路。

## 6. 不应统一的部分

- 不把所有 Executor 合成一个巨型类。
- 不让同步查询套用媒体 Provider 的内部状态。
- 不把 UI ContentPart 当作业务事实源。
- 不把 Skill 文本当成授权。
- 不让 MCP 自己决定租户权限。
- 不用 Goal 替代普通 Chat。
- 不把 ERP 内部专业规划全部搬进通用 Orchestrator。
- 不要求企微具备 Web 的每一种组件，只要求结果可读且状态收敛。

## 7. 冻结结论

项目最终应采用：

```text
一个 Session Runtime
一个 Run / Action / Event 事实协议
一组专业 Executor
多个 Channel Projection
```

现有 Conversation Actor、`execute_chat`、ToolExecutor、媒体完成服务、Artifact/ContentPart、
企微 Outbox 都应迁移接入，而不是重写。

第十七层完成后，第一轮事实调研结束。下一阶段不直接编码，而是先根据全项目差距矩阵
冻结目标架构、模块边界、数据库/API/事件协议和分阶段兼容迁移方案。
