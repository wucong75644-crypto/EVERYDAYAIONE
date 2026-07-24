# AGENT 04：Model Loop 模型循环

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：模型请求、流式事件、工具轮次、并行执行、停止、重试、取消和预算
> 后续专项：Policy、ToolBridge、Goal、Context 和 Persistence 分别深入

## 1. 结论摘要

行业里所谓 Agent 的核心循环不是“模型反复调用工具”，而是一套受控制的状态推进器：

```text
准备本轮上下文与能力
→ 模型采样
→ 解析文本 / 思考 / Tool Calls / Stop Reason
→ 执行前策略
→ 并发或串行执行工具
→ 结构化 Observation 回填
→ 判断完成 / 等待 / 恢复 / 继续
```

Grok Build 已将模型传输重试、Session 级恢复、工具并发、完成要求、结构化输出、上下文压缩、插话和终态记账纳入同一主循环。

EVERYDAYAIONE 已有流式模型适配器、工具增量解析、读并发/写串行、预算预留、最终合成和专业 Agent 的失败停滞策略。但主 Chat 当前并存两套循环：

- Actor/企微等新通道使用 `execution_engine.execute_chat()`。
- 旧 Web 流使用 `ChatStreamLoop`。

两套循环行为不等价；共享 `ToolLoopExecutor` 中更成熟的失败分类、循环检测和停止策略也尚未用于主 Chat。

第一轮结论为“融合升级”：

1. 以通道无关 `execute_chat` 为未来唯一 Model Loop 内核。
2. 将旧 Web 循环中仍有价值的空输出恢复、steer、分阶段取消迁移到统一内核。
3. 将 `ToolLoopExecutor` 的结构化失败分类、停滞检测和 wrap-up 决策提取为共享策略，而不是保留第三套循环。
4. 保留本项目墙钟预算和最后一轮总结；补齐 Completion Contract、统一 StopReason 和可恢复 Checkpoint。
5. 借鉴 Grok 的分层重试，但不采用默认最多 15 次、约 6 分钟的模型传输重试作为 SaaS 通用默认值。

## 2. Grok Build 主循环

### 2.1 主控制流

源码：`xai-grok-shell/src/session/acp_session_impl/turn.rs`

```text
process_conversation_turn_with_recovery()
└─ process_conversation_turn()
   ├─ 刷新模型元数据 / 模型切换压缩
   ├─ 准备 Tool Definitions
   ├─ 初始化本 Turn 统计与恢复状态
   └─ loop
      ├─ 注入 interjection / Skill / monitor / memory / MCP reminder
      ├─ 构造 ConversationRequest
      ├─ run_turn_via_sampler()
      ├─ 处理文本、拒绝、工具调用、结构化输出
      ├─ execute_tool_calls()
      ├─ 检查 max_turns
      ├─ 检查 preflight overflow 并压缩
      └─ 继续下一次模型调用
```

Grok 的一个用户 Prompt 可包含多次“模型采样 → 工具执行”。`tool_turn_count` 从 1 开始，下一轮超过 `max_turns` 才返回 `MaxTurnsReached`。

### 2.2 模型采样与事件顺序

源码：`acp_session_impl/sampler_turn.rs::run_turn_via_sampler`

每轮先：

1. 刷新临近过期的认证。
2. 重建完整 SamplerConfig。
3. 注入 `idle_timeout_secs`。
4. 提交带随机 RequestId 的采样请求。

采样流由独立 drainer 处理 UI/持久化事件。模型响应完成后，Turn Loop 最多等待 5 秒 `stream-drain barrier`，确保文本事件尽量先于 Tool Call 事件到达；超时后继续执行并记录顺序可能不完美。

该设计体现两个边界：

- 模型流读取与状态机推进解耦。
- UI 事件顺序有显式 barrier，而不是依赖异步任务“通常会先完成”。

### 2.3 Sampler 传输重试

源码：`xai-grok-sampler/src/retry.rs`

配置优先级：

```text
GROK_MAX_RETRIES
→ Model max_retries
→ DEFAULT_MAX_RETRIES = 15
```

重试策略：

