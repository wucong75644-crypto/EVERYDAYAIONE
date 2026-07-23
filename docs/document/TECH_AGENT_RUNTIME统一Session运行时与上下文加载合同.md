# Agent Runtime 统一 Session 运行时与上下文加载合同

> 状态：总体设计候选，待用户确认
> 日期：2026-07-19
> Grok Build 核验基线：`7cfcb20d2b50b0d18801a6c0af2e401c0e060894`
> 输入：17 个板块第一轮对标、最新源码增量审计、EVERYDAYAIONE 当前 Actor/Context/Memory/Artifact 实现
> 范围：Session 工作方式、Model Loop、Context Epoch、首轮/多轮/工具循环/压缩/恢复加载合同

## 1. 设计结论

目标不是单独优化 PromptBuilder，而是建立统一 Session Runtime：

```text
Ingress
  → Conversation Actor（持久串行、lease、fencing）
  → Session Runtime（本次 Run 的唯一状态推进器）
  → Context Runtime（决定模型看到什么）
  → Model Runtime（执行一次模型采样）
  → Action Runtime（工具、权限、副作用、结果）
  → Persistence（原子提交事实、产物和回执）
  → Projection（Web / 企微 / 后续客户端）
```

核心不变量：

1. PostgreSQL 是持久事实源，Redis 和进程内状态只能加速。
2. 一个 Conversation 同一时刻只有一个主 Run 拥有状态推进权。
3. 一个用户 Turn 内可有多个 ModelStep，但共享一个权威工作集。
4. 同一 Context Epoch 内的模型输入只允许追加，禁止重写已发送前缀。
5. ToolCall、ToolResult 和 ArtifactRef 必须组成可恢复的原子事实组。
6. 确定性 Pruning 与 LLM Compaction 分工，不能生成竞争摘要。
7. Compaction 是正常流程中创建新 Epoch、重写活动前缀的唯一入口。
8. Memory 在 Epoch 内固定；新会话或 Compaction 后重新自动召回。
9. 每次真实 Provider 调用都生成 ContextReceipt 和 ModelStepReceipt。
10. Web、企微只做输入输出投影，不拥有独立 Context 或 Model Loop 规则。

## 2. Grok Build 源码事实

最新主链：

```text
SessionActor
  → drain interjection / skill / monitor events
  → first-turn memory injection
  → MCP reminder
  → pre-sampling compaction check
  → resolve effective tools
  → build ConversationRequest
  → sampler
  → parse response / tool calls
  → execute tools
  → append ToolResult
  → next ModelStep
  → completion / recovery / persistence
```

| 能力 | Grok Build 源码 |
|---|---|
| Session 初始化与固定前缀 | `xai-grok-shell/src/session/acp_session_impl/session_setup.rs` |
| 环境、规则、Skill、MCP 前缀 | `acp_session_impl/prompt_build.rs`、`session/user_message.rs` |
| Model Loop | `acp_session_impl/turn.rs` |
| Request 组装 | `xai-chat-state/src/actor/request_builder.rs` |
| 50% ToolResult Pruning | `request_builder.rs::should_prune/prune_conversation` |
| 85% 自动 Compaction | `session/compaction.rs` |
| 压缩后历史重建 | `xai-chat-state/src/compaction_utils.rs::build_compacted_history` |
| 首轮 Memory | `turn.rs::first_turn_memory_reminder` |
| 压缩后 Memory | `session/helpers/compaction_context.rs` |
| Cache Identity | `ConversationRequest.x_grok_conv_id` |
| Cache Usage | `turn.rs` 的 `cached_prompt_tokens` telemetry |

Grok Build 不是“轻 Prompt + 单一压缩器”：

- 基础 System Prompt 约 4.6KB，另外加载环境、项目规则、Skill、MCP、Memory 和 Tool Schema。
- 上下文超过 50% 时确定性裁剪旧 ToolResult。
- 默认达到窗口 85% 时执行 LLM Compaction。
- 图片接近 50MB 时使用独立高低水位淘汰。
- 首轮 Memory 注入后留在当前前缀，恢复时禁止重新搜索，以保护 KV Cache。

