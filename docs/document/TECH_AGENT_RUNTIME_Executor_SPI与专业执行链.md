# Agent Runtime Executor SPI 与专业执行链

> 状态：总体设计 / 第一轮冻结
> 日期：2026-07-18
> 范围：Action 经 Policy 允许后，如何被专业 Executor 接受、执行、恢复、结算并形成 Artifact
> 前置：Action 状态机、Policy、Context、数据库 RPC 与事件协议

## 1. 结论

EverydayAI 采用“统一外壳、专业执行器”，不建设万能 Executor：

```text
Action + PolicyReceipt + CapabilityEnvelope
  -> ExecutorRegistry.resolve()
  -> Executor.prepare()
  -> Dispatcher.submit()
       -> Completed
       -> Accepted(TaskRef)
       -> Rejected
       -> Unknown(TaskRef)
  -> progress / callback / poll
  -> reconcile
  -> ActionResult
  -> Artifact + CostSettlement + RuntimeEvent
```

统一的是身份、状态、幂等、取消、超时、事件和结果信封；图片、视频、ERP、文件、Sandbox、MCP、图表等仍保留自己的 Provider、专业状态和补偿策略。

核心原则：

1. Executor 只消费 Action 和受限 Capability，不直接读取完整会话。
2. 超过 1 秒且需要跨进程恢复的外部任务优先返回 `Accepted`。
3. 网络超时不等于未执行，`Unknown` 是非终态且必须对账。
4. Provider 接受成功后，模型循环、WebSocket 或当前 Worker 断开都不影响任务继续。
5. Action、Attempt、Provider request、Artifact 和 Cost reservation 使用稳定 lineage。
6. 结果分为 model、display、artifact、audit 四种视图，不再混成 `str | AgentResult`。
7. Plugin/MCP 只能实现受限 SPI，不能绕过 Core Policy、Artifact 和审计。

## 2. 项目上下文

### 2.1 架构现状

当前 `ToolExecutor` 通过多个 Mixin 和 `_handlers` 字典按工具名分发，返回值可能是字符串、ToolOutput 或 AgentResult。文件、ERP、Sandbox、媒体各有专业实现，这是应保留的资产。媒体 Handler 已具备数据库任务、Webhook 优先、Poll 兜底、积分结算和 OSS/Workspace 持久化；但聊天 `MediaToolMixin` 另有阻塞 90/300 秒的同步生成链。ToolLoop 再从结果中提取 `emit_payloads`，形成第二条产物通道。

### 2.2 可复用模块

| 模块 | 复用方向 |
|---|---|
| `ToolExecutor` | 迁移期 CompatibilityExecutor |
| `ErpToolMixin/Dispatcher` | ERP 专业 Executor |
| `FileExecutor/ResourceManifest` | 文件范围和路径安全 |
| `SandboxExecutor/emit_payloads` | 沙盒与结构化产物捕获 |
| 图片/视频 Handler | 异步 Media Executor 主链 |
| `BackgroundTaskWorker` | 迁移为 Media Reconciler Worker |
| `TaskCompletionService` | 完成消费与单终态适配 |
| `CreditService` | CostLedgerAdapter |
| `ArtifactLedger/ContentPart` | 结果证据与展示适配 |

### 2.3 设计约束

- 保留 Conversation Actor 的 claim/lease/fencing。
- 生产异步事实存 PostgreSQL，Redis 只通知、限流或短锁。
- 所有执行必须有有效 PolicyReceipt。
- 群聊 Workspace、真实发言人结算和 ResourceManifest 规则不变。
- 不要求所有工具异步化；本地快速读取可原地完成。
- Provider 不支持幂等/取消/状态查询时必须如实声明。

### 2.4 当前冲突

- `generate_image/video` 存在同步工具与异步任务两条主链。
- Provider submit 成功而本地任务落库失败可能形成孤儿任务。
- ERP 写入幂等依赖 Redis 参数哈希和有限 TTL。
- 完成锁续期失败不立即失去处理权，可能双完成。
- 图片与视频 Workspace 行为不一致。
- ToolOutput、AgentResult、字符串和 emit payload 没有单一结果协议。

## 3. 执行语义分类

| 类型 | 示例 | 默认模式 | 核心约束 |
|---|---|---|---|
| `immediate_read` | 天气、知识搜索、本地 ERP 查询 | 同 Worker | timeout、来源、范围、缓存 |
| `local_render` | ECharts、Mermaid | 同 Worker/前端投影 | schema、确定性、无外部副作用 |
| `sandbox_job` | Python、文件分析 | 前台有界/后台 | 沙盒、资源、流输出、进程树取消 |
| `resource_mutation` | 文件写删、ERP 写入 | 持久 Action | 锁、幂等、审计、补偿 |
| `async_generation` | 图片、视频 | 持久后台 | 计费、回调、轮询、OSS、对账 |
| `external_action` | 发消息、部署、日程 | 持久后台 | 强授权、外部 ACK、Unknown |
| `remote_extension` | MCP Tool | 由 descriptor 决定 | 网关、租户隔离、远端信任 |
| `child_run` | Subagent | 独立 Run | 上下文、能力、预算、父级唤醒 |

