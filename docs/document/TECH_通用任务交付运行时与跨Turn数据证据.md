# 通用任务交付运行时与跨 Turn 数据证据技术设计

> 状态：已完成
> 日期：2026-07-18
> 任务等级：A级
> 对标基线：Grok Build `c68e39f60462f28d9be5e683d9cbe2c57b1a5027`

> 2026-07-18 架构纠偏：保留原模型上下文、工具循环、ERP、沙盒和最终表达方式。
> 数据安全能力收口为最终提交前的通用 Evidence Guard；它不理解业务意图、不替模型
> 计算或渲染。失败以结构化 Observation 返回原模型循环纠正，连续失败才阻断。
> 合并来源：通用任务交付运行时方案 + ERP 跨 Turn 数据上下文方案

## 1. 最终结论

两个方案合并为一个标准，不建设两套并行运行时：

```text
用户请求
→ RunContract
→ 模型 / 工具动作
→ ArtifactLedger
→ DomainPolicy
→ CompletionGate
→ 模型最终草稿
→ Evidence Guard
→ Actor 原子提交
→ 下一 Turn ContextSnapshot
```

- 通用运行时负责单次 Run 内的目标、产物、证据和完成判断。
- 跨 Turn 数据上下文是通用运行时的 `DATA_RESULT` 领域扩展。
- 原有 `AgentResult`、模型 observation、emit payload、消息 ContentPart 和渠道投递协议保持不变。
- 新能力从现有工具结果消费点旁路登记证据，不要求现有工具改为另一种返回格式。
- 只有 Conversation Actor 提交链持久化跨 Turn 数据证据；其他执行循环首期只接入 Run 内观察和完成判断。
- 所有面向用户的精确数据结论必须来自已验证证据，不能由模型重新心算。

## 2. 为什么当前链路会答错

当前 ERP 查询结果在本轮仍以结构化 `AgentResult.data` 存在，但封闭 Turn 在后续上下文中主要恢复：

1. assistant 可见文本；
2. 压缩后的 tool digest；
3. 当前消息相关资源。

原始数据行和统计口径没有作为可复算证据进入下一 Turn。用户追问“除了拼多多”“按有效订单”“重新计算”时，模型只能从前一条自然语言回答中抽取数字并自行求和，导致：

- 把总订单与有效订单混用；
- 排除条件丢失；
- 口径修改后沿用旧总数；
- 明细之和与结论不一致；
- 多次“重新计算”得到不同答案。

所以问题不在单个提示词，而在跨 Turn 消费链缺少结构化、带口径、可验证的数据证据。

## 3. 唯一运行时模型

### 3.1 RuntimeState

`RuntimeState` 是一次 Run 的唯一治理对象：

```python
@dataclass
class RuntimeState:
    contract: RunContract
    ledger: ArtifactLedger
    data_working_set: DataWorkingSet | None = None
    completion: CompletionDecision | None = None
```

它不替代 Conversation Actor、ExecutionBudget、StopPolicy 或 ToolExecutor，只为这些现有组件提供统一的只读判断依据。

### 3.2 RunContract

`RunContract` 表示本轮必须交付什么。空合同沿用原行为：

```python
@dataclass(frozen=True)
class RunContract:
    required_artifacts: frozenset[ArtifactKind]
    optional_artifacts: frozenset[ArtifactKind]
    forbidden_artifacts: frozenset[ArtifactKind]
    required_capabilities: frozenset[CapabilityKind]
    policy_ids: tuple[str, ...]
    source: ContractSource
    confidence: float
```

### 3.3 ArtifactLedger

`ArtifactLedger` 登记工具执行产生的结构化证据。首期产物类型包括：

- `TEXT`
- `TABLE`
- `CHART`
- `FILE`
- `DATA_RESULT`

Ledger 只保存 Run 内证据和验证状态，不成为前端消息协议，不改变 emit payload。

### 3.4 DataResultArtifact

ERP 和其他业务数据查询统一映射为：

```python
@dataclass(frozen=True)
class DataResultArtifact:
    artifact_id: str
    source: str
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    query_scope: Mapping[str, object]
    metric_definitions: Mapping[str, str]
    fingerprint: str
    tool_call_id: str | None
```

输入只来自结构化字段：

- `AgentResult.data`
- `AgentResult.columns`
- `AgentResult.file_ref`
- `AgentResult.source`
- `AgentResult.metadata`

禁止从 `summary`、Markdown 表格或模型回答反向解析为可信数据。

### 3.5 DomainPolicy

通用接口：

```python
class DomainPolicy(Protocol):
    policy_id: str

    def validate_artifact(
        self,
        contract: RunContract,
        evidence: ArtifactEvidence,
        payload: Mapping[str, object],
    ) -> PolicyResult: ...

    def evaluate_completion(
        self,
        contract: RunContract,
        snapshot: ArtifactSnapshot,
    ) -> PolicyResult: ...
```

