# 统一 Validation 与 Recovery Runtime 技术设计

> 状态：方案已确认，待实施
> 日期：2026-07-19
> 任务等级：A级
> 对标基线：Grok Build `c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 范围：全项目通用校验、错误分类、有界恢复、完成判断和终态回执

## 1. 目标与非目标

### 1.1 目标

建设唯一、通道无关、工具无关、模型无关的 Validation 与 Recovery Runtime：

```text
Tool Call
→ 输入校验
→ 专业 Executor
→ 终态结果归一化
→ Validation
→ Recovery Decision
→ 模型 Observation / Completion
→ 现有终态 Owner 原子提交
```

统一 Runtime 必须：

1. 为每个 Tool Call 生成唯一终态结果。
2. 区分参数纠错、工具语义纠错、Provider 传输重试和上下文恢复。
3. 统一错误分类、重复失败检测、无进展检测和恢复预算。
4. 让 Web、企微、Conversation Actor 和专业 Agent 消费同一判断语义。
5. 保持系统结构化事实与模型可读文本分离。
6. 不接管专业工具执行、渠道展示、积分结算或 Actor 终态。

### 1.2 非目标

本阶段不包含：

- SQL、表格、ERP 或其他业务专属校验。
- Skill Registry、Skill 选择或自动注入。
- 最终自然语言数字正则校验。
- 任意业务结论正确性的自动证明。
- 模型选择或 Provider 降级策略重写。
- 全量 Tool Catalog 重构。
- 通用 Goal、Subagent 或 Background Action Runtime。

## 2. 项目上下文

### 2.1 架构现状

Web 与企微聊天已统一进入 Conversation Actor，并通过
`execute_chat()` 运行通道无关模型循环。主 Chat 已使用 `RuntimeState`、
`ArtifactLedger`、`CompletionGate` 和 `ExecutionBudget`，但没有消费现有
`StopPolicy` 的工具失败分类和重复失败治理。

ERP Agent 与 ScheduledTaskAgent 共用 `ToolLoopExecutor`，已具备
`ResultClass`、`FailureTracker`、`StopPolicy` 和 wrap-up，但没有复用主 Chat
的 Artifact、Completion 和 Context Receipt 语义。Provider 失败后的模型切换又位于
Actor Executor 外层，形成第三种恢复边界。

因此当前问题不是缺少能力，而是同一错误在不同循环里具有不同分类、重试、停止和
展示行为。

### 2.2 可复用模块

- `AgentResult`：现有结构化工具结果。
- `validate_tool_args()`：工具参数清洗和基础 Schema 校验。
- `ExecutionBudget`：时间、轮次和 Token 总预算。
- `FailureTracker`：连续失败和相同错误追踪。
- `CompletionGate`：必需产物完成判断。
- `ArtifactStore` / `ArtifactLedger`：完整工具事实和证据旁路。
- `ConversationExecutionService`：Claim、Lease、Fencing 和原子终态。
- `ExecutionSink`：Web、企微等通道过程投影。

### 2.3 设计约束

- Validation Runtime 必须是纯判断层，不执行工具、不写数据库。
- 专业 Executor 继续拥有业务执行和业务错误的事实。
- Conversation Actor 继续是 Chat 终态唯一 Owner。
- 失败工具结果仍必须进入模型上下文，保持 Tool Call/Result 配对。
- 自动恢复必须受 `ExecutionBudget` 约束。
- 副作用结果未知时默认禁止自动重放。
- 新旧执行入口必须消费同一结果与决策类型，不能复制判断代码。
- 所有新增文件不超过 500 行，函数不超过 120 行，复杂度不超过 15。

### 2.4 潜在冲突

- `stop_policy.py` 主要依赖关键词推断，不能继续作为权威分类来源。
- `validate_tool_args()` 对动态工具缺少 Schema 时会跳过校验。
- `AgentResult.metadata["retryable"]` 已存在，但没有成为统一分类事实。
- 主 Chat 与 `ToolLoopExecutor` 对空输出、失败和 wrap-up 的行为不同。
- Actor 外层模型切换重试不能与工具语义纠错合并。
- 旧技术文档把结构合法的 Evidence 描述为业务准确，需同步纠正。

## 3. Grok Build 对标映射

| Grok Build | EVERYDAYAIONE 目标 |
|---|---|
| Typed Tool Args / `try_parse()` | `validate_tool_call()` |
| `prepare_dispatch()` | 参数规范化、权限与执行前检查 |
| 专业 Tool 实现 | 保留专业 Executor |
| `ToolRunResult.output` | `ValidatedToolResult.system_fact` |
| `ToolRunResult.prompt_text` | `ValidatedToolResult.observation` |
| Progress | 现有 `tool_step=running` |
| 唯一 Terminal | 每个 call ID 唯一 `ValidatedToolResult` |
| Structured error feedback | `RecoveryObservation` |
| Completion Requirement | `CompletionGate` + 成功终态检查 |
| Sampler retry | 现有 Provider 传输/换模型层 |
| Session context recovery | 现有上下文压缩恢复 |
| Doom loop / max turns | FailureTracker + ExecutionBudget |
| Turn bookkeeping | Actor 原子提交 + Validation Receipt |

不照搬 Grok 的本地 Session 存储、偏大的重试次数和长 Tool Call 阻塞策略。

## 4. 核心协议

### 4.1 ValidationStage

```python
class ValidationStage(StrEnum):
    INPUT = "input"
    EXECUTION = "execution"
    OUTPUT = "output"
    COMPLETION = "completion"