执行语义由版本化 ExecutorDescriptor 声明，不由工具名字符串临时猜测。

## 4. ExecutorDescriptor

```json
{
  "executor_type": "media.image",
  "revision": 3,
  "action_kinds": ["media.image.generate"],
  "mode": "async_generation",
  "max_inline_ms": 1000,
  "timeouts": {
    "prepare_ms": 2000,
    "submit_ms": 30000,
    "execution_ms": 600000,
    "reconcile_ms": 30000
  },
  "idempotency": "adapter",
  "query_status": true,
  "cancel": "best_effort",
  "progress": true,
  "callback": true,
  "resource_key_strategy": "workspace+action",
  "concurrency_pool": "image_provider",
  "result_schema_revision": 2
}
```

枚举：

- `idempotency`: `native | adapter | none`
- `cancel`: `supported | best_effort | unsupported`
- `query_status`: boolean
- `mode`: 上一节八类

未登记 Executor 不可 dispatch。MCP 动态 Executor 由 Gateway 生成保守 descriptor；远端声明只能收紧，不能降低平台风险和 timeout。

## 5. 核心 SPI

```python
class Executor:
    descriptor: ExecutorDescriptor

    async def prepare(
        self, action: ActionRequest, capability: CapabilityEnvelope
    ) -> PreparedExecution: ...

    async def submit(
        self, prepared: PreparedExecution, attempt: ActionAttempt
    ) -> SubmissionOutcome: ...

    async def cancel(
        self, task_ref: TaskRef, attempt: ActionAttempt
    ) -> CancelOutcome: ...

    async def query(
        self, task_ref: TaskRef, attempt: ActionAttempt
    ) -> QueryOutcome: ...

    async def materialize(
        self, completion: ProviderCompletion, attempt: ActionAttempt
    ) -> ActionResult: ...
```

并非每个实现都支持全部方法。Registry 根据 descriptor 决定是否调用；不允许用空实现假装支持。

### 5.1 `ActionRequest`

包含：

- `action_id/run_id/model_step_id/tool_call_id`
- `action_kind/tool_name/tool_revision`
- 规范化 `arguments` 与 `arguments_hash`
- `actor/org/channel/workspace_scope`
- `resource_manifest_refs`
- `policy_receipt_id/authorization_grant_id`
- `cost_reservation_id`
- `deadline_at/priority`
- `parent_action_id/batch_id/ordinal`

Executor 不接收 messages、PromptBuilder 或用户完整历史。需要正文时通过 CapabilityEnvelope 对指定 ref 执行受控 Get。

### 5.2 `CapabilityEnvelope`

只包含本次 Action 所需最小能力：

- 可读/可写资源 refs 和 resource revision；
- 临时 Provider credential handle，不是明文密钥；
- 网络域名、方法和流量限制；
- Workspace 根和允许路径；
- CPU、内存、磁盘、输出预算；
- Policy obligations；
- Cost reservation 上界；
- 事件和 Artifact writer capability。

Envelope 有 action/attempt 绑定、过期时间和不可扩张 scope。

## 6. SubmissionOutcome

统一四种：

```text
Completed(ActionResult)
Accepted(TaskRef, initial_progress)
Rejected(ExecutionError)
Unknown(TaskRef?, ExecutionError, reconciliation_required=true)
```

### Completed

执行已确定结束，结果可进入 materialize/commit。适合天气、本地查询、图表等。

### Accepted

Provider 或后台系统已受理，持久化：

- provider request ID；
-查询/取消/callback locator；
- accepted timestamp；
-专业初始状态；
-下一次 reconcile 时间。

### Rejected

明确未受理，例如参数无效、权限拒绝、Provider 4xx 且确认没有创建任务。只有明确未发生副作用才可安全释放 reservation。

### Unknown

无法证明是否受理，例如 submit timeout、连接在响应前断开。不得立即重提或退款；进入 `Action=unknown`，按 descriptor 的 query/dedup/人工对账策略处理。

## 7. TaskRef 与稳定身份

```json
{
  "action_id": "uuid",
  "attempt_id": "uuid",
  "executor_type": "media.image",
  "provider": "kie",
  "provider_request_id": "opaque",
  "idempotency_key": "action:uuid",
  "status_locator": {"kind": "provider_task"},
  "callback_correlation": "opaque",
  "accepted_at": "timestamp"
}
```

身份规则：

