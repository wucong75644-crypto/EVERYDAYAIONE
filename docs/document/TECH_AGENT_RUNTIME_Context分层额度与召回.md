# Agent Runtime Context 分层、额度与召回设计

> 状态：总体设计 / 第一轮冻结
> 日期：2026-07-18
> 范围：一次 ModelStep 看见什么、为什么看见、占多少额度、如何压缩和恢复
> 对标基线：Grok Build `c68e39f60462f28d9be5e683d9cbe2c57b1a5027`

## 1. 结论

EverydayAI 不应继续把 Context 等同于不断增长的 `messages[]`。目标链路是：

```text
PostgreSQL / Artifact Store / Workspace / Memory / Event Log
  -> immutable ContextSnapshot
  -> ContextPlan
  -> Retrieval + ContextBlocks
  -> Budget Allocator
  -> ContextAssembler
  -> ModelStep
  -> ContextReceipt
```

核心原则冻结为：

1. 完整事实永久留在事实层，模型上下文只是当前步骤的有预算投影。
2. 当前用户输入、Policy、Goal 契约和未完成 Action 属于控制面，不能被普通压缩淘汰。
3. 大 ToolOutput、文件、图片、视频、表格只进入 Artifact/Workspace；模型默认拿摘要和引用。
4. 摘要是带覆盖范围的派生物，不是权威消息，也不能覆盖原文。
5. Memory、Knowledge、Artifact、旧 ToolOutput 统一采用 `Search -> Get` 的渐进式披露。
6. 每次 ModelStep 都生成 ContextReceipt，能够解释装入、裁剪、召回和 token 消耗。
7. Skill/MCP/Subagent 采用隔离且受限的 ContextPlan，不能继承整个父会话。

采用 Grok 的分层、压缩抑制和按需恢复思想，但保留本项目更强的数据库 revision Snapshot、ResourceManifest、多租户 Scope 和 Artifact 能力。

## 2. 项目上下文

### 2.1 架构现状

当前 `ContextSnapshot` 已按 `task/input/turn/base_revision/through_message_id` 固定闭合历史，Redis revision 不匹配时回源数据库；这是正确的事实边界。PromptBuilder、history loader、handler compressor、Memory V2、Session Memory、会话摘要和 DataContextSnapshot 分别负责组装或压缩。Web 与企微在工具循环中使用不同预算和保留参数，最终仍原地修改消息数组。现有系统具备大部分原料，但缺少统一 ContextPlan、信息路由规则、可恢复引用和组装回执。

### 2.2 可复用模块

| 模块 | 复用方式 |
|---|---|
| `context_snapshot.py` | 保留为 Snapshot 事实入口 |
| `conversation_cache` | 保留闭合历史缓存，不作为事实源 |
| `history_loader.py` | 迁移为 HistoryBlockProvider |
| `context_compressor/` | 算法复用，收口为 Compactor |
| `resource_manifest.py` | 作为资源可见性和读取范围 |
| `data_context_snapshot.py` | 作为跨 Turn 数据证据 Provider |
| `ArtifactLedger` | 升级为 Artifact/Evidence 引用入口 |
| `services/memory/` | 长期 Memory Provider |
| `PromptBuilder` | 逐步收口为 ContextAssembler 的指令 Provider |

### 2.3 设计约束

- 相同 `snapshot_revision + model + plan_revision` 重试必须得到相同 block refs/hash。
- 任何裁剪不能拆开 ToolCall/ToolOutput、用户 Turn 或结构化 ContentPart。
- 群聊禁止注入个人 Memory/persona，沿用 channel Workspace。
- Context 不得成为权限旁路；Artifact/Get 仍执行 Policy 和 Resource Scope。
- UI 流事件、动画进度、租约日志和内部思维草稿不进入模型。
- 兼容当前 OpenAI-style message 与 tool-call Provider 协议。

### 2.4 潜在冲突

- `services/memory/context_compressor.py` 与 handler compressor 并存。
- Web 200K、普通/企微 32K 是通道常量，不是模型能力推导。
- 工具归档为字符串元数据，缺少稳定 `artifact_ref/tool_output_ref`。
- Session Memory、循环摘要、conversation summary、Memory L1/L2/L3 职责重叠。
- 当前摘要通过 system 文本前缀区分，缺少 revision 覆盖和注入防护。

## 3. 信息存储与进入上下文的路由

### 3.1 五层模型

| 层 | 名称 | 内容 | 默认进入模型 |
|---|---|---|---|
| L0 | Fact Store | Message、Action、Attempt、完整 ToolOutput、事件、账本 | 否 |
| L1 | Control Plane | Agent、Policy、Goal、当前输入、未解决 Interaction | 是 |
| L2 | Working Set | 近期闭合 Turn、当前步骤、未完成 Action、相关错误 | 是 |
| L3 | Continuation | ContextSummary、决策、已完成项、未满足 gap | 按需 |
| L4 | Retrieval | Memory、Knowledge、Artifact、Workspace、旧结果索引 | Search 后进入 |

