# Agent Runtime Policy、授权、成本与副作用设计

> 状态：总体设计 / 第一轮冻结
> 日期：2026-07-18
> 范围：Action 从“模型提出”到“允许进入 Executor”之间的强制决策边界
> 前置文档：`TECH_AGENT_RUNTIME目标架构与模块边界.md`、`TECH_AGENT_RUNTIME核心状态机.md`、`TECH_AGENT_RUNTIME数据库模型.md`

## 1. 结论

EverydayAI 需要一个独立、确定性、可恢复的 `Policy Engine`。它不是 Prompt，也不是工具函数里的 `if`，而是所有 Action 在调度前必须经过的唯一执行门：

```text
Action Requested
  -> Capability / Entitlement
  -> Schema / Resource Scope
  -> Risk / Side Effect
  -> Intent Authorization
  -> Cost / Budget Reservation
  -> Policy Decision
       allow -> Action Queued
       require_interaction -> Interaction Open
       deny -> Action Rejected
```

核心产品原则冻结为：

1. 用户明确要求“生成、执行、发送、导出”等，可以构成授权，不等于每次都弹窗。
2. “写、讨论、评价、翻译提示词”不构成执行授权。
3. 模型、Skill、MCP、Plugin、Hook、子 Agent 都只能提出 Action，不能给自己扩权。
4. 付费生成必须先完成成本预留；高风险外部动作必须满足更严格授权。
5. Policy 决策和授权必须持久化，断线、重启、重放后不得重新猜测。
6. 未登记工具、元数据缺失、策略服务异常时默认关闭，而不是默认安全。

这比 Grok Build 当前公开实现更适合本项目：保留其直观的
`Tool Call -> Permission -> ToolBridge`，但补齐 SaaS 所需的组织权限、数据范围、积分账本、持久交互和审计凭证。

## 2. 当前实现与断层

| 当前实现 | 已有能力 | 断层 |
|---|---|---|
| `config/chat_tools.py` | `SAFE/CONFIRM/DANGEROUS` 分类 | 未登记工具默认 `SAFE`；风险维度过于单一 |
| `ChatToolMixin._prepare_tool_arguments` | dangerous 工具发确认请求 | confirm 级只写日志；确认仅驻留进程内，超时固定 60 秒 |
| `PermissionMode` | `auto/ask/plan` 与提示词提醒 | 主要依靠模型遵守；`auto` 容易被误解为全部放行 |
| `PermissionChecker` | 职位、部门、权限点判断 | 未成为每个 Action 的统一前置条件 |
| `apply_data_scope` | 查询前注入数据范围 | 各工具自行接入，无法证明所有 Executor 都执行 |
| `CreditService` | 锁定、确认、退款 | 未统一绑定 Action、Policy Decision 和幂等生命周期 |
| WebSocket confirm | 用户可确认危险工具 | 无持久 Interaction；断线、重启和多端恢复不完整 |
| `ToolEntry` | domain、tags、priority 等目录元数据 | 缺少风险、权限、成本、幂等、取消、数据范围声明 |

当前问题不是“工具不能用”，而是工具目录、授权、权限、费用、确认、执行和恢复分别存在，模型调用后没有统一的可信执行契约。

## 3. 职责边界

### 3.1 Policy Engine 负责

- 汇总身份、租户、权限、数据范围、用户意图、工具元数据和成本。
- 返回确定性的 `allow / require_interaction / deny`。
- 生成不可变 `PolicyDecisionReceipt`。
- 声明执行义务：成本预留、沙盒、脱敏、审计、收件人限制等。
- 在 Action 重试、恢复和参数变化时判断是否需要重新决策。

### 3.2 Policy Engine 不负责

- 不理解自然语言；意图由 Agent 结构化，Policy 只验证证据和范围。
- 不执行工具；Dispatcher 仅接受有效的 allow receipt。
- 不直接扣费；Cost Ledger 执行预留、确认和退款。
- 不负责前端展示；Interaction 和 RuntimeEvent 由 Projection 展示。
- 不通过模型二次判断安全；强策略必须是代码与数据规则。

### 3.3 现有能力归属