不直接复制其本地 Session、JSONL、Markdown Memory、固定阈值和本地权限模型。保留本项目
PostgreSQL Actor、revision、多租户、Curated Memory、Artifact、媒体异步任务和积分事务。

## 3. 当前项目上下文

### 3.1 架构现状

Web 与企微主聊天已共用 Conversation Actor、固定 ContextSnapshot 和 `execute_chat`。
Context 已有 typed ConversationItem、统一历史加载、模型能力预算、结构化 Compaction、
Artifact、Curated Memory 和 ContextReceipt。主要问题是状态推进仍分散在 Handler、
PromptBuilder、Tool Loop、ContextVar、多个 Compressor 和后置持久化路径中。

### 3.2 可复用模块

| 模块 | 可复用能力 |
|---|---|
| Conversation Actor | serial queue、lease、fencing、attempt、原子终态 |
| `execute_chat` | 通道无关 Model/Tool Loop |
| ContextSnapshot | 固定 base revision |
| ConversationItem | user/assistant/tool/artifact/interrupt 类型化事实 |
| ContextBudget | 按模型窗口推导 75%/85%/92% 阈值 |
| ContextAssembler | 稳定旧前缀结构化 Compaction |
| Unified History Loader | Compaction 后增量恢复 |
| Artifact Runtime | 完整事实、model view、ref、Search/Get/Read |
| Curated Memory | 首轮最多 3 条、Search/Get、证据与生命周期 |
| ContextReceipt | 消息、工具、Token、hash 的无正文回执 |
| Provider Adapter | prompt/completion/cached/cache-creation Token |

### 3.3 约束与冲突

- 不改变 Web/企微 API 和 ContentPart 用户协议。
- 不允许新旧 Runtime 同时产生副作用或重复扣费。
- 迁移 138–144 未应用前不得部署依赖对应表的版本。
- PromptBuilder 每轮重渲染稳定块，时间未成为持久 TurnItem。
- `compress_messages_if_needed` 与 `assemble_history` 可能生成竞争摘要。
- Tool Loop 压缩必须限定为当前 Run，不能覆盖跨 Turn 事实。
- Memory 尚未显式绑定 Epoch；`user_preferences` 仍固定传入 `None`。
- `PromptBuilder` 465 行，新增职责不得继续堆入该文件。

## 4. 统一事实模型

```text
SessionIdentity
  conversation_id / user_id / org_id / channel
  current_revision / active_run_id

Run
  run_id / input_message_id / base_revision
  execution_mode / context_epoch_id / status / stop_reason

ModelStep
  step_index / input_context_receipt / effective_toolset_hash
  provider_request_id / stop_reason / usage / tool_calls

ContextEpoch
  epoch_id / base_compaction_id
  agent_definition_hash / permission_snapshot_hash
  user_preferences_hash / memory_snapshot_hash
  project_instructions_hash / stable_prefix_hash

ContextPlan
  epoch_header / continuation / working_set / current_turn
  effective_tools / budget / pruning_receipt
  compaction_receipt / cache_identity
```

首期不新增 Epoch 表。`epoch_id` 由 conversation、latest compaction、Agent、权限、偏好、
Memory 和模型族的稳定 hash 派生；先进入 shadow receipt，后续确有审计需要再 additive 落表。

## 5. Session 状态机

```text
IDLE
  → CLAIMED
  → HYDRATING
  → READY
  → SAMPLING
  → EXECUTING_ACTIONS
  → SAMPLING
  → COMMITTING
  → COMPLETED
```

异常分支：

```text
SAMPLING → COMPACTING → SAMPLING
SAMPLING → AUTH_RECOVERY → SAMPLING
EXECUTING_ACTIONS → WAITING_EXTERNAL
活动状态 → CANCELLING → CANCELLED
活动状态 → OWNERSHIP_LOST / FAILED
```