### 3.2 信息路由矩阵

| 信息 | 权威位置 | 上下文形式 | 退出条件 |
|---|---|---|---|
| 当前用户命令 | Message | 完整 CurrentInputBlock | ModelStep 完成仍保留到 Run 终态 |
| Agent/System 规则 | Agent revision | 精简 InstructionBlock | Agent revision 变化 |
| Policy/授权 | receipt/grant | 约束摘要，不放凭证 | 过期、撤销、Action 完成 |
| Goal/验收 | Goal tables | Objective、gaps、budget | Goal 终态 |
| 最近对话 | Messages | 完整 TurnBlock | 超预算后进入摘要覆盖 |
| ToolCall/Output | Action/Result | 当前 pair 或 OutputRefBlock | Action 闭合且不再相关 |
| 大查询结果 | Artifact | schema、统计、snippet、ref | 按 relevance 淘汰 |
| 文件 | Workspace/Artifact | metadata、selected chunks、ref | 当前步骤不再引用 |
| 图片/视频 | Artifact/OSS | metadata、OCR/vision 派生、ref | 当前步骤不再引用 |
| 图表/Mermaid | Artifact | spec 摘要、数据 lineage、ref | 当前步骤不再引用 |
| Skill Catalog | Skill Registry | 匹配后的短目录 | 选择完成或意图变化 |
| Skill 正文 | Skill revision | 当前 Skill 指令块 | SkillRun 完成/暂停 |
| MCP Catalog | MCP Registry | 筛选后的 schema | 当前 Run 工具集冻结 |
| Memory | Memory Store | 最多若干 snippet/ref | 当前 ModelStep 后可退出 |
| 知识库 | Knowledge Store | citation snippet/ref | 当前证据不再需要 |
| UI progress | RuntimeEvent | 不进入 | 始终 |
| 完整审计/日志 | Audit/Event | 不进入 | 始终 |
| 模型隐藏思维 | 不持久化 | 不进入 | 始终 |

## 4. ContextBlock 协议

内部统一块，不直接绑定 Provider 格式：

```json
{
  "block_id": "uuid",
  "kind": "current_input",
  "source_type": "message",
  "source_ref": "message:uuid",
  "source_revision": 42,
  "priority": "required",
  "token_estimate": 320,
  "content_hash": "sha256:...",
  "sensitivity": "org_internal",
  "scope": {"org_id": "uuid", "channel": "web"},
  "expires_after": "run",
  "payload_ref": null,
  "dependencies": []
}
```

`kind` 首期固定：

- `agent_instruction`
- `policy_constraint`
- `goal_contract`
- `interaction_state`
- `context_summary`
- `memory_snippet`
- `knowledge_snippet`
- `history_turn`
- `current_input`
- `resource_manifest`
- `action_pair`
- `tool_output_ref`
- `artifact_ref`
- `skill_instruction`
- `continuation_directive`

Priority：

- `required`：不得裁剪；装不下则拒绝当前模型或切换更大窗口。
- `protected`：压缩前保留，只有显式降级策略可替换为引用。
- `ranked`：按相关度、时效和成本分配。
- `optional`：有剩余额度才进入。

块必须携带来源和 hash。摘要、Memory、Knowledge 等不可信文本在转成 Provider system/developer 内容前必须使用数据边界包装，不能让其中的伪指令提升优先级。

## 5. ContextPlan

Session Actor 在每个 ModelStep 前冻结：

```json
{
  "plan_id": "uuid",
  "run_id": "uuid",
  "model_step_id": "uuid",
  "snapshot_revision": 42,
  "model_capability_revision": 5,
  "input_limit": 114688,
  "output_reserve": 16384,
  "safety_margin": 8192,
  "fixed_prompt_budget": 12000,
  "tool_schema_budget": 10000,
  "dynamic_content_budget": 68000,
  "required_block_refs": [],
  "retrieval_requests": [],
  "protected_refs": [],
  "compaction_policy": "standard",
  "plan_revision": 1
}
```

Plan 输入：

- Snapshot、当前 Run/Goal/Skill/SubRun 状态；
- 模型上下文窗口和最大输出；
- EffectiveToolset 的 schema token；
- Policy Scope 和渠道限制；
- 当前输入意图及引用的 Artifact；
- 前一 ModelStep receipt 和新增 ActionResult。

同一 ModelStep 的传输重试复用同一个 Plan 和 blocks；只有上下文错误恢复、用户插话、ActionResult 到达、模型切换或权限变化才创建下一 revision。

## 6. Token 预算计算

### 6.1 基本公式