```text
PermissionChecker   -> EntitlementProvider
apply_data_scope    -> ResourceScopeProvider / Executor obligation
CreditService       -> CostLedgerAdapter
PermissionMode      -> SessionPolicyPreference
SafetyLevel         -> 迁移为 ToolPolicyMetadata
WS confirm          -> Persistent Interaction Projection
```

## 4. 工具策略元数据

每个本地工具、MCP 工具、Skill 暴露动作和子 Agent 模板进入目录前，必须形成版本化 `ToolPolicyMetadata`：

```json
{
  "tool_name": "generate_image",
  "revision": 3,
  "permission_code": "media.image.generate",
  "side_effect": "paid_generation",
  "data_access": "user_private",
  "authorization": "explicit_intent",
  "cost": {"mode": "estimated", "unit": "credits"},
  "idempotency": "provider_or_adapter",
  "cancellation": "best_effort",
  "execution_zone": "external_provider",
  "argument_schema_hash": "sha256:...",
  "redacted_fields": ["prompt.private_references"]
}
```

枚举固定为：

| 字段 | 值 |
|---|---|
| `side_effect` | `none`、`local_reversible`、`paid_generation`、`external_reversible`、`external_irreversible` |
| `data_access` | `none`、`public`、`user_private`、`org_internal`、`sensitive` |
| `authorization` | `none`、`explicit_intent`、`persisted_interaction`、`preapproved_workflow`、`forbidden` |
| `cost.mode` | `none`、`fixed`、`estimated`、`variable` |
| `idempotency` | `native`、`adapter`、`none` |
| `cancellation` | `supported`、`best_effort`、`unsupported` |

目录注册失败规则：

- 本地工具缺元数据：启动检查失败，不能发布。
- 动态 MCP 工具缺元数据：默认 `persisted_interaction + external_irreversible + sensitive`。
- MCP Server 不得自行声明更低风险；平台策略只能保持或提高风险。
- Tool schema 或策略 revision 变化后，旧 allow receipt 不可用于新参数。

## 5. Policy 输入与输出协议

### 5.1 `PolicyEvaluationRequest`

| 字段 | 含义 |
|---|---|
| `action_id/run_id/session_id` | 事实对象 |
| `actor_id/org_id/channel` | 身份、租户、入口 |
| `origin_command_id/turn_id` | 用户原始命令证据 |
| `intent_receipt_id` | 结构化意图证据，可空 |
| `tool_name/tool_revision` | 工具及策略版本 |
| `arguments_hash` | 规范化参数哈希，不放完整敏感内容 |
| `resource_manifest` | 资源类型、ID、所属人、组织和数据级别 |
| `estimated_cost` | 数量、单位成本、上下界、币种或积分 |
| `permission_mode` | `auto/ask/plan` |
| `provenance` | model、skill、mcp、subagent、hook 调用来源链 |
| `existing_grant_ids` | 可复用的授权 |
| `goal_budget` | Goal 剩余 token、时间、成本和 Action 配额 |

### 5.2 `PolicyDecisionReceipt`

```json
{
  "decision_id": "uuid",
  "action_id": "uuid",
  "decision": "allow",
  "reason_codes": ["EXPLICIT_USER_INTENT", "ENTITLED", "COST_RESERVED"],
  "effective_scope": {"org_id": "uuid", "resource_ids": []},
  "grant_id": "uuid",
  "cost_reservation_id": "uuid",
  "obligations": ["sandbox", "audit", "redact_result"],
  "policy_revision": 7,
  "tool_revision": 3,
  "arguments_hash": "sha256:...",
  "evaluated_at": "timestamp",
  "expires_at": "timestamp"
}
```

Dispatcher 必须同时验证：

- receipt 属于当前 `action_id`；
- `arguments_hash`、tool revision、org 和 actor 一致；
- receipt 未过期、未撤销；
- 所有义务已满足；
- 付费 Action 已持有有效 reservation。

任何参数实质变化都创建新 Action 或重新评估，不允许修改旧参数后沿用授权。

## 6. 用户意图如何构成授权

### 6.1 `IntentReceipt`

Agent 把用户原始表达解析为结构化意图，但 receipt 必须保留可审计来源：