`DataAccuracyPolicy` 负责：

- 数据列和口径字段存在性；
- 数值字段类型；
- 过滤条件与分组字段合法性；
- 计算结果与输入行确定性复核；
- 明细合计与最终结论一致性；
- 空数据、查询失败和计算失败的区分；
- 禁止没有结构化证据支持的精确数字通过最终提交边界。

### 3.6 CompletionGate

`CompletionGate` 统一输出：

- `CONTINUE`
- `FINALIZE`
- `FALLBACK`
- `NEEDS_INPUT`
- `BLOCKED`

空合同不改变当前“无 Tool Call 即结束”的行为。非空合同只有在必需产物 ready 且策略通过后才能 `FINALIZE`。

## 4. 原消费方式与修改后消费方式

### 4.1 工具结果

原链路：

```text
ToolExecutor
→ AgentResult
→ apply_tool_results()
→ unpack_tool_result()
→ 模型 observation
→ emit payload / 用户展示
```

修改后：

```text
ToolExecutor
→ AgentResult
├→ 原 apply_tool_results() / observation（不变）
├→ 原 emit payload / 用户展示（不变）
└→ ArtifactCollector
   → ArtifactLedger
   → DomainPolicy
```

`ArtifactCollector` 是旁路观察者，不接管也不重写原返回值。

### 4.2 本轮结束

原链路：

```text
ChatExecutionResult
→ GenerationOutcome
→ commit_generation_turn()
→ assistant message + usage + credits + tool digest
```

修改后：

```text
ChatExecutionResult(runtime_snapshot)
→ GenerationOutcome(runtime_snapshot)
→ commit_generation_turn(runtime_projection)
→ assistant message + 原字段
→ data evidence projection + runtime audit
```

提交仍由同一个 Actor fencing token 保护，消息完成、扣费、任务完成和证据投影必须原子成功或原子失败。

### 4.3 下一 Turn

原链路：

```text
base_revision
→ ContextSnapshot(history + summary + resources)
→ PromptBuilder
```

修改后：

```text
base_revision
→ ContextSnapshot(
     history + summary + resources + data_context
   )
→ PromptBuilder
→ 模型获得数据目录和口径摘要
→ 模型继续按原方式选择 ERP、沙盒或直接回答
```

`ContextSnapshot` 冻结本 Turn 可见的数据 revision。PromptBuilder 不直接查数据库，避免一次生成过程中读到变化状态。

### 4.4 Universal Evidence Guard

包含精确业务数字的最终回答仍由模型生成。提交前执行：

1. 从最终草稿提取通用数值 Claim；
2. 结合用户问题和声明局部上下文，与 Ledger 中对应字段的 ready `DATA_RESULT`
   结构化数值、行数及沙盒派生结果匹配，不能用其他字段中的相同数字冒充证据；
3. 不一致时丢弃草稿，把 `evidence_validation_error` 返回原模型循环；
4. 最多纠正两次，仍失败才输出不含可疑数字的固定降级说明。

Guard 不认识订单、平台或自然语言关键词。模型继续负责理解、工具选择、沙盒计算和
最终表达；最终一轮在验证通过前缓冲，未经确认的精确结果不得流向用户。

## 5. 接口接线清单

| 生产方 | 现有接口 | 新增消费方 | 兼容要求 |
|---|---|---|---|
| ERP / 通用工具 | `AgentResult` | `ArtifactCollector` | 工具签名和返回类型不变 |
| `apply_tool_results` | 原始 tool result tuple | `RuntimeState.ledger` | observation 内容不变 |
| emit payload | `AgentResult.emit_payloads` | 原投递链 + ledger | ContentPart 不变 |
| `execute_chat` | `ChatExecutionResult` | `GenerationOutcome` | 新字段有缺省值 |
| `GenerationOutcome` | result/usage/cost/digest | Actor commit | 旧构造调用继续有效 |
| Actor commit | `commit_generation_turn` | evidence projection | 同一事务、同一 fencing |
| `ContextSnapshot` | history/summary/resources | `data_context` | 新字段缺省为空 |
| `PromptBuilder` | snapshot | data context renderer | 无数据时输出完全不变 |
| Evidence Guard | 最终模型草稿 + ArtifactLedger | `GuardReceipt` | 不注册模型工具，不产生前端工具事件 |
| 最终回答 | model stream | PASS/RETRY/BLOCK | PASS 原样释放；RETRY 回原模型循环 |

## 6. 持久化边界

新增持久化只保存跨 Turn 必需的证据投影：

- conversation_id
- source message / turn revision
- artifact_id 和 fingerprint
- source、columns、query_scope、metric_definitions
- 小结果集行数据或受控 file reference
- validation status
- created_at