```text
provider_input_limit
= model_context_window
 - reserved_output
 - provider_safety_margin

dynamic_content_budget
= provider_input_limit
 - fixed_instruction_tokens
 - effective_tool_schema_tokens
 - required_control_tokens
```

`reserved_output`：

```text
min(
  model_max_output,
  max(run_requested_output, model_context_window × 12.5%)
)
```

`provider_safety_margin` 初值：

```text
max(2048, model_context_window × 5%)
```

如果 required blocks 已超过 `provider_input_limit`，不得静默截断。按顺序：

1. 缩减 EffectiveToolset；
2. 将可引用 Resource/ToolOutput 改为 ref；
3. 选择更大上下文模型；
4. 返回 `CONTEXT_REQUIRED_BLOCKS_OVERFLOW`。

### 6.2 动态预算桶

dynamic content 首期权重：

| 桶 | 目标占比 | 最低保障 | 上限 |
|---|---:|---:|---:|
| Recent history | 35% | 2 个完整 Turn | 50% |
| Current actions/results | 25% | 全部未完成 pair | 40% |
| Retrieved evidence | 20% | 当前明确引用项 | 35% |
| Summary/continuation | 10% | 当前有效 summary | 15% |
| Memory/knowledge | 5% | 0 | 10% |
| Skill/MCP reminders | 5% | 活跃 Skill 必需指令 | 10% |

桶不是硬切割：未使用额度按 `required -> protected -> ranked` 重新分配。任何单桶不得通过填满无关内容挤掉 required block。

### 6.3 触发参数

| 参数 | 初值 |
|---|---:|
| `soft_compaction_ratio` | 0.80 |
| `hard_compaction_ratio` | 0.90 |
| `emergency_trim_ratio` | 0.95 |
| `recent_turn_min` | 2 |
| `recent_turn_target` | 6 |
| `memory_auto_limit` | 3 |
| `memory_snippet_chars` | 500 |
| `knowledge_auto_limit` | 5 |
| `retrieval_search_limit` | 10 |
| `retrieval_get_limit_per_step` | 5 |
| `compaction_timeout_seconds` | 60 |
| `compaction_total_budget_seconds` | 300 |
| `context_estimation_error_ratio` | 0.05 |

Grok 的 85% 作为参考，不直接照抄；本项目首期 80% 是为了给工具 schema、异步结果和 Provider 包装留出空间。

## 7. ContextAssembler

Provider 前的稳定组装顺序：

```text
1 Agent instruction
2 Policy constraints / effective scope
3 Goal contract / current step / continuation
4 Active Skill instruction
5 ContextSummary
6 Memory and Knowledge reminders
7 Recent closed Turn blocks
8 Current Action pairs and retrieved evidence
9 Current input + ResourceManifest
10 EffectiveTool schemas
```

实现规则：

- Provider role 映射在 Adapter 完成，内部块不伪装 role。
- ToolCall 与 ToolOutput 必须成对；并行 Action 可按 call order 固定。
- 当前用户输入永远是最后一个用户语义块，不允许摘要出现在其后伪装新指令。
- 相同 blocks 以 stable sort key 组装，避免 Prompt cache 前缀漂移。
- Tool schema 先经过 EffectiveToolset 筛选，再计入预算。
- Assembler 不查询数据库或调用模型，只消费已冻结 providers 的结果。

## 8. Search -> Get 渐进式召回

所有大信息源统一两个阶段：

```text
search(query, filters, limit)
  -> [{ref, title, snippet, score, source, revision, size}]

get(ref, selector, max_tokens)
  -> typed ContextBlock or Artifact view
```

覆盖：

- `memory_search/get`
- `knowledge_search/get`
- `artifact_search/get`
- `tool_output_search/get`
- `workspace_search/read`
- `conversation_search/get_turns`
- `mcp_resource_search/read`

规则：

1. Search 结果本身不自动成为事实，仅用于选择。
2. Get 必须重新校验 actor/org/resource scope。
3. 同一 ModelStep 固定 search result refs/hash，重试不重新检索。
4. Get 的正文仍受单次预算和敏感数据出口策略限制。
5. 引用不可用时返回 `unavailable/deleted/forbidden/revision_changed`，不能让摘要冒充原文。
6. Retrieval 结果必须保留 citation/ref，最终产物可追溯。

## 9. ToolOutput 与 Artifact

Tool Executor 返回完整 `ActionResult`，Context Provider 再生成不同视图：

```text
ActionResult
  ├─ model_view: small structured summary
  ├─ artifact_refs: complete data/files/media
  ├─ display_view: channel projection
  └─ audit_view: redacted metadata
```

大小策略：

| 序列化大小 | model_view |
|---|---|
| `<= 8 KB` | 完整结构化结果 |
| `8–64 KB` | schema + summary + selected rows + ref |
| `> 64 KB` | metadata + deterministic stats + ref |
| 二进制/媒体 | metadata + derived text + ref |