每次循环固定执行：

```text
Drain 插话/后台/Skill/Tool 事件
→ Guard ownership/取消/预算/交互
→ Capacity Pruning/Compaction/suppression
→ Capability 读取 EffectiveToolset
→ Context 构建 ContextPlan/Receipt
→ ModelStep Provider 调用
→ Advance 完成/Action/压缩/恢复/等待
```

## 6. 上下文内容合同

### 6.1 Epoch 固定头部

```text
AgentCore
  身份、通用安全和行动边界、输出协议

SessionStable
  当前模式、Custom Instructions、Memory Snapshot

ProjectInstructions
  组织规则、项目规则、有效 Agent/Skill 指令
```

禁止放入固定头部：每轮时间、当前用户消息、临时附件正文、ToolResult、UI progress、
同一 Turn 内可能变化的权限结果。

### 6.2 Continuation

有 Compaction 时加载：

```text
goals / constraints / decisions / facts
artifact_refs / failures / unfinished
from_sequence / through_sequence
```

### 6.3 Working Set

```text
未完成 ToolCall/ToolResult
→ 用户明确引用的 Artifact/File/Evidence
→ 最近完整 Turn
→ 当前任务 Artifact model view
→ 仍有效的失败与未完成状态
```

### 6.4 Current Turn

```text
TurnContext
  current_time / location / channel facts / attachment refs

UserItem
  用户原话 / 图片和文件引用
```

TurnContext 必须成为 ConversationItem 并随 Turn 持久化，不能下轮消失。

### 6.5 Tools

Tools Schema 走 Provider `tools` 字段。一个 Run 内默认冻结 EffectiveToolset；权限模式、
动态 Tool/MCP 发现、Agent/Skill、Structured Output、Plan mode 变化可产生新版本。

## 7. 不同阶段的精确加载

### 7.1 新会话首轮

```text
[AgentCore]
[SessionStable: mode + preferences + first-query memories]
[ProjectInstructions]
[TurnContext: time + location + attachments]
[User]
[Tools]
```

Compaction、历史 Turn、历史 Artifact 和历史 ToolResult 均为空。

### 7.2 同一用户 Turn 的第二次模型调用

不重新读取 DB、召回 Memory或生成固定头部：

```text
第一次 ModelStep 的全部 ContextPlan
+ Assistant ToolCall
+ ToolResult model view
+ ArtifactRef
```

第三次及后续 ModelStep 继续只追加。

### 7.3 下一用户 Turn

```text
[同一 Epoch 固定头部]
[已有 Continuation]
[上一 Turn 完整 User/Assistant/Tool 事实]
[新的 TurnContext]
[新的 UserItem]
[Tools]
```

老 ToolResult 达到 Pruning 年龄后，模型视图变为 ArtifactRef，完整事实不删除。

### 7.4 Compaction 后

创建新 Epoch：

```text
[重新渲染 AgentCore]
[最新 mode/preferences]
[按最后用户问题重新召回 Memory]
[ProjectInstructions]
[CompactionSummary]
[最近至少两个完整 Turn]
[未完成 Action/Artifact/Todo]
[Current Turn]
[Tools]
```

### 7.5 Worker 冷恢复

```text
ExecutionScope
+ base_revision
+ latest ready compaction <= base_revision
+ items after through_sequence
+ active artifacts/evidence
+ pending action/interaction state
→ ContextPlan
```

热缓存和冷恢复必须得到相同 epoch、prefix hash、item 顺序、toolset hash 和 Receipt。

## 8. 容量与 Memory

### 8.1 Deterministic Pruning

建议从 `usable_input × 50%` 启动：

- 最近 3 个用户 Turn 的 ToolResult 完整保留。
- 更早大结果使用 bounded model view。
- 很老结果只保留 ArtifactRef 和 unavailable 语义。
- Tool pair 不拆散，不调用 LLM，不生成对话摘要。