| 错误 | 行为 |
|---|---|
| 500/502/503/504/520、连接、流中断、空响应 | 指数退避重试 |
| 第一次通用传输重试 | 同时重建 HTTP Client，强制逃离异常 HTTP/2 连接池 |
| 429 | 最多 2 次，优先服从 Retry-After |
| 413/图片处理错误 | 去掉内联图片后重试一次，不计普通预算 |
| 400/401/403/404/408/422 | 不做普通传输重试 |
| IdleTimeout、序列化、MaxTokensTruncation | 直接终止 |
| 上下文超长 | 交由 Session 压缩，不重复发送相同 Payload |
| Doom Loop | 独立近即时抖动重采样 |

普通退避为 2、4、8、16 秒，之后封顶 30 秒，并加入 ±20% jitter。默认 15 次约可持续 6 分钟。

这个默认更适合本地持续工作的 Coding Agent；多租户 SaaS 需要按交互请求、后台 Goal 和付费 Provider 分别设置总重试预算。

### 2.4 Session 级恢复

Sampler 重试耗尽或不能处理后，Session 再做语义恢复：

- 上下文错误：更新真实 Context Window，压缩后重新提交。
- 可恢复 401：刷新凭据，再以 1、2、4 秒计划重新提交。
- 加密历史不兼容：要求新建 Session，不盲目重试。
- Rate Limit：向 UI 发出 Exhausted 状态。
- Empty Response、Idle Timeout：记录强类型信号。

这里区分了三类动作：

```text
同请求传输重试
≠ 修改上下文后重新采样
≠ 修改认证后重新采样
```

统一 Model Loop 必须保留这种分类，避免同一 Tool Call 或付费生成动作被无差别重复。

### 2.5 Completion Requirement

若 Agent 声明必须调用指定工具，`process_conversation_turn_with_recovery()` 会检查本 Turn 的 `tools_called`。

未满足时：

- 注入 `auto_recovery` 消息。
- 按 `base_delay_ms × 2^(attempt-1)` 退避。
- 受 `max_delay_ms` 和 `max_retries` 限制。
- 发送 AutoRecoveryStarted/Exhausted 事件。
- `MaxTurnsReached` 时不再恢复。

它解决的是“模型说做完了，但系统可验证动作没发生”，与网络重试完全不同。

当前验证粒度仍较弱：只检查工具名是否调用，不验证调用结果是否成功或产物是否符合目标。EVERYDAYAIONE 后续 Goal Verifier 应升级为结果级 Completion Evidence。

### 2.6 结构化输出

Grok 根据 Provider 能力选择：

- 原生 JSON Schema。
- 退化为 `StructuredOutput` Tool。

Tool 模式要求该工具单独、只调用一次且位于其他工具之后。Schema 不符合时把校验错误作为 Tool Result 回填，最多重试 3 次。

这是统一“模型自纠错”的好范例：错误变成结构化 Observation，而不是由应用层猜测修补 JSON。

### 2.7 多工具并发与冲突控制

源码：`acp_session_impl/tool_calls.rs::execute_tool_calls`

执行分三步：

1. 按模型顺序逐个准备 Tool Call，执行 Hook、Permission 和参数解析。
2. 将批准的调用加入 `FuturesUnordered` 并发执行。
3. 按完成顺序处理结果、Post Hook、Skill 更新和事件。

冲突控制：

- 只要同批存在某路径的写操作，所有访问该路径且可识别路径的调用共享 Mutex。
- 等待型工具可被新 interjection 打断。
- 一次认证恢复通过 `OnceCell` 在并发工具间共享。
- 某调用被拒绝后，后续未准备调用写入“因前序拒绝而取消”的 Tool Result，保持 Tool Call/Result 配对。

Grok 比简单的“所有只读并发、所有写入串行”更精细：互不冲突的写操作仍可并行，相同文件访问才串行。

### 2.8 停止与继续

Turn 完成不是简单判断“没有 Tool Call”：

- 检查待办 TodoGate。
- 检查插话是否在终态记账前后到达。
- 检查结构化输出。
- 检查 Completion Requirement。
- 检查 `max_turns`。
- 记录 Signals、Token、Cache、工具、反馈和 StopReason。

因此 Grok 的主循环实质是“小型运行时”，模型只负责提出下一动作。

