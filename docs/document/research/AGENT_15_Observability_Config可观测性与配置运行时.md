# AGENT 15：Observability / Config 可观测性与配置运行时

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：日志、Trace、Metrics、Usage、成本、告警、反馈、配置优先级、热更新和快照
> 后续专项：Testing / Operations、端到端链路继续核验

## 1. 结论摘要

Agent 可观测性不能只靠搜索文本日志。一次用户目标会跨越 Session、Run、模型、Tool、
SubRun、Provider callback、Artifact、积分结算和多通道投递，必须共享稳定关联键。

推荐目标结构：

```text
Runtime
  ├─ TelemetryContext
  ├─ typed TelemetryEvent / Metric
  ├─ UsageLedger
  └─ EffectiveConfigSnapshot
          ↓
Observability Pipeline
  ├─ structured logs
  ├─ traces/spans
  ├─ bounded metrics
  ├─ audit records
  ├─ error/alert pipeline
  └─ feedback/evaluation
```

配置目标：

```text
Config Catalog → Layered Resolver → Validation/Policy Clamp
              → EffectiveConfigSnapshot → Run/Action
```

观测数据不是业务事实源；业务终态、积分和 Artifact 仍由数据库状态与 RuntimeEvent
决定。配置也不能在运行中被任意字段热替换：UI 偏好可实时更新，安全、授权、模型、
工具、成本和 Executor 参数必须按 Run/Action 冻结 revision。

## 2. Grok Build 可观测性实现

### 2.1 独立 Telemetry Engine

Grok 将遥测从 Shell 抽成 `xai-grok-telemetry`，内部包括：

- typed events。
- Session task-local context。
- Prompt timing 与 inference latency。
- product analytics。
- external OTEL logs/metrics。
- Sentry。
- debug/unified log。
- trace upload。
- redaction 与 schema validator。
- feedback signal。

调用方发一个 typed event，由独立 gate 分流到内部产品分析和用户自己的 OTEL
collector，避免每个业务点分别拼装不同 payload。

### 2.2 Telemetry 模式

Grok 有三级模式：

| 模式 | 行为 |
|---|---|
| Disabled | 不发送产品遥测 |
| SessionMetrics | 仅 metadata lifecycle，不含内容 |
| Enabled | 完整产品事件与分析 |

未知配置值按 Disabled 处理。Session lifecycle 可在 `SessionMetrics` 和 `Enabled`
下发送，产品事件只在 `Enabled` 下发送。外部 OTEL 有独立 gate，不与内部产品遥测
混为一个开关。

外部 OTEL 启用需要双重 opt-in：

1. `GROK_EXTERNAL_OTEL=1`。
2. logs/metrics 至少一个 exporter 为 `otlp` 或 `console`。

只设置 master switch 或只设置 exporter 都不会启用。内容 gate 默认关闭，远程策略
只能关闭，不能在运行时打开。

### 2.3 关联上下文

Grok 使用 task-local `TelemetryCtx`：

```text
session_id
prompt_index / turn_number
prompt_id
```

每次 Prompt 开始轮换 `prompt_id`。发事件时同步 snapshot context，避免后台发送任务
与后续 Turn 递增竞态。锁竞争时宁可缺 turn number，也不阻塞业务执行。

Session span 的 `session_id` 字段还是 debug firehose 的路由契约；源码用测试固定字段名。
这表明关联字段和事件名属于数据协议，不能当普通日志文案随意改名。

### 2.4 Typed Schema

External OTEL schema 固定版本 `v1`，事件包括：

```text
session_start / session_end / user_prompt / turn_completed
api_request / api_error
tool_result / tool_decision
mcp_server_connection
permission_mode_changed
skill_activated / plugin_loaded
compaction / subagent / auth / internal_error / model_switched
```

属性键也是闭合集合，例如：

```text
session.id / prompt.id / event.sequence
organization.id / deployment.id
model / permission_mode / outcome / duration_ms
input_tokens / output_tokens / reasoning_tokens / cache_read_tokens
tool_name / decision / error_category / status_code
```

新增字符串属性必须进入 allowlist 和固定测试；事件重命名或删除需要 schema version
升级。Metric attribute 使用更严格的小集合，防止高基数。

### 2.5 隐私与脱敏

Grok 的外发管道采用 default-deny：