```

### 4.2 ResultClass

```python
class ResultClass(StrEnum):
    SUCCESS = "success"
    RETRYABLE = "retryable"
    NEEDS_INPUT = "needs_input"
    AMBIGUOUS = "ambiguous"
    PARTIAL = "partial"
    FATAL = "fatal"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
```

`UNKNOWN` 表示外部副作用可能已发生但未取得确定终态，不等同于普通失败。

### 4.3 RecoveryDecision

```python
class RecoveryDecision(StrEnum):
    CONTINUE = "continue"
    RETRY_MODEL = "retry_model"
    RETRY_TRANSPORT = "retry_transport"
    NEEDS_INPUT = "needs_input"
    FINALIZE = "finalize"
    WRAP_UP = "wrap_up"
    FAIL = "fail"
    CANCEL = "cancel"
```

### 4.4 ToolEffect

```python
class ToolEffect(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    ASYNC_EXTERNAL = "async_external"
```

本阶段不建设完整 Tool Catalog。Effect 优先读取工具已有元数据；未声明时采用安全默认：
失败调用可回填模型，但禁止 Runtime 自动重放同一调用。

### 4.5 ValidatedToolResult

```python
@dataclass(frozen=True)
class ValidatedToolResult:
    tool_call_id: str
    requested_tool_name: str
    effective_tool_name: str
    stage: ValidationStage
    result_class: ResultClass
    effect: ToolEffect
    terminal: bool
    system_fact: object
    observation: object
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    retry_after_seconds: float | None = None
    fingerprint: str = ""
```

约束：

- `system_fact` 只供 Runtime、Artifact、审计和持久化消费。
- `observation` 只供模型上下文消费。
- 两者来自同一个归一化结果，但不得互相反向解析。
- `terminal=True` 后同一 call ID 不得再写第二个终态。

### 4.6 ValidationReceipt

```python
@dataclass(frozen=True)
class ValidationReceipt:
    task_id: str
    model_step: int
    tool_call_id: str
    tool_name: str
    stage: ValidationStage
    result_class: ResultClass
    decision: RecoveryDecision
    attempt: int
    fingerprint: str
    reason_code: str
    duration_ms: int
```

Receipt 不包含完整用户数据、工具正文、密钥、URL或代码，只记录校验与决策事实。

## 5. 错误来源与恢复边界

| 错误来源 | 示例 | 决策 Owner | 允许动作 |
|---|---|---|---|
| Tool Call 输入 | JSON、必填参数、类型错误 | Validation Runtime | `RETRY_MODEL` |
| 工具业务结果 | 工具返回结构化失败 | Validation Runtime + Model Loop | `RETRY_MODEL/NEEDS_INPUT/FAIL` |
| Tool 传输 | 只读HTTP瞬时失败 | Executor策略 | 安全时`RETRY_TRANSPORT` |
| Provider采样 | 429、5xx、流中断 | Adapter/Actor Executor | 现有模型重试或降级 |
| 上下文超长 | Provider context error | Context Runtime | 压缩后恢复一次 |
| 副作用未知 | Provider已受理但响应丢失 | 专业Executor/终态Owner | 禁止盲重放，进入`UNKNOWN` |
| Actor ownership | Lease/Fencing丢失 | Conversation Actor | 取消执行，不提交 |

同一错误只能由一个 Owner 执行恢复，防止嵌套重试形成乘法放大。

## 6. 恢复状态机

```text
VALIDATE_INPUT
├─ invalid → OBSERVE_ERROR → RETRY_MODEL / NEEDS_INPUT / FAIL
└─ valid → EXECUTE

EXECUTE
├─ progress → EXECUTE
└─ terminal → VALIDATE_OUTPUT

VALIDATE_OUTPUT
├─ success → RECORD_PROGRESS → CHECK_COMPLETION
├─ retryable → TRACK_FAILURE → RETRY_MODEL / WRAP_UP
├─ needs_input → NEEDS_INPUT
├─ unknown side effect → WRAP_UP / FAIL
├─ cancelled → CANCEL
└─ fatal → WRAP_UP / FAIL

CHECK_COMPLETION
├─ satisfied → FINAL_SYNTHESIS
├─ missing + budget → CONTINUE
└─ missing + exhausted → WRAP_UP / FAIL
```

### 6.1 默认恢复预算

- 同一错误指纹最多允许模型纠正 1 次。
- 单次 Run 连续工具失败 3 次进入 wrap-up。
- 上下文超长最多恢复 1 次。
- 空输出最多恢复 1 次。
- 所有恢复共享原 `ExecutionBudget`，不得另建隐藏预算。
- Provider传输重试继续使用适配器/Actor现有配置，不计入工具语义纠错次数，但计入墙钟。

这些值先固定为代码默认，不新增环境变量；生产证据充分后再进入Config Catalog。

## 7. 两套循环的消费方式

### 7.1 主 Chat

```text
prepare_tool_turn
→ validate_tool_calls
→ execute approved calls
→ normalize_tool_results
→ validate terminal results
→ append paired observations
→ RecoveryController.decide
→ continue / final synthesis / wrap-up / fail
```

`execute_chat()` 只负责推进循环。校验、分类、指纹和决策全部下沉到独立 Runtime。

### 7.2 ERP与定时任务

`ToolLoopExecutor` 删除自己的结果分类决策实现，改为消费同一
`ValidationRuntime.observe_turn()`。专业 Hook、工具扩展和结果缓存保持不变。

### 7.3 Actor外层

`ChatGenerationExecutor._execute_with_retry()` 只处理 Provider/模型级失败，不处理
工具结果失败。工具语义纠错必须在同一次 `execute_chat()` 内完成。

## 8. 持久化设计

### 8.1 原则

Validation Runtime 不直接写数据库。它只产生 Receipts，并随纯执行结果返回，由当前
终态 Owner 原子提交。

### 8.2 新表

新增 `conversation_validation_receipts`：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | Receipt ID |
| conversation_id | UUID | NOT NULL, FK | 会话 |
| org_id | UUID | NULL | 组织隔离 |
| task_id | UUID | NOT NULL, FK | Actor任务 |
| model_step | INTEGER | >= 0 | 模型步骤 |
| tool_call_id | TEXT | NOT NULL | 工具调用 |
| tool_name | TEXT | NOT NULL | 有效工具名 |
| stage | TEXT | CHECK | 校验阶段 |
| result_class | TEXT | CHECK | 结果分类 |
| decision | TEXT | CHECK | 恢复决策 |
| attempt | INTEGER | >= 0 | 同类尝试次数 |
| fingerprint | TEXT | NOT NULL | 脱敏错误指纹 |
| reason_code | TEXT | NOT NULL | 稳定原因码 |
| duration_ms | INTEGER | >= 0 | 阶段耗时 |
| context_revision | BIGINT | NOT NULL | 提交revision |
| created_at | TIMESTAMPTZ | DEFAULT NOW | 创建时间 |

唯一约束：

```text
(task_id, model_step, tool_call_id, stage, attempt)
```

索引：

- `(conversation_id, context_revision DESC, created_at DESC)`
- `(task_id, model_step, tool_call_id)`
- `(result_class, created_at DESC)`，仅非成功结果

### 8.3 原子提交

新增 `commit_generation_turn` 重载参数 `p_validation_receipts JSONB`。原12参数重载
保留兼容；Actor新链使用13参数重载。RPC在既有 lease、task status 和 base revision
校验通过后，与消息、ContextItem、Artifact、Evidence、Receipt一起提交。

数据库限制：

- 每个任务最多100条Receipt。
- 每条Receipt必须为JSON对象且字段白名单通过。
- 不允许正文、arguments、output或stack trace进入表。
- RLS与现有上下文表保持一致。

失败到达Actor `_fail()` 且没有 `GenerationOutcome` 时不强求Receipt落库；任务错误码、
错误消息和结构化日志仍是失败事实。可恢复工具失败通常会在Run内wrap-up并正常提交
Receipt。

## 9. 文件结构

### 9.1 新增

- `backend/services/agent/runtime/validation/types.py`：枚举与不可变协议。
- `backend/services/agent/runtime/validation/normalizer.py`：旧结果到统一终态。
- `backend/services/agent/runtime/validation/input.py`：Tool Call输入校验适配。
- `backend/services/agent/runtime/validation/tracker.py`：失败与进展追踪。
- `backend/services/agent/runtime/validation/recovery.py`：纯决策状态机。
- `backend/services/agent/runtime/validation/observation.py`：模型错误Observation。
- `backend/services/agent/runtime/validation/runtime.py`：单Run门面。
- `backend/migrations/140_validation_recovery_runtime.sql`：Receipt表和提交RPC。
- `backend/migrations/140_validation_recovery_runtime_rollback.sql`：回滚。
- 对应的窄单元测试文件，避免单个测试文件继续膨胀。

放入现有 `runtime/validation/`，因为它属于通用Agent Runtime，不属于Chat、ERP或
某个工具。

### 9.2 修改

- `backend/services/agent/runtime/runtime_state.py`
- `backend/services/agent/runtime/completion_gate.py`
- `backend/services/agent/agent_result.py`
- `backend/services/agent/tool_args_validator.py`
- `backend/services/agent/stop_policy.py`
- `backend/services/agent/tool_loop_executor.py`
- `backend/services/handlers/chat/execution_engine.py`
- `backend/services/handlers/chat/tool_loop.py`
- `backend/services/handlers/chat/execution_result.py`
- `backend/services/handlers/chat/executor.py`
- `backend/services/conversation_execution.py`

`stop_policy.py` 在迁移完成后只保留wrap-up合成能力；分类、Tracker和决策迁入统一
Runtime。若全仓调用清零，后续再按项目规则删除死代码，不在迁移中保留双实现。

## 10. 连锁修改清单

| 改动点 | 影响范围 | 同步内容 |
|---|---|---|
| `ValidatedToolResult` | Chat与ToolLoopExecutor | 两套循环统一归一化 |
| `RecoveryDecision` | 循环控制 | 删除字符串/枚举混合判断 |
| `RuntimeState`增加validation | Stream setup、executor测试 | 每Run初始化与Receipt投影 |
| `ChatExecutionResult`增加receipts | Chat executor | 传递到GenerationOutcome |
| `GenerationOutcome`增加receipts | Actor commit | RPC增加参数 |
| `LoopResult`增加receipts | ERP、ScheduledTask | 调用方消费或记录 |
| 输入校验返回类型变化 | ToolLoopExecutor、ChatToolMixin | 从字符串错误改结构化结果 |
| Completion使用成功终态 | CompletionGate | 工具“调用过”不能等于完成 |
| Receipt持久化 | migration、RPC测试 | 原子提交、数量和字段校验 |
| 日志Schema | Chat、ERP、定时任务 | 统一task/tool/attempt/reason |

## 11. 技术栈、API与依赖

- 后端继续使用 Python 3.11、FastAPI、PostgreSQL和现有异步模型循环。
- 不新增第三方依赖，不新增面向前端或外部调用方的HTTP API。
- 前端消息协议保持兼容，只消费现有Tool Step与最终消息投影。
- 数据库只新增内部Receipt表和向后兼容的Actor提交RPC重载。
- 本阶段不需要UI设计或Zustand状态变更。

## 12. 实施与验收附录

边界场景、可观测性、测试矩阵、实施阶段、部署回滚、风险和验收标准见
`TECH_统一Validation与Recovery运行时_实施附录.md`。