### 8.2 LLM Compaction

继续使用 75% soft、85% hard、92% emergency：

1. 获取 conversation/revision 协调锁。
2. 选择稳定完整旧 Turn。
3. 生成和校验结构化摘要。
4. 以 source hash、revision CAS 原子提交。
5. 创建新 Epoch。

最终超过 hard limit 时，不删除当前输入、不拆工具组、不发送残缺 ContextPlan。

### 8.3 Compaction suppression

| 状态 | 错误 | 清除条件 |
|---|---|---|
| NONE | 正常 | — |
| TURN | 本轮瞬时失败 | 下一用户 Turn |
| STICKY | 内容/schema/尺寸稳定失败 | 模型、窗口、历史或配置变化 |
| UNTIL_SUCCESS | 认证、余额、Provider | 普通模型调用成功 |

同会话只允许一个 in-flight Compaction；结果必须匹配 revision、prefix hash 和 model。

### 8.4 Memory

```text
Turn committed → Session Flush → Evidence Validation
→ Session Memory Log → Consolidation → Curated Memory
```

自动召回只发生在新会话首轮或 Compaction 新 Epoch。Epoch 内用户纠正时，当前原话优先，
提交后异步更新 Memory，下一 Epoch 使用新值；不原地改写固定前缀。

## 9. Provider Cache 合同

```text
ContextCacheIdentity
  conversation_id / epoch_id / stable_prefix_hash
  model_id / agent_definition_hash / effective_toolset_hash
```

Adapter 声明：

```text
supports_explicit_cache_key
supports_cached_token_usage
cache_key_transport
prefix_cache_semantics
```

每次请求记录 input、cached、cache creation Token，cache hit ratio，Epoch/prefix/toolset hash，
以及 Pruning/Compaction 前后 Token。Provider 不支持时记录 unsupported，不能伪造命中率。

## 10. 模块边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| Conversation Actor | Claim、lease、fencing、终态 | Prompt 内容 |
| Session Runtime | 状态推进、continuation 所有权 | 业务工具实现 |
| Context Runtime | 信息选择、预算、Epoch、Receipt | 权限和副作用 |
| Model Runtime | Provider、流、usage、stop reason | 执行工具 |
| Action Runtime | ToolCall、Policy、Attempt、Result | 模型表达 |
| Memory Runtime | Flush、Consolidation、Search/Get | 当前任务事实 |
| Artifact Runtime | 大对象、view/ref、鉴权 | 长期偏好 |
| Projection | Web/企微展示与恢复 | Runtime 推进 |

禁止 Context Runtime 反向调用 Session Runtime。

## 11. 边界场景

| 场景 | 处理策略 |
|---|---|
| 新会话无历史 | 只构建 Epoch header + Current Turn |
| Memory 失败 | 空 Snapshot，聊天继续 |
| 用户纠正 Memory | 当前原话优先，下一 Epoch 更新 |
| ToolResult 超大 | 完整存 Artifact，模型只取 view/ref |
| ToolCall 无 Result | 恢复时生成 interrupted/unknown result |
| 模型窗口变小 | 清相关 suppression，立即重预算 |
| Compaction 过期 | revision/hash 不匹配即废弃 |
| 两 Worker 压缩 | Redis single-flight + DB CAS |
| Redis 故障 | 依赖 DB CAS，不阻断聊天 |
| Provider 无 cache key | 正常调用并记录 prefix hash |
| 用户连续输入 | Actor mailbox 串行；显式 interjection 才进入活动 Run |
| Worker 退出 | lease 过期后从 DB 恢复 |
| 权限改变 | Action 重新 Policy；必要时新 Epoch/toolset version |
| 取消 | 外部 Accepted/Unknown Action 进入 reconcile |
| Artifact 失权 | 返回 unavailable/forbidden，不回灌旧正文 |