- 数字、布尔可直接进入。
- 字符串 key 必须在 allowlist。
- URL 只保留 `scheme://host[:port]`。
- secret shape、home path、username 被清洗。
- free-text event name 被替换为静态 callsite。
- 未知复杂值按内容处理并拒绝。
- export 前再次校验；违规 log 丢单条，违规 metric 丢整个 batch。

用户 Prompt 和 Tool details 分别需要显式 content gate。Exporter 发现 schema bug 时
宁可丢遥测，不允许泄漏。

Sentry 同样 `send_default_pii=false`，清洗 message、exception、stack path、
breadcrumb、extra 和 tag，移除 cwd/server name。Trace sample rate 当前为 1%，关闭前
最多 flush 2 秒。

### 2.6 Timing 与 Usage

Grok 分解 Prompt latency：

```text
total_ms
pre_model_ms
mcp_wait_ms
tool_collection_ms
model_call_ms
```

Inference 还记录 TTFT 和 inter-token latency percentiles。Usage 区分：

- input。
- output。
- reasoning。
- cache read。
- cache creation 在 Provider 归一化中计入完整 prompt。

纯 cache hit 即使 uncached input 为 0 仍必须发 usage。Subagent usage 回折 Parent 时
保留 model breakdown，并对归因不完整做 fail-closed 标记，而不是静默记 0。

### 2.7 Feedback

Grok Feedback Manager 将以下职责分开：

- Session signals 本地 actor。
- heuristics 判断何时请求反馈。
- 定时同步 signals，默认 60 秒。
- 用户 feedback 本地持久化。
- 可选远端提交。
- telemetry 只记录是否有文本、评分、模型和是否主动邀请，不上传反馈正文。

关闭时上传队列默认最多 drain 30 秒。反馈远端失败不会阻塞 Session，本地记录仍保留。

### 2.8 Unified Log 与 Debug Trace

Grok 同时保留：

- 人类诊断用 tracing/debug log。
- typed analytics event。
- external OTEL。
- Session trace artifact。

它们共享相关键但职责不同。Telemetry field 的稳定 enum 值被 dashboard 查询依赖，
源码明确用测试固定，不能因“文案优化”修改。

## 3. Grok Build 配置实现

### 3.1 配置层级

Grok TOML 从低到高：

1. system managed config。
2. user managed config。
3. user config。
4. user requirements。
5. system requirements。
6. macOS MDM requirements。

各层先应用与版本匹配的 override，再 deep merge。Campaign 可覆盖普通配置，但应用后
重新叠加 requirements，保证低信任实验不能突破管理员约束。

部分功能还有：

- CLI flag。
- environment variable。
- remote settings。
- model-specific override。
- project config。

每个字段必须在 resolver 中明确优先级，不存在“全系统统一 env 总是最高”的简单规则。

### 3.2 Requirements 与 Fail-closed

管理员 Requirements 可限制用户配置。`fail_closed` 环境变量只能收紧为 true，不能
关闭管理员设置的 fail-closed。高权限层包含无效 version override 时可拒绝启动。

TOML 解析错误不会把源代码行输出到日志，因为该行可能包含 secret；只报告行、列和
错误类型。

### 3.3 Hot Reload

Config watcher 默认 1 秒 debounce：

- 忽略 Access 事件，防止读取配置引发自激 reload storm。
- 只窄范围 non-recursive watch，避免扫描 `node_modules/.git/target`。
- 同批去重。
- 内容 hash 去重 mtime-only 变化。
- global 与 project change 分开路由。
- 新配置先完整解析和验证，失败保留 last-known-good。

不同字段产生 typed `ConfigUpdate`，例如 Auth、MCP、Memory、Skills、Models 和 UI。
不是把整个 Config 对象直接替换到所有 Session。

### 3.4 配置生效边界

Grok 同时存在：

- 立即 UI 更新。
- Catalog reload。
- 新 Agent rebuild 后生效。
- 新 Session 才生效。
- 进程启动时一次性解析。

这是正确方向：是否热更新由字段语义决定。局限是多个 resolver/环境变量仍分散在不同
crate，若没有 effective snapshot，事后还原某次执行配置仍困难。

## 4. EVERYDAYAIONE 可观测性现状

### 4.1 已有强项

项目已有：