```json
{
  "source": "user_command",
  "turn_id": "uuid",
  "intent": "generate_image",
  "action_family": "media.image.generate",
  "object_refs": ["prompt_artifact:uuid"],
  "quantity": {"mode": "explicit", "value": 3},
  "constraints": {"aspect_ratio": "1:1"},
  "authorization_strength": "explicit_execute",
  "evidence_span_hash": "sha256:..."
}
```

模型输出本身不是授权证据。Policy 以原始用户命令、已解决 Interaction 或预批准工作流为根信任。

### 6.2 判定表

| 用户表达 | Intent | 执行 |
|---|---|---|
| “写三段海报提示词” | `author_prompt` | 只产出 Prompt Artifact |
| “评价/翻译这段提示词” | `transform_prompt` | 不生成图片 |
| “用这个提示词生成图片” | `generate_image` | 可直接授权付费生成 |
| “就按上面的方案生成” | `generate_image` + Artifact ref | 解析上一轮产物后执行 |
| “想几个方案，合适的话做出来” | 含糊条件 | 先规划；执行前 Interaction |
| Skill 中写“下一步生成图片” | Skill 建议 | 不构成授权 |

如果模型无法把代词稳定绑定到唯一 Artifact，必须交互，不得猜测。

### 6.3 多提示词与批量 Action

一条明确用户命令可以授权一批同类 Action，不需要逐张弹窗：

1. 如果用户显式给出 N 段提示词或明确数量 N，批次授权 `max_actions=N`。
2. “把这些都生成”中的“这些”必须绑定已持久化 Prompt Artifact 列表。
3. 每个子 Action 都保存自己的参数哈希，并派生绑定同一 `batch_grant_id`。
4. 成本按整批估算并一次预留，执行和退款仍逐 Action 记账。
5. 模型新增、复制或扩大数量，不在授权范围内，必须重新交互。
6. 首期硬上限建议：单批图片 16、视频 4；超过上限拆批并持久交互。

若用户只用了含糊复数且没有可数 Artifact，默认最多推导 4 个 Action；超过 4 个必须先展示数量和预计成本。

## 7. 风险与交互矩阵

| 类型 | 示例 | 明确用户指令 | 无明确指令 |
|---|---|---|---|
| 无副作用读取 | 天气、公开搜索 | 直接执行 | 可按 Goal 规划执行 |
| 私有/组织读取 | ERP 查询、文件读取 | 权限与范围通过后执行 | Goal 范围内可执行；敏感数据可要求交互 |
| 本地可逆 | 生成草稿、临时文件 | 直接执行 | 仅预批准 workflow 可执行 |
| 付费生成 | 图片、视频 | 成本可预留则不额外弹窗 | 必须 Interaction |
| 外部可逆 | 创建草稿、可撤销日程 | 精确对象和范围可授权 | 必须 Interaction |
| 外部不可逆 | 发消息、部署、删除、ERP 写入 | 默认持久 Interaction | 禁止 |

例外：组织可为固定 workflow 建立 `preapproved_workflow`，精确限制工具、参数模板、资源范围、有效期、调用者和预算。它不能成为全局“永不询问”。

## 8. `auto / ask / plan` 的正确语义

- `auto`：减少不必要打断，但仍执行全部强策略；绝不等于绕过权限、成本或高风险确认。
- `ask`：在强策略基础上扩大交互范围；付费生成、本地写入和外部动作均可要求确认。
- `plan`：只允许读取、分析和生成计划产物；任何副作用 Action 返回 `require_interaction`，用户确认退出后创建新授权。

非法模式必须降级到 `ask`，不能像当前实现降级到 `auto`。模式提示词只改善模型行为，Policy Engine 才是执行保障。

## 9. 权限与数据范围

策略顺序固定：

1. 验证 actor 属于 org，且 channel 与 session binding 一致。
2. 根据 `permission_code` 调用 EntitlementProvider。
3. 由 ResourceScopeProvider 生成 `effective_scope`。
4. Executor 只能拿到受限查询句柄或 Capability Token，不能拿全库后自行过滤。
5. 结果经 Data Egress Policy 判断能否进入模型上下文、Artifact 或外部通道。