- 用户的一次语义动作对应一个 Action。
- 技术重试创建新 Attempt，沿用 Action。
- Provider 原生支持幂等时传 `action_id`；否则 Adapter 建立持久映射。
- 批量三张图是三个 Action，共享 batch/grant，ordinal 固定展示顺序。
- Artifact、Usage、Cost 和 RuntimeEvent 必须关联 Action；随机临时 task ID 不再是主身份。

## 8. Dispatcher 与 Worker

### 8.1 Dispatcher

职责：

1. 校验有效 PolicyReceipt、Action 状态和 attempt owner。
2. resolve descriptor，获取资源冲突键和并发池。
3. 原子创建 Attempt/Outbox；异步 submit 由 Worker 消费。
4. 对 `max_inline_ms <= 1000` 的确定性操作可同 Worker 执行。
5. 将 Outcome 通过 RPC 推进 Action，追加 RuntimeEvent。

Dispatcher 不解释业务结果、不直接扣费、不拼 UI ContentPart。

### 8.2 Worker 池

首期不拆微服务，仍是模块化单体中的独立进程入口：

- `agent-action-worker`：通用短执行和资源变更。
- `media-action-worker`：图片/视频 submit/reconcile/materialize。
- `sandbox-worker`：隔离代码与文件处理。
- `mcp-gateway-worker`：外部连接。
- Conversation Actor Worker 继续负责模型 Run，不阻塞等待媒体。

按 `executor_type + org_id` 限流；资源写操作再按 resource key 串行。Worker lease 默认 60 秒、每 20 秒续期；实际值可由 15–300 秒限制范围内按任务类型覆盖。

## 9. Progress、Callback 与 Reconcile

统一 ExecutorEvent：

```json
{
  "action_id": "uuid",
  "attempt_id": "uuid",
  "kind": "progress",
  "provider_status": "generating",
  "progress": 0.42,
  "message_code": "MEDIA_GENERATING",
  "occurred_at": "timestamp",
  "dedup_key": "provider-event-id"
}
```

规则：

- Provider callback 先写 Callback Inbox，再由 reconciler 关联 TaskRef。
- Poll 与 Callback 竞争同一数据库 completion fencing token。
- progress 可合并；状态变化、accepted、unknown、terminal 必须持久。
- Provider progress 不可信且可倒退，Projection 展示单调 `max(previous,current)`，审计保留原值。
- callback 必须验签、校验时间窗和 body 大小。
- 无 callback 时按指数退避 + jitter poll，不使用所有任务固定节拍。

首期轮询参数：

| 类型 | 初始 | 最大 | 总期限 |
|---|---:|---:|---:|
| 图片 | 2 秒 | 30 秒 | 10 分钟 |
| 视频 | 5 秒 | 60 秒 | 30 分钟 |
| MCP 长任务 | 2 秒 | 60 秒 | descriptor 决定 |
| ERP unknown | 5 秒 | 60 秒 | 10 分钟后人工/延迟对账 |

Webhook 正常时保留 120 秒低频兜底扫描；这是恢复扫描，不是每个 Task 的主定时器。

## 10. 超时、重试与 Unknown

超时分层：

- `prepare_timeout`：本地验证/资源准备。
- `submit_timeout`：只覆盖提交请求。
- `execution_deadline`：外部任务允许完成的期限。
- `materialize_timeout`：下载、OSS、Workspace 和结果转换。
- `reconcile_deadline`：确认 Unknown 结果。

错误分类：

| 类别 | 是否新 Attempt | 是否换 Provider |
|---|---|---|
| validation/policy | 否 | 否 |
| explicit provider rejection | 按策略 | 可 |
| rate limit | 延迟同 Attempt 或新 Attempt | 可 |
| transient before send | 可安全重试 | 可 |
| submit outcome unknown | 禁止直接重提 | 对账后决定 |
| execution failed terminal | 新 Attempt 需策略和预算 | 可 |
| materialization failed | 不重新生成，只重试 materialize | 否 |

默认退避：`min(60s, 2s × 2^attempt) + 0..20% jitter`，最多 5 次基础设施重试。业务生成重试次数由工具策略控制，默认图片 2、视频 1；每次新 Provider 生成都是新 Attempt 和明确成本处理。

## 11. 取消

取消是一项请求，不直接等于完成：

```text
requested -> cancel_requested
  -> provider confirms -> cancelled
  -> provider rejects/already done -> reconcile terminal
  -> no cancel support -> running/unknown + user no longer waiting
```

- 未 submit 的 Action 可原子取消并释放 reservation。
- accepted Action 调用 Provider cancel；无能力时继续对账。
- 父 Run 取消不自动删除已完成 Artifact。
- 批量取消逐 Action 处理，已完成项保持成功。
- Sandbox 必须终止进程树；无法证明终止则 Attempt unknown/failed，不复用 Kernel。
- 外部消息/ERP 写入已发生时不能用“取消”伪装回滚。