- Loguru 文件日志，每日 rotation，应用日志保留 30 天。
- 数据一致性日志保留 90 天。
- Sentry error/performance/profile，API 与 Sync Worker 均初始化。
- `error_logs` 表、指纹聚合、未解决错误 upsert、致命企微告警。
- Error sink 队列上限 5000、5 秒/50 条批量。
- ToolAudit：Task/Conversation/User/Org/Tool/Turn/耗时/状态/参数 hash/Token/Trace。
- `knowledge_metrics`：任务、模型、耗时、Token、重试、用户、组织和 params。
- Agent `trace_id` ContextVar。
- Langfuse trace/span/generation Null Object 降级。
- 取消专项指标、ERP 同步健康检查、KIE 余额告警和一致性巡检。
- Web 前端统一 logger。
- 企微反馈事件入口。

这些模块可作为统一管道的 Sink 或 Adapter，不应全部删除重写。

### 4.2 当前断层

1. `trace_id` 实际用 task ID，只有字符串 ContextVar，没有 Run/Action/Attempt/Provider
   span context。
2. `logger.bind(trace_id=...)` 调用结果未建立统一 scoped logger，很多日志仍靠文案拼
   `task=... | org=...`。
3. Langfuse 只接入 Chat setup 和 ERP Agent 少量位置，Generation usage 生命周期不完整。
4. ToolAudit 和 KnowledgeMetrics 各自定义字段，Action lifecycle、Provider callback、
   Artifact、积分 settlement 和 channel delivery 无统一 schema。
5. Tool audit fire-and-forget，进程退出或 DB 短暂失败会丢失。
6. Error sink 满时静默丢弃，没有 dropped counter；自身 DB 失败只写 warning。
7. 致命错误通过 message regex 分类，错误文案变化会让告警失效。
8. Sentry 当前未设置 `before_send` 脱敏，默认 SDK 行为不足以证明 Prompt、URL、Token、
   文件路径不会进入错误事件。
9. `knowledge_metrics.params` 可装任意 JSON，存在敏感数据和高基数漂移风险。
10. 企微赞踩目前只写日志，没有绑定 Message/Run/Model/Config revision 的反馈事实。
11. 前端 logger 仅 console；生产客户端错误、协议 gap 和渲染降级没有受控采集。
12. 指标大多是 DB row 或日志文案，缺少真正 Counter/Gauge/Histogram 和 SLO。

### 4.3 配置现状

`Settings` 使用 Pydantic Settings：

- `.env` + process environment。
- 类型校验。
- `lru_cache` 进程单例。
- secrets 和大量 Runtime 参数共处一个 359 行类。

另有：

- `OrgConfigResolver`：org encrypted value > system default。
- enterprise-only 凭证未配置时不允许降级，防跨租户泄漏。
- JSON/Python Tool/Model registry。
- Conversation、Memory、ERP 等数据库配置。
- 前端 build-time env 和 local settings。
- 多个模块级常量。

`Settings` 不热更新，修改环境变量需要重启。OrgConfig 有进程级 key cache，变更可能
需要重启或手工失效。当前没有统一 Config Catalog、source metadata、revision、
effective snapshot、字段 owner 和生效策略。

### 4.4 当前参数冲突

同一概念存在多处值：

- 图片等待：Settings 180 秒、Image Agent 120 秒、Task 常量 10 分钟。
- 视频等待：Settings 600 秒、Task 常量 30 分钟。
- Agent 轮次：`agent_loop_max_turns=8`、`budget_max_turns=15`、ERP Agent 8。
- Context 容量和预算同时分布于多组字段。
- Sentry 初始化在 API 与 Sync Worker 重复。

它们不一定都错误，因为阶段语义可能不同；问题是字段名没有体现 `submit/read/poll/
reconcile/run` 的边界，也没有有效配置回执，排障时无法知道一次 Action 实际取了哪个值。

## 5. 目标 Observability 架构

### 5.1 TelemetryContext

统一上下文：

```text
trace_id
org_id / user_id
conversation_id / turn_id
run_id / parent_run_id
action_id / attempt_id
interaction_id / artifact_id
provider / external_operation_id
config_snapshot_id
```

Context 在 Run claim 时构建；创建后台 task、SubRun、Provider callback 和 Outbox
delivery 时显式序列化/恢复，不能只依赖 ContextVar 自动继承。

Trace ID 是关联标识，不是数据库幂等键；Run/Action ID 仍使用业务稳定 ID。

### 5.2 Span 模型

```text
Run span
  ├─ context.assemble
  ├─ model.inference
  ├─ action.policy
  ├─ action.execute
  │   └─ provider.request / provider.poll / artifact.persist
  ├─ subrun
  ├─ settlement
  └─ channel.delivery
```