阈值按序列化字节先分类，最终仍按 tokenizer 计费。不能只把字符串改成 `[已归档]`；必须提供稳定 `tool_output_ref`，否则模型无法恢复。

数值类结果优先保存确定性统计、字段定义、过滤条件和 lineage，不让摘要模型重新计算关键数字。Data Validator 的证据只进入 Artifact/Evidence，不作为可被模型自由改写的普通 ToolOutput。

## 10. ContextSummary

```json
{
  "summary_id": "uuid",
  "conversation_id": "uuid",
  "from_revision": 1,
  "through_revision": 32,
  "objective": "...",
  "decisions": [],
  "completed_actions": [],
  "artifact_refs": [],
  "failed_attempts": [],
  "open_questions": [],
  "next_step": "...",
  "source_refs": [],
  "model_id": "...",
  "prompt_revision": 2,
  "content_hash": "sha256:..."
}
```

约束：

- 新 summary 只能覆盖连续、已闭合 revision。
- 当前用户纠正旧事实后，旧 summary 标记 superseded，不原地覆盖。
- 摘要必须包含 objective，以及 `open_questions` 或 `next_step`；缺失视为退化。
- 关键数字必须引用 Artifact/Evidence，不能只存在自然语言摘要。
- 清除 analysis 草稿、控制标签和伪 system 指令。
- Summary 生成失败不删除原 blocks。

事实冲突优先级：

```text
current user correction
> current authoritative ActionResult / Artifact
> original closed Message
> ContextSummary
> retrieved Memory / Knowledge
```

## 11. Compaction 状态机

压缩顺序：

```text
archive completed large ToolOutput to refs
-> replace old evidence with selected views
-> summarize continuous closed history
-> relevance trim ranked blocks
-> emergency tail-preserving trim
```

抑制状态采用 Grok 已验证的模型：

| 状态 | 含义 | 清除条件 |
|---|---|---|
| `none` | 可压缩 | — |
| `turn` | 本轮瞬时失败 | 下一 ModelStep |
| `sticky` | 内容/schema 稳定失败 | model/window/input revision 改变 |
| `until_success` | 认证/余额/基础设施失败 | 普通模型调用成功 |

并发参数：

- 同 `conversation_id + snapshot_revision + compaction_kind` 只允许一个 in-flight。
- 预压缩结果记录 `prefix_block_count + prefix_hash + model_revision`。
- 应用时前缀不匹配则丢弃结果，不修改当前 Plan。
- 同一 revision 只接受一个 active summary，旧结果可审计但不再装入。

## 12. Memory、Knowledge 与隐私

三种能力分开：

- `Compaction`：让当前任务继续。
- `Memory flush`：提取未来可能复用的个人/组织知识。
- `Retrieval`：针对当前问题取少量相关内容。

Memory flush 默认异步，不是每次压缩的同步前置条件。首期只在 Turn 成功闭合后进入现有 pipeline；压缩不能因为 Memory 服务不可用而失败。

Scope：

- 私聊：可按用户设置检索个人 Memory。
- 群聊：个人 Memory/persona 自动关闭；只允许显式组织 Knowledge 和 channel Workspace。
- Subagent：只能继承父 Run 已选择的 ref 或受限检索 capability。
- MCP：外部 Resource 默认不写长期 Memory；必须经 Data Egress/Retention Policy。
- 删除请求：Message、Memory、索引副本、Artifact 和 summary lineage 都必须可追踪清理。

## 13. Skill、MCP 与 Subagent 上下文

### Skill

- Catalog 常驻的只是筛选后 `name/description/when_to_use/revision`。
- 选中后按需加载正文；Instruction Skill 进入 `skill_instruction`。
- 二进制资源只进入 ResourceManifest/Artifact ref。
- Skill 正文超预算不静默截断步骤；采用章节索引和按需读取，或拒绝加载。
- 同一 Run 固定 Skill hash，热更新只影响新 Run。

### MCP

- 只注入 EffectiveToolset 中的 MCP schema，不注入整个 Server catalog。
- Resource 使用 Search/Read；Prompt 作为不可信外部模板，不能成为系统授权。
- MCP 返回内容默认 ToolOutput/Artifact，不直接成为 system instruction。

### Subagent

父 Agent 创建 `SubRunContextEnvelope`：

```text
objective + acceptance criteria
selected source refs
capability subset
token/time/cost budget
return schema
```

不复制完整父对话。子 Agent 返回结构化 `SubRunResult + artifact_refs + evidence_refs`；父 Agent 默认只接收摘要和引用，需要时再 Get。这样避免 N 个子 Agent 各复制 200K context。

ContextReceipt、边界场景、方案对比、影响范围和迁移验收见
`TECH_AGENT_RUNTIME_Context回执边界与迁移附录.md`。