## 3. EVERYDAYAIONE 主 Chat 循环

### 3.1 新通道无关内核

源码：`backend/services/handlers/chat/execution_engine.py`

```text
execute_chat()
→ prepare_chat_stream()
→ while budget 未停止
   ├─ use_turn()
   ├─ prepare_tool_turn()
   ├─ adapter.stream_chat()
   ├─ 累积 thinking/text/tool_call deltas
   ├─ 无 Tool Call：完成
   ├─ 有 Tool Call：执行并回填
   ├─ 推送产物
   └─ 压缩工具上下文
→ budget stop 时 synthesize_wrap_up()
→ 构建 ContentPart / Tool Digest
```

该内核被 Actor 执行入口和 Chat Generate 复用，是正确的未来收口方向。它通过 `ExecutionSink` 解耦输出通道。

当前缺口：

- 不读取 Provider `finish_reason` 来决定继续、截断、拒绝或异常，只累计最后值。
- 没有旧 Web 的空输出恢复。
- 没有旧 Web 的 steer 注入。
- 取消检查只在 chunk、模型轮次和工具整批前后，不能中断不可取消的单工具。
- 没有接入 StopPolicy 的连续失败/同错/循环检测。
- 没有 Completion Contract 或结构化输出自纠错。

### 3.2 旧 Web ChatStreamLoop

源码：`backend/services/handlers/chat/stream_loop.py`

它额外实现：

- loop 顶部、流结束、工具结束后的三段取消检查。
- 工具执行后读取进程内 steer 消息。
- 工具后模型空输出时重试一次，并关闭 thinking mode。
- 第二次仍为空时回退展示最近 Tool Output。
- 流式 thinking/text WebSocket 投递及每 20 chunk 临时保存。

这些能力没有全部进入新内核。旧循环继续运行意味着不同渠道下同一句请求可能产生不同恢复和取消行为。

### 3.3 工具调用增量

`accumulate_tool_call_delta()` 使用 Provider 返回的 `index` 聚合：

```text
id
name
arguments += arguments_delta
```

模型流结束后按 Tool Call ID 排序再执行，而不是按原始 index。通常 ID 顺序稳定，但协议没有保证 ID 字典序等同模型声明顺序；涉及副作用与依赖工具时可能改变语义。

目标协议应保留原始 `index/ordinal`，用于：

- 顺序执行依赖动作。
- 稳定 UI 顺序。
- Tool Result 配对。
- 重放和审计。

### 3.4 工具并发策略

`ChatToolMixin._execute_tool_calls()` 使用 `partition_tool_calls()`：

- 连续的 concurrency-safe 工具组成批次，通过 `asyncio.gather()` 并行。
- 非安全工具各自形成批次，按模型顺序串行。
- 若序列为 `read, write, read`，两个 read 不跨 write 并发，保持屏障语义。

优点是简单、安全、可预测。局限是：

- 安全性来自工具名静态配置，不读取本次参数中的资源冲突。
- 同批并行使用 `gather()`；单个任务抛出未捕获异常会影响整批，不过 `_execute_single_tool()` 当前会转换大部分异常。
- 没有统一并发上限，模型一次返回大量只读调用时直接创建等量协程。
- 危险工具确认最长等待 60 秒，等待状态未形成可跨进程恢复的 Loop Outcome。

本项目应先保留“读并发、写串行”作为默认，再为声明了 Resource Keys 的工具增量引入按资源锁并发，而不是直接复制 Grok 的文件路径专用逻辑。

### 3.5 ExecutionBudget

主 Chat 默认：

| 参数 | 值 |
|---|---:|
| `max_turns` | 15 |
| `max_wall_time` | 600 秒 |
| `wrap_up_turns_reserved` | 1 |

停止优先级：

```text
max_turns
→ wrap_up_budget
→ wall_timeout
```

到第 14 轮即触发 wrap-up，使用不带 Tools 的模型请求总结，默认超时 15 秒、温度 0.3。总结失败但已有文本时返回部分结果；完全无输出时标记预算错误。

优点：

- 比 Grok 只有可选 `max_turns` 多了墙钟安全网。
- 预留收尾而不是突然截断。
- 子 Agent `fork()` 限制轮次不超过父剩余量，并共享父剩余墙钟。