群聊沿用现有 ExecutionScope 原则：不注入个人 Memory，资源放 channel Workspace，审计和付费仍绑定真实发言人。子 Agent 继承父 Run 的 scope 交集，不能扩大到父级未授权资源。

## 10. 成本与预算协议

付费 Action 的顺序固定为：

```text
estimate -> policy allow -> reserve -> dispatch
         -> success: commit actual
         -> failure before provider accept: release
         -> accepted/unknown: reconcile
         -> partial batch: per Action commit/release
```

规则：

- reservation 唯一绑定 `action_id + attempt_generation`，重复请求返回原记录。
- 预留和 Action 进入 `queued` 必须在同一数据库原子边界，或通过 Outbox 保证最终一致。
- Provider 已接受但结果未知时不得退款，Action 进入 `unknown` 后先对账。
- 实际成本高于预估时，只能在授权上界内补扣；超上界暂停并 Interaction。
- Goal 预算、组织预算、用户余额三者取最小有效上界。
- 用户明确指定数量时，该数量进入授权；价格变化或模型变化超过组织阈值时仍需交互。

首期参数建议：

| 参数 | 初值 |
|---|---:|
| `policy_evaluation_timeout_ms` | 2,000 |
| `interaction_ttl_seconds` | 900 |
| `intent_grant_ttl_seconds` | 900 |
| `image_batch_hard_limit` | 16 |
| `video_batch_hard_limit` | 4 |
| `ambiguous_plural_max_actions` | 4 |
| `entitlement_cache_ttl_seconds` | 60 |
| `policy_receipt_ttl_seconds` | 300 |

Policy 应为本地确定性服务；数据库或账本不可用时，付费、写入和外部 Action 失败关闭。只读公开工具也不绕过 Action 持久化。

## 11. Hook、Skill、MCP 与 Subagent

权限只能收窄：

```text
effective = user/org policy
          ∩ session mode
          ∩ goal budget
          ∩ parent run scope
          ∩ skill declaration
          ∩ mcp server policy
          ∩ hook restrictions
```

- Skill 可声明所需工具、步骤和资源，但不能自动批准。
- MCP 动态发现只增加“可请求能力”，不增加用户 entitlement。
- Subagent 获得显式 Capability Envelope；默认继承父权限的交集和剩余预算。
- Hook 可以拒绝、改写为更小范围、添加义务；不能把 deny 改为 allow。
- ToolOutput 中的文本永远视为不可信数据，不能触发下一步授权。

这正面解决提示词注入：“工具结果要求删除文件/调用付费模型”只能成为模型观察，不能成为授权根。

## 12. Interaction 与恢复

`require_interaction` 必须创建数据库 `agent_interactions`：

- UI、企微或未来渠道只是同一 Interaction 的 Projection。
- 用户响应通过 CAS 从 `open -> resolved/expired/cancelled`。
- approve 创建有范围的 AuthorizationGrant，再重新评估原 Action。
- 断线后客户端按 RuntimeEvent replay 恢复卡片。
- 超时不向模型伪装成工具失败；Action 保持可解释的 rejected/expired 原因。
- 同一 Interaction 多端响应只接受第一个合法结果。

确认内容默认展示工具、动作、目标资源、数量、预计成本、风险和授权有效范围；敏感原始参数只显示脱敏摘要。

## 13. 审计、隐私与可观测性

每次决策记录：

- policy/tool revision、reason codes、grant、scope 和 obligations；
- 决策延迟、Interaction 率、拒绝率、误触发申诉和成本差异；
- `arguments_hash` 与脱敏摘要，不默认记录完整提示词、文件内容或凭证；
- 从用户命令到子 Agent/MCP 的 provenance 链；
- 执行前后 receipt 一致性校验结果。

关键指标：

- `policy_decision_total{decision,reason,tool}`
- `policy_interaction_total{channel,outcome}`
- `policy_denied_total{reason}`
- `cost_reservation_total{state,tool}`
- `authorization_scope_violation_total`
- `dispatch_without_valid_receipt_total`，目标恒为 0

## 14. 失败、并发与降级