约束：

- 不把整个 RunContract 和 ArtifactLedger 作为消息 JSON 塞回历史；
- 大结果集只保存受控引用和摘要统计；
- 同一 conversation + fingerprint 幂等；
- 查询必须受 `base_revision` 限制；
- 新 migration 提供 rollback；
- 不修改既有 migration 文件。

## 7. 失败、并发与降级

| 场景 | 行为 |
|---|---|
| `AgentResult.data` 为空 | 登记 empty，不伪造统计 |
| 仅有 Markdown 表格 | 可展示，不升级为可信 DATA_RESULT |
| 工具成功但证据校验失败 | 原 observation 保留，完成门不放行精确结论 |
| 最终草稿数字无证据 | 丢弃草稿并返回结构化纠错 Observation |
| 连续两次纠正仍失败 | 阻断可疑数字并返回固定安全说明 |
| Actor lease 丢失 | 消息和证据都不提交 |
| 重试同一 Turn | fingerprint 幂等，不重复写证据 |
| 并发新消息 | 依据 base_revision 读取固定快照 |
| 数据引用过期或文件缺失 | 返回可解释 fallback，必要时重新查询 |
| WebSocket 取消 | 沿用现有取消语义，不提交未完成证据 |
| 普通聊天 | 空合同、空 data context，行为不变 |

## 8. 分阶段实施

### Phase 1：Run 内观察模式

- 新增 RuntimeState、RunContract、ArtifactLedger 和策略接口。
- 在 `apply_tool_results` 后登记证据。
- 不阻断、不改变任何现有完成行为，只记录审计结果。

### Phase 2：统一完成门

- Actor、Web Stream、专业 Agent 调用同一纯判断内核。
- 首期只对明确合同启用，空合同保持原行为。

### Phase 3：跨 Turn 数据证据

- 新增 `DATA_RESULT` 映射、持久化投影和 Actor 原子提交。
- 扩展 ContextSnapshot。
- 模型继续消费紧凑证据目录和原工具 Observation。

### Phase 4：Universal Evidence Guard

- 在模型最终草稿与 emit/persist 之间校验通用数值 Claim。
- 校验失败返回原模型循环，由模型自行决定调用沙盒、ERP或修正回答。
- 删除关键词 Data Validator 和正常答案直接渲染路径。
- 完成 Web、企微和非 WebSocket 渠道兼容回归。

## 9. 计划修改的代码路径

新增：

- `backend/services/agent/runtime/runtime_contract.py`
- `backend/services/agent/runtime/artifact_ledger.py`
- `backend/services/agent/runtime/runtime_state.py`
- `backend/services/agent/runtime/artifact_collector.py`
- `backend/services/agent/runtime/policies/data_accuracy.py`
- `backend/services/agent/runtime/evidence_guard/`
- 新版本数据库 migration 与 rollback

修改：

- `backend/services/handlers/chat/tool_loop.py::apply_tool_results`
- `backend/services/handlers/chat/execution_engine.py::execute_chat`
- `backend/services/handlers/chat/executor.py::ChatGenerationExecutor.execute`
- `backend/services/conversation_execution.py::GenerationOutcome`
- Actor generation commit 调用点
- `backend/services/handlers/context_snapshot.py::ContextSnapshot`
- ContextSnapshot loader
- PromptBuilder 的 snapshot 消费路径
- tool registry 的数据工具装配路径

首期不修改：

- `AgentResult` 公共字段及现有序列化语义；
- 现有 ContentPart / emit payload 协议；
- ERP 工具业务查询接口；
- 前端消息渲染协议；
- 历史 migration；
- 普通聊天的默认停止行为。

## 10. 验收标准

核心用例：

1. 首轮查询“昨天付款订单按平台划分”，保存结构化平台数据及有效订单口径。
2. 下一轮追问仍由模型理解并选择沙盒或ERP，不由Guard接管意图。
3. 模型草稿中的错误合计不会展示，而是收到结构化纠错Observation。
4. 沙盒生成正确派生Evidence后，模型修正答案通过Guard并原样展示。
5. 新数据查询产生新 revision，旧 Turn 仍只能看见其 base revision 内证据。
6. 普通聊天、图表、文件、企微和 Web 原有投递内容不变。
7. Actor 重试不重复扣费、不重复提交消息、不重复写 evidence。
8. 工具失败、空数据、口径缺失时不产生伪造数字。

回归必须覆盖：

- 原 `AgentResult` observation；
- emit payload 和 ContentPart；
- Web 流式与取消；
- Actor claim/lease/fencing；
- usage、credits、tool digest；
- ContextSnapshot revision；
- ERPAgent 与 ScheduledTaskAgent 原停止策略；
- 历史消息读取。