## 12. ActionResult 与四种视图

```json
{
  "action_id": "uuid",
  "status": "completed",
  "result_schema": "media.image.v2",
  "model_view": {
    "summary": "生成 1 张图片",
    "artifact_refs": ["artifact:uuid"]
  },
  "display_view": {
    "content_parts": [{"kind": "image", "artifact_id": "uuid"}]
  },
  "artifact_view": {
    "artifacts": [{"artifact_id": "uuid", "role": "primary"}]
  },
  "audit_view": {
    "provider": "kie",
    "model": "model-id",
    "duration_ms": 48210
  },
  "usage": {},
  "settlement": {}
}
```

- `model_view`：小、结构化、可进入 Context。
- `display_view`：渠道无关 ContentPart，不带短期签名 URL 身份。
- `artifact_view`：完整产物、lineage、checksum、Workspace/OSS locator。
- `audit_view`：脱敏参数、Provider、耗时、错误分类和策略 revision。

`emit_payloads` 迁移为 display/artifact adapter；兼容期仍可生成旧 payload，但不再是事实源。Result materialize 成功与 Action terminal 在同一原子边界写 Artifact、Usage、Settlement、RuntimeEvent 和 Projection Outbox。

## 13. 专业执行器边界

### 13.1 天气、搜索、知识

- `immediate_read`，默认总 timeout 30 秒。
- Search 保留来源和 query；Fetch 单独实现 SSRF、DNS、redirect、MIME 和 10 MB 上限。
- 外部结果是数据，不是指令。
- 无结果返回 completed empty，不伪装异常。

### 13.2 ECharts / Mermaid

- `local_render`，AI 只生成结构化 spec/DSL。
- 后端验证 schema/大小，前端专业渲染。
- 结果形成 Chart/Diagram Artifact；渲染失败可降级源码或表格。
- 无成本 reservation，不需要异步 Provider task。

### 13.3 文件

- Asset ID + revision 是资源身份，路径只是 locator。
- 默认只访问 ResourceManifest，workspace scope 需显式授权。
- 同资源写串行，读可并行；删除采用可恢复 Trash Artifact。
- 大文件分块/索引，不把完整内容返回 model view。

### 13.4 Sandbox

- 前台执行不超过 15 秒；预计更长立即 Accepted/background。
- hard execution timeout 初值 120 秒。
- CPU/内存/磁盘/output 由 CapabilityEnvelope 限制。
- emit chart/file/image/table 直接形成 Artifact，不依赖 stdout 扫描。
- Worker 重启后 Kernel 变量不可恢复，必须显式重新执行依赖步骤。

### 13.5 ERP

- 查询与写入分开 descriptor。
- 读取携带数据范围 Capability；分页/导出形成 Artifact。
- 写入以 Action ID 为持久幂等主键，Redis 锁只作并发优化。
- 响应丢失进入 Unknown，优先按 provider request/业务单号对账。

### 13.6 图片与视频

- 聊天工具只创建 Action，不再同步等待 Provider。
- 复用异步 Handler 的 submit、callback/poll、完成和结算能力。
- 图片/视频统一进入 Workspace + OSS Artifact。
- Provider 完成但 OSS 失败只重试 materialize，不重新生成。
- 模型可继续其他步骤；需要等待时 Run 进入 waiting_actions。

### 13.7 MCP

- Executor 实际位于 MCP Gateway，不在主 Worker 建任意 stdio。
- 每个远端工具生成保守 descriptor、schema hash 和 server revision。
- 同步上限默认 30 秒；长任务必须有 TaskRef/查询协议，否则超时进入 Unknown，不允许占 Worker 6000 秒。
- 结果仍转为标准 ActionResult/Artifact。

### 13.8 Subagent

Subagent 是 Child Run Executor，不是进程内函数调用。submit 创建 SubRun，Accepted 返回 child_run_id；完成后生成 SubRunResult 和 Artifact refs，通过 parent wake event 继续父 Run。

## 14. Hook 边界

执行生命周期 Hook：

```text
before_prepare
after_prepare
before_submit
after_accept
on_progress
before_materialize
after_result
on_error
```

规则：

- Core Policy 位于不可绕过边界，不是普通 Hook。
- Hook 可拒绝、添加义务、脱敏或观察，不能扩大 Capability。
- fail-closed：安全/合规 Hook；fail-open：纯 telemetry Hook。
- Hook timeout 初值：同步 500 ms，异步观察 2 秒。
- 外部 Plugin Hook 不在事务内执行，只通过 Outbox 接收脱敏事件。

并发参数、边界场景、方案比较、影响范围及迁移验收见
`TECH_AGENT_RUNTIME_Executor并发边界与迁移附录.md`。