风险：

- `use_turn()` 在模型调用前执行，所谓 Turn 实际是模型采样次数，不是纯工具轮次，命名需统一。
- `tool_timeout()` 最低返回 1 秒；父预算已经为 0 时仍可能再执行 1 秒。
- 墙钟只在循环顶部检查，正在运行的模型流或工具不受总 Deadline 的强制取消。
- Wrap-up 是额外模型请求，但不计入 `turns_used`，成本与审计需要单列。

### 3.6 Provider 超时与重试

Adapter 工厂根据模型选择 `stream_timeout`：

- 普通 Chat 默认 60 秒。
- 已知推理模型默认 120 秒。
- Google Adapter 当前 read timeout 为 600 秒。

配置注释称普通/推理超时“已被 budget 替代”，但 Adapter 仍真实使用该超时；而 ExecutionBudget 不会主动中断一个阻塞流。这是配置语义与运行事实不一致。

主 Chat 的 Provider 错误由 `classify_error()` 后交给 `_handle_stream_failure()` 重试旧入口，Actor 终态则由外层任务重试语义管理。需要在后续 Persistence/端到端板块精确核验：重试发生在模型请求、整个 Run 还是整个数据库 Task，是否可能重复工具副作用。

### 3.7 ToolLoopExecutor 的成熟策略未进入主 Chat

`backend/services/agent/tool_loop_executor.py` 用于 ScheduledTask 等专业 Agent，已有：

- 连续 3 次相同 Tool Call 的循环检测。
- 上下文错误压缩后只恢复一次。
- 空输出处理。
- `FailureTracker`。
- `ResultClass=SUCCESS/RETRYABLE/NEEDS_INPUT/AMBIGUOUS/FATAL`。
- 同错最多重试 1 次，连续失败 3 次 wrap-up。
- 多工具取最严重结果。
- `StopDecision=CONTINUE/WRAP_UP/HARD_FAIL`。

但 `StopPolicy.evaluate()` 的调用方只有 `ToolLoopExecutor`，主 Chat 两套循环均未使用。`HARD_FAIL` 当前也没有在执行器中形成独立分支。

这表明本项目不是缺少停止策略，而是策略与主循环断层。

## 4. 差距与决策

| 能力 | Grok Build | EVERYDAYAIONE | 决策 |
|---|---|---|---|
| 唯一主循环 | Session 内统一 | 主 Chat 两套 + 专业循环 | 收口新内核 |
| 模型流事件 | 独立 Drainer + 5 秒 Barrier | 直接 Sink/WS，异步保存 | 引入事件顺序契约 |
| 传输重试 | 强类型、抖动、HTTP 重建 | Provider/外层分散 | 统一 Retry Taxonomy |
| 上下文恢复 | Session 压缩后 resubmit | 部分专业循环支持 | 融合升级 |
| 工具并发 | 全并发 + 资源锁 | 读并发、写串行 | 保留默认，增量资源锁 |
| 工具顺序 | 保留调用顺序和索引 | 最终按 ID 排序 | 修正为 ordinal |
| 空输出恢复 | Sampler 强类型重试 | 仅旧 Web 有一次恢复 | 迁移统一 |
| 停滞检测 | Doom/Todo/Completion 多层 | 专业循环已有，主 Chat 未接 | 提取共享策略 |
| Completion | 结构化工具要求 | 无主循环契约 | 引入结果级证据 |
| 结构化输出 | 原生 Schema/Tool 自纠错 | 未统一 | 采用 |
| 墙钟预算 | 主要依赖超时和 max_turns | 有 600 秒墙钟 | 保留并强化 Deadline |
| 最终合成 | Turn 完成逻辑 | 预留一轮、15 秒合成 | 保留现有优势 |
| Steer/Cancel | Session 原生 | 旧 Web 较完整，新内核不足 | 迁移至 Session 命令 |

## 5. 推荐目标 Model Loop