| 场景 | 处理 |
|---|---|
| 两个 Worker 同时评估 | `action_id + policy_revision + arguments_hash` 唯一 receipt |
| 确认与取消竞态 | Interaction CAS；取消后 grant 不可生效 |
| 参数在确认后变化 | receipt 哈希不匹配，禁止调度 |
| 权限在等待期间撤销 | 调度前二次校验高风险 entitlement version |
| 余额预留后 Worker 崩溃 | reservation 由恢复器按 Action 状态对账 |
| Provider 回调先于 accepted | 复用 Callback Inbox，不提前退款 |
| MCP 元数据更新 | revision 失配，重新评估 |
| Policy DB 不可用 | 付费/写入/外部操作失败关闭 |
| UI 不支持确认卡 | 渠道投递文本交互；仍写同一 Interaction |

## 15. 计划修改范围（实施阶段）

本轮不修改代码。未来实施预计影响：

| 模块 | 计划职责 |
|---|---|
| `backend/services/agent_runtime/policy/` | evaluator、rules、receipts、intent grants |
| `backend/services/agent_runtime/catalog/` | ToolPolicyMetadata 与动态 MCP 覆盖规则 |
| `backend/services/agent_runtime/cost/` | CreditService 适配、reservation/reconcile |
| `backend/services/agent_runtime/interactions/` | 持久 Interaction 与 Grant |
| `backend/services/permissions/` | EntitlementProvider、ResourceScopeProvider |
| `backend/config/chat_tools.py` | 兼容读取，逐步退出 SafetyLevel 主判定 |
| `backend/services/handlers/chat_tool_mixin.py` | 删除进程内确认所有权，迁移到 Runtime |
| `backend/services/handlers/permission_mode.py` | 仅保留偏好和提示，不再承担安全边界 |
| `backend/config/tool_registry.py` | 接入版本化策略元数据 |
| 数据库迁移 | Policy receipt、Grant、Interaction、Cost reservation 约束 |
| Web/企微 Projection | 恢复式确认卡与批量成本摘要 |

计划函数/类边界：

- `PolicyEvaluator.evaluate(request) -> PolicyDecisionReceipt`
- `IntentAuthorizer.issue_from_command(...) -> IntentReceipt`
- `AuthorizationService.resolve(...) -> AuthorizationGrant`
- `EntitlementProvider.check(...)`
- `ResourceScopeProvider.resolve(...)`
- `CostLedger.reserve/commit/release/reconcile(...)`
- `PolicyGuard.assert_dispatchable(action, receipt)`

## 16. 迁移顺序与验收门禁

1. 为现有工具补齐策略元数据，只观察不拦截；未知工具告警。
2. 新 Policy Engine shadow evaluate，与旧 SafetyLevel 决策对比。
3. 先接图片/视频成本链，验证明确意图无需额外弹窗、提示词讨论不执行。
4. 将 dangerous 进程内确认迁移为持久 Interaction。
5. 接 ERP/文件的数据范围和敏感数据出口。
6. MCP、Skill、Subagent 仅在 Capability Envelope 完成后开放。
7. 删除旧确认所有权和默认 SAFE 逻辑。

上线门禁：

- 任何 Executor 都无法绕过有效 receipt。
- 重放同一 Action 不重复扣费、不重复外部副作用。
- 批量生成逐 Action 记账，部分失败精确退款。
- 断线、重启后确认和结果可恢复。
- `plan` 模式强制无副作用，`auto` 不绕过强策略。
- ToolOutput/Skill/MCP 文本不能成为授权根。
- 组织权限、数据范围和 channel scope 均有拒绝测试。

## 17. 本轮边界

本轮冻结 Policy 大方向、协议和建议参数，不冻结每个业务工具的最终风险表。下一轮设计 Context 与 Executor SPI 时，需要继续确定：

- 哪些数据进入模型上下文、Artifact、Memory、Workspace 或仅留审计；
- Executor 如何声明输入、输出、进度、回调、取消和对账能力；
- 天气、图表、图片、视频、文件、ERP、MCP 的具体元数据清单；
- Web 与企微如何投影同一个 Interaction 和批量 Action。