## 12. 连锁修改与架构影响

| 改动点 | 影响范围 | 同步要求 |
|---|---|---|
| Session Runtime facade | Actor executor、`execute_chat`、RuntimeState | 首期兼容调用 |
| ContextPlan 协议 | PromptBuilder、Snapshot、Assembler、Provider setup | 保留 messages 投影 |
| TurnContext 持久化 | item builder、Actor commit、history loader | 时间/位置不再临时消失 |
| Epoch identity | PromptBuilder、Memory cache、Compaction、Receipt | 统一失效原因 |
| Pruning 收口 | compressor、tool loop、Artifact | 只做确定性 projection |
| Compaction 单入口 | assembler、旧 summary/compressor | 先 shadow 对账 |
| Cache capability | adapter base/types、各 Provider | 不支持时降级 |
| Receipt 扩展 | receipt、execution result、Actor commit | 不保存正文 |
| Toolset version | tool selection、permission、MCP/Skill | 同 Run 默认冻结 |
| 冷恢复一致性 | history loader、Snapshot | 与热路径 hash 对账 |

跨模块耦合风险为高，必须分波次，禁止一次性切换。

## 13. 方案与实施波次

推荐 A+：在现有 Conversation Actor 上收口 Session Runtime；不重写 Actor、数据库、
Memory、Artifact 和专业 Executor。

| 波次 | 内容 | 生产行为 |
|---:|---|---|
| 0 | 冻结 ContextPlan/Epoch/ModelStep/CacheIdentity（子任务 1.1–1.3 已完成） | shadow receipt 与 ProviderUsage 已原子持久 |
| 1 | Provider 请求改由 ContextPlan 单一投影（子任务 2.1–2.2 已硬切换） | 无旧路径回退 |
| 2 | Pruning/Compaction 收口和 suppression（3.1 确定性 Pruning、3.2 当前 Run LLM Compaction、3.3a 回填门禁、3.3b 跨 Turn 读取硬切已完成） | 50%/85% 主链与 ConversationItem/Compaction 冷恢复已硬切；生产迁移/回填门禁和 emergency gate 待执行 |
| 3 | Session Runtime 驱动 Model Loop | 保留旧兼容入口 |
| 4 | Action Runtime：只读→文件/ERP→媒体/外部写 | 坚持单执行者 |
| 5 | Skill/Goal/Subagent/MCP | 全部接同一 Session 合同 |
| 6 | 旧 Prompt/Compressor/Loop 退出 | 观察窗口后删除 |

## 14. 测试、灰度与回滚

核心不变量：

1. 同 Epoch `request[N].input` 是下一请求的稳定前缀。
2. Compaction 是正常流程唯一改变旧前缀的事件。
3. Tool pair 不拆组。
4. 热恢复和冷恢复 ContextPlan hash 一致。
5. 同 Run 只有一个外部副作用执行者。
6. Receipt 不含正文。
7. Memory Snapshot 在 Epoch 内不漂移。
8. 当前用户纠正优先于旧 Memory。

灰度指标：

- ContextPlan shadow mismatch。
- stable prefix divergence。
- cached token ratio。
- compaction success/reject/stale。
- tool pair repair。
- cold/warm hash mismatch。
- duplicate external action，必须为 0。
- terminal delivery mismatch。

每个 Wave 使用独立 feature flag。Shadow 不改变 Provider payload；Context 阶段可按组织回
旧 builder；Action 只切 dispatcher，不重放已 Accepted 动作；新增数据库结构只停止写入，
不删除事实。迁移 138–144 未验证前不得进入依赖它们的生产 Wave。

## 15. 待确认

1. 采用 A+，在现有 Conversation Actor 上收口 Session Runtime。
2. Context Epoch 首期逻辑派生，不新增表。
3. 先完成 Context/运行回执，再迁移 Action、Goal、Skill、Subagent、MCP。
4. 任何板块不得建立第二套 Session 或 Model Loop。