Span terminal 必须区分：

```text
completed / failed / cancelled / timeout / rejected / unknown
```

业务 Unknown 不能被观测层映射成 error 后自动重试。

### 5.3 Typed Telemetry Schema

定义版本化事件，不允许调用方传任意属性：

```text
run.started / run.completed
model.request / model.response
action.decision / action.result
provider.request / provider.reconcile
artifact.ready
interaction.requested / answered
usage.recorded / settlement.completed
delivery.result
config.applied / config.rejected
projection.gap / replay.result
```

每个事件声明：

- required/optional fields。
- allowed labels。
- content classification。
- cardinality class。
- sink routing。
- retention。
- owner 和 schema version。

RuntimeEvent 与 TelemetryEvent 可以来自同一业务 transition，但不能共用一张万能表：
前者用于恢复与 UI，后者用于统计诊断。

### 5.4 Metrics 与 SLO

首批低基数指标：

| 指标 | 类型 | 主要标签 |
|---|---|---|
| `agent.run.count` | Counter | type,outcome |
| `agent.run.duration` | Histogram | type,outcome |
| `gen_ai.client.token.usage` | Counter | provider,model,token_type |
| `gen_ai.client.operation.duration` | Histogram | provider,model,operation |
| `agent.action.count` | Counter | capability,outcome,risk |
| `agent.interaction.wait` | Histogram | kind,outcome |
| `agent.event.delivery_lag` | Histogram | channel,event_class |
| `agent.event.replay_gap` | Counter | channel |
| `agent.config.rejected` | Counter | source,reason |
| `agent.telemetry.dropped` | Counter | sink,reason |

禁止把 user ID、conversation ID、Run ID、URL、错误正文放进 metric label；它们只进入
Trace/Log/Audit。

核心 SLO 候选：

- Run terminal success/known-outcome rate。
- P50/P95/P99 TTFT 与 terminal latency。
- Action Unknown rate。
- Provider accepted 后 reconcile 延迟。
- Interaction 恢复成功率。
- Event replay gap 和 terminal delivery 延迟。
- 积分 hold 未结算数量。

### 5.5 Usage 与成本

Usage 采用 append ledger：

```text
usage_id / run_id / action_id / attempt_id / subrun_id
provider / model
input / output / reasoning / cache_read / cache_write
provider_units / estimated_cost / billed_cost / credits
source / completeness / recorded_at
```

模型、SubRun、媒体 Provider 和工具外部费用都归因到 Action Attempt。Provider 未返回
usage 时标记 estimated/incomplete，不记成 0。计费事务可以消费 Usage，但 Telemetry
Sink 失败不能影响已提交结算。

### 5.6 Logs、Audit、Alert 分工

- Log：诊断过程，结构化字段，有限保留。
- Trace：一次 Run 的因果时间线。
- Metric：聚合 SLO 和告警。
- Audit：谁在何时请求/批准/执行了什么，较长保留且不可随意修改。
- Alert：从 Metric/ErrorCode/Invariant 触发，不以自然语言 regex 为主。
- Feedback：用户对结果的监督信号，绑定事实对象。

现有 Error sink 保留为兼容入口，逐步把关键问题改成 typed error code 和 invariant
event；message regex 只作为旧日志兜底。

## 6. 隐私与数据治理

数据分级：

| 类别 | 示例 | 默认策略 |
|---|---|---|
| public metadata | model、tool name、outcome | 可观测 |
| identifiers | Run/Action/Org | Trace/Audit，Metric 禁止 |
| customer content | Prompt、Tool args/result | 默认不外发 |
| secrets | Token、Cookie、Credential | 全部 Sink 禁止 |
| sensitive business | ERP 数据、文件路径 | 摘要/hash/Artifact ref |

实施三道门：

1. emit-time typed allowlist。
2. sink-specific content gate 和 scrubber。
3. export-time validator，违规 fail-closed。

日志 helper 必须阻止直接记录 Tool arguments、Prompt、Provider response 和签名 URL。
Sentry、Langfuse、Error DB、企微告警和前端上报分别配置独立允许字段。

Config Runtime、Feedback、边界风险、方案对比、差距矩阵与实施顺序见
[AGENT_15_Config_Feedback配置与反馈附录.md](AGENT_15_Config_Feedback配置与反馈附录.md)。