```text
ModelLoop.run(AgentRunContext)
├─ before_model()
├─ sample_with_retry()
├─ normalize_model_output()
├─ emit ordered stream events
├─ if terminal candidate
│  └─ CompletionVerifier
├─ before_tools()
├─ ToolScheduler
│  ├─ policy/permission
│  ├─ concurrency limit
│  ├─ resource conflict keys
│  └─ cancellation/deadline
├─ append structured observations
├─ StallPolicy / BudgetPolicy
├─ checkpoint()
└─ continue | wait_input | wrap_up | completed | cancelled | failed
```

必须统一的类型：

- `ModelTurnOrdinal`
- `ToolCallOrdinal`
- `ModelStopReason`
- `RunStopReason`
- `RetryClass`
- `ToolObservation`
- `CompletionEvidence`
- `LoopDecision`

模型的 `finish_reason` 不能直接等于 Run 终态；它只是 CompletionVerifier 的输入之一。

## 6. 边界与验收场景

| 场景 | 预期 |
|---|---|
| 模型纯文本结束 | Verifier 允许后 completed |
| finish_reason=length | 不伪装完成；压缩、续写或部分结果 |
| 模型空响应 | 有限重采样，事件中标明 attempt |
| Tool Call JSON 分片 | 按 index 稳定合并并校验 |
| 同轮多段生图 Prompt | 保留 ordinal，可按成本策略限并发 |
| 读、写、读 | 写作为资源屏障，结果顺序可重放 |
| 两个不同资源写入 | 声明资源键后可安全并行 |
| 相同文件并发写 | 同一资源锁串行 |
| 单工具超时 | 返回 timeout Observation，不丢 Tool Result 配对 |
| 用户拒绝确认 | 进入 waiting/rejected 明确状态，不当普通工具错误 |
| 用户取消模型流 | 终止 Provider 请求并保存部分输出 |
| 用户取消长工具 | 传播取消；不可取消工具标记 cancelling 并等待调和 |
| Steer 到达工具执行中 | 持久命令排队，在安全点注入下一模型轮 |
| Provider 5xx | 只重试模型采样，不重复已经成功的工具 |
| 整个 Run 重试 | 从 Checkpoint 恢复，幂等键阻止副作用重复 |
| 连续同错 | StallPolicy 有限恢复后 wrap-up |
| Completion 工具失败 | 不能只按“调用过”判成功 |
| 预算耗尽 | 生成部分结果和未完成原因，记录独立 stop reason |
| Wrap-up 也超时 | 使用确定性部分结果卡片，不再次无限重试 |

## 7. 候选影响范围

- `backend/services/handlers/chat/execution_engine.py`
- `backend/services/handlers/chat/stream_loop.py`
- `backend/services/handlers/chat/stream_session.py`
- `backend/services/handlers/chat/stream_runner.py`
- `backend/services/handlers/chat/stream_lifecycle.py`
- `backend/services/handlers/chat_tool_mixin.py`
- `backend/services/handlers/chat_tool_helpers.py`
- `backend/services/agent/tool_loop_executor.py`
- `backend/services/agent/execution_budget.py`
- `backend/services/agent/stop_policy.py`
- Provider Adapters、Actor Run、WebSocket/Event、Task Retry 和 Checkpoint

当前不修改上述实现。最终拆分必须等 Policy、ToolBridge、Persistence 与端到端重试链路完成后确定。

## 8. 第一轮证据

已核验：

- Grok Turn 主循环、Sampler 错误分类和默认参数。
- Grok Completion Recovery、StructuredOutput、工具并发与资源锁。
- 本项目新旧两套主 Chat 循环及调用方。
- 本项目工具增量、并发分批、确认等待、预算和 wrap-up。
- `ToolLoopExecutor` 与 StopPolicy 的实际接入范围。

既有结构风险：

- `backend/services/agent/tool_loop_executor.py` 为 756 行，超过 500 行硬阈值。
- Model Loop 行为在三处实现，后续不能通过继续增加分支解决。
- 本阶段只记录，不提前拆分。

尚待后续核验：

- 外层 Actor Task 的具体重试是否可能重复工具副作用。
- Policy Hook 的完整前后顺序。
- 工具幂等、事务和异步媒体任务的取消语义。
- UI Event 的持久序号和断线恢复。
- Goal Verifier 与 Model Loop CompletionVerifier 的职责边界。
