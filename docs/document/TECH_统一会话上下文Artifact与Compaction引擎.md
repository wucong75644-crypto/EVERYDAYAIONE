# 统一会话上下文、Artifact 与 Compaction 引擎

> 状态：已确认，进入实施
> 范围：Web、企业微信及后续所有聊天入口共用
> 发布策略：直接切换正式主链，不设置业务灰度
> 参考：Grok Build ConversationItem、持久化 ToolOutput、分层压缩；结合本项目 PostgreSQL、Actor、OSS 和多租户约束落地

## 1. 目标与不变量

本次升级将消息历史、工具结果、文件分析结果和压缩摘要统一为可持久化、可检索、可审计的上下文事实。模型不再依赖某个工具的特殊处理，也不再因跨 Turn 的固定字符截断丢失关键结果。

必须满足：

1. 所有入口共用同一套 `ContextSnapshot -> ContextPlan -> ContextAssembler`。
2. 所有工具结果进入同一 `ActionResult -> Artifact -> ConversationItem` 协议，不按工具名设白名单。
3. 完整结果永久存储；模型窗口内只放预算允许的视图和稳定引用。
4. 工具调用与工具结果组成原子组，压缩、裁剪时不得拆开。
5. 压缩生成新事实，不删除原始消息、上下文项和 Artifact。
6. 同一快照、模型能力与策略版本必须生成相同的计划哈希。
7. Web 与企业微信只负责输入、输出投影，不拥有上下文组装规则。
8. Actor fencing、任务终态、消息、上下文项、Artifact 元数据和 Evidence 在同一提交边界收敛。

## 2. 统一调用链

```text
Web / 企业微信
  -> ConversationActor
  -> ContextSnapshotLoader
  -> ContextPlanner
  -> ContextAssembler
  -> Model Adapter
  -> ToolExecutor
  -> ActionResultNormalizer
  -> ArtifactStore（完整事实）
  -> ToolResultProjector（本轮模型视图）
  -> Model Adapter（继续推理）
  -> CompactionCoordinator（达到阈值时）
  -> commit_generation_turn_vNext
     ├─ message / task / conversation revision
     ├─ conversation_context_items
     ├─ conversation_artifacts
     ├─ conversation_data_evidence
     └─ context_receipt
```

下一 Turn 只从持久事实重建快照。旧消息不会整段无条件塞入模型；Assembler 使用最新有效压缩项、压缩覆盖范围之后的原始项、当前用户输入、必须保留的工具原子组以及按需召回的 Artifact 视图。

## 3. 数据模型

### 3.1 conversation_context_items

模型可消费的有序事实流。

| 字段 | 说明 |
|---|---|
| `id` | UUID 主键 |
| `organization_id` / `conversation_id` | 租户与会话边界 |
| `task_id` / `turn_id` | 产生该项的执行与 Turn |
| `sequence` | 会话内严格递增序号 |
| `item_type` | system、user、assistant、reasoning、tool_call、tool_result、artifact_ref、compaction、interrupt |
| `group_id` | 工具调用/结果原子组 |
| `payload` | 有界结构化内容，不保存超大正文 |
| `content_hash` | 规范化内容哈希 |
| `context_revision` | 提交时的会话版本 |
| `created_at` | 创建时间 |

约束：`(conversation_id, sequence)` 唯一；payload 最大 256 KB；租户查询必须同时带 `organization_id` 与 `conversation_id`。

### 3.2 conversation_artifacts

保存任意工具或执行器产生的完整事实及多种消费视图。

| 字段 | 说明 |
|---|---|
| `id` | 稳定 Artifact ID |
| `organization_id` / `conversation_id` / `task_id` | lineage |
| `tool_call_id` / `tool_name` | 来源 |
| `artifact_type` | text、json、table、file、image、error、mixed |
| `status` | pending、ready、failed、cancelled |
| `storage_kind` | inline、oss、message_slice |
| `inline_content` / `storage_ref` | 完整事实二选一 |
| `model_view` | 当前或后续模型的有界视图 |
| `history_view` | 长期历史的短视图 |
| `content_hash` / `byte_size` | 完整性 |
| `metadata` / `sensitivity` | schema、分页、权限和脱敏信息 |
| `created_at` / `expires_at` | 生命周期 |

新结果不超过 64 KB 时可 inline；超过 64 KB 存 OSS。历史回填可使用 `message_slice` 引用既有不可变消息块，避免部署时复制大数据。Artifact ID 按会话、tool_call 和内容稳定生成并用于幂等；内容哈希只用于会话内检索和完整性校验，禁止跨调用或跨租户静默合并。

### 3.3 conversation_compactions

保存结构化压缩结果、覆盖区间与来源哈希。

核心字段：`from_sequence`、`through_sequence`、`source_hash`、`summary_payload`、`summary_hash`、`model`、`prompt_version`、`pass_count`、`input_tokens`、`output_tokens`、`status`。同一来源指纹只能有一个 ready 结果。

### 3.4 conversation_context_receipts

保存每次真实 Provider 请求所消费的计划，不保存重复正文。

核心字段：`task_id`、`model_step`、`base_revision`、`plan_hash`、`model`、`block_refs`、`estimated_tokens`、`provider_tokens`、`trimmed_refs`、`compaction_id`、`created_at`。用于解释“模型当时看到了什么”和核对预算偏差。

## 4. 运行时协议

### 4.1 ConversationItem

`ConversationItem` 是存储和组装的唯一基础类型。`tool_call` 与 `tool_result` 必须共享 `group_id`；Artifact 正文不进入 item payload，只保存 ID、摘要、类型、大小、哈希、是否完整和读取游标。

### 4.2 ActionResultNormalizer

所有工具返回值先规范化，不改变现有工具签名：

- 字符串、数字、布尔值和空值转为 text/json Artifact；
- `AgentResult` 保留 summary、data、file_ref、error 和显示 payload；
- 文件、图片、表格及混合 ContentPart 保留结构与 lineage；
- 异常形成 typed error result，不伪装成普通文本；
- UTF-8 字节数、规范化 JSON 和哈希在同一层计算。

### 4.3 模型视图与读取工具

默认单个工具结果的直接模型视图上限为 40 KB，并受当前模型剩余 Token 预算进一步约束。超过预算时返回：

- 稳定 `artifact_id`；
- 结果类型、总大小、哈希和 schema；
- 有界摘要及首尾预览；
- `truncated=true`；
- `next_cursor` 和可读取范围。

内部通用工具提供 `artifact_get`、`artifact_search`、`artifact_read`。`artifact_read` 支持 cursor、selector 和 max_tokens；权限检查使用当前租户、会话和用户上下文。系统提示要求模型在答案依赖被截断部分时继续读取，不能把预览当完整结果。

### 4.4 本轮与跨轮

本轮 ToolResult 可从 `RuntimeState` 的 Artifact draft registry 读取，避免在 Actor 最终提交前暴露未 fenced 的数据库事实。大对象先按 task 前缀上传 OSS；物化中断、租约丢失、提交异常或非 committed 结果立即 best-effort 删除本次新对象，清理失败记录 task_id、object_key 和错误类型。

跨轮只读取 ready Artifact。完整正文永远可召回，是否直接注入由 ContextPlan 决定。

已落地的跨轮消费边界：`ContextSnapshot` 先读取固定
`conversation_id + base_revision + summary_revision + org_id` 范围内的
`ConversationItem`。只要该范围存在新事实，就完全禁止拼接旧 `messages`
历史；仅未回填会话允许 legacy 回退。Artifact Search/Get/Read 使用同一
revision/org 边界，并统一读取 inline、OSS 和历史 `message_slice`。
Redis 只缓存该投影，v5 schema 使旧投影自动失效。

## 5. Token 规划与压缩

预算来自模型能力，不使用固定总字符数：

```text
usable = context_window
       - max_output_tokens
       - provider_safety_margin
       - fixed_prompt_tokens
```

Block 优先级：

1. `required`：当前用户输入、系统约束、未闭合工具组；
2. `protected`：最近对话、最新有效 compaction、当前任务相关 Artifact；
3. `ranked`：历史事实、Evidence、Memory、Knowledge；
4. `optional`：低相关历史和展示性内容。

软阈值达到可用窗口的 75% 时预触发压缩，硬阈值前必须完成裁剪或压缩。压缩按完整工具组和 Turn 边界选择稳定前缀，采用两段结构化摘要：

1. 第一段总结已稳定的旧前缀：目标、约束、决策、事实、Artifact refs、失败与未完成项；
2. 第二段合并靠近当前的尾部变化，保留最近至少两个用户 Turn，预算允许时保留更多。

压缩失败时使用确定性降级：保留 required/protected、Artifact 引用和原子组，按 ranked 分数裁剪；仍超过硬上限则切换到支持更大窗口的已配置模型或返回明确错误，不静默丢事实。

落地实现由 `ContextPlan` 承担最终消费协议：历史低于软阈值时仅剥离
内部 sequence/revision 元数据；超过软阈值时以倒数第二个用户 Turn 为
稳定前缀切点，完整保留最近两个用户 Turn 和其中的 tool call/result。
主、备摘要模型依次生成 goals/constraints/decisions/facts/
artifact_refs/failures/unfinished；两者失败时使用有界确定性结构化降级。
压缩产物随当前 `GenerationOutcome` 进入 fenced commit。下一轮先读取
最新 ready compaction，只加载 `through_sequence` 之后的原始项。
最终完整 Prompt 仍超过硬阈值时明确返回
`CONTEXT_PLAN_EXCEEDS_HARD_LIMIT`，禁止旧逻辑把当前输入替换为
“已归档”。分页扫描最多 5,000 项，达到上限明确失败而非静默截断。

模型缓存只影响费用和延迟，不参与正确性。六小时或二十四小时后缓存失效，系统仍从持久事实生成相同语义的 ContextPlan；稳定前缀与 plan hash 有利于重新建立缓存。

## 6. 原子提交与并发

`commit_generation_turn_vNext` 在校验 Actor lease、task status 和 base revision 后一次完成：

1. 校验 Artifact draft 元数据和对象哈希；
2. 插入 ready Artifact 元数据与 Evidence；
3. 插入有序 ConversationItem；
4. 插入消息、ContextReceipt 和可选 Compaction；
5. 更新任务终态与 conversation revision；
6. 写入投递 Outbox。

任一步失败则数据库事务整体回滚。重试使用 `task_id + local_sequence`、Artifact hash 和 compaction source hash 幂等。并发摘要使用现有 revision CAS；过期结果不得覆盖较新摘要。

## 7. 历史数据与直接切换

迁移采用 additive schema，不修改或删除原 `messages`：

1. 停止入口并 drain Actor；
2. 应用新表、RLS、索引和 vNext RPC；
3. 停止聊天入口并 drain Actor 后运行幂等批处理脚本，把历史消息解析为
   typed ConversationItem；回填完成前不得部署新消费者，避免回填游标
   之后、应用切换之前产生只存在于旧 messages 的事实缺口；
4. 历史长工具结果建立 `message_slice` Artifact；
5. 校验会话数、消息覆盖率、tool pair 完整率、hash 和租户隔离；
6. 部署新代码并启动 Worker、Backend；
7. Web 和企业微信同时进入统一主链；
8. 完成普通聊天、大工具结果、连续追问、重启恢复和压缩 smoke。

回滚时停止入口、drain、新代码回退并恢复旧 RPC。旧 `messages` 从未删除，因此旧版本可立即继续工作；新表先只读保留，不在故障回滚中做破坏性删除。

生产兼容性：自建 PostgreSQL 由应用 owner 角色执行迁移；仅当
`pg_roles` 中存在 `service_role` 时追加 Supabase 表权限和函数执行权限。
迁移与 rollback 必须先在带 `lock_timeout`、`statement_timeout` 的单事务
中预演并最终回滚，再进入正式维护窗口。

## 8. 实施文件清单

### 新增

- `backend/migrations/138_unified_conversation_context.sql`
- `backend/migrations/rollback/138_unified_conversation_context_rollback.sql`
- `backend/migrations/139_actor_artifact_terminal_integrity.sql`
- `backend/migrations/rollback/139_actor_artifact_terminal_integrity_rollback.sql`
- `backend/scripts/backfill_conversation_context_items.py`
- `backend/services/agent/runtime/artifacts/types.py`
- `backend/services/agent/runtime/artifacts/normalizer.py`
- `backend/services/agent/runtime/artifacts/store.py`
- `backend/services/agent/runtime/artifacts/projector.py`
- `backend/services/agent/runtime/artifacts/reader.py`
- `backend/services/agent/runtime/context/items.py`
- `backend/services/agent/runtime/context/planner.py`
- `backend/services/agent/runtime/context/assembler.py`
- `backend/services/agent/runtime/context/compactor.py`
- 对应迁移、单元、集成和端到端测试

Artifact 提交参数必须只携带当前 `storage_kind` 的有效字段：inline 只携带
`inline_content`，OSS 只携带 `storage_ref`。数据库写入触发器再次强制清除
互斥字段，避免应用 JSON `null` 与 SQL `NULL` 的语义差异使原子 Turn
提交在工具成功后回滚。确定性的完整性错误必须立即进入 fenced fail 协议；
只有提交结果未知的连接错误保留重试，防止重复执行昂贵工具。

### 修改

- `backend/services/agent/runtime/runtime_state.py`
- `backend/services/handlers/chat/tool_loop.py`
- `backend/services/handlers/chat/execution_engine.py`
- `backend/services/handlers/chat/executor.py`
- `backend/services/handlers/chat/stream_setup.py`
- `backend/services/handlers/context_snapshot.py`
- `backend/services/handlers/chat_context_mixin.py`
- `backend/services/prompt_builder/builder.py`
- `backend/services/conversation_runtime.py`
- `backend/services/conversation_delivery.py`
- `backend/services/agent/tool_executor.py`
- `backend/services/agent/evidence_tool_mixin.py`
- `backend/config/evidence_tools.py`
- `docs/PROJECT_OVERVIEW.md`
- `docs/FUNCTION_INDEX.md`
- `docs/CURRENT_ISSUES.md`

删除旧字符截断、工具名安全列表和重复历史压缩逻辑前，必须用全局调用方搜索证明新主链已覆盖其全部消费者。

## 9. 验收标准

必须覆盖：

- 任意工具返回 8 KB、40 KB、64 KB、1 MB 内容；
- `file_analyze` 和 ERP/Analyze 工具无需专用分支；
- 当轮模型可通过 Artifact 连续分页读取完整结果；
- 下一 Turn、24 小时后、Worker 重启后仍可召回；
- 工具失败、空结果、超时、取消、重试及模型切换；
- 压缩不拆 tool pair，不丢 Artifact refs；
- Compaction 失败时确定性降级；
- Actor 重复提交、旧 lease、revision 冲突和 OSS 上传后 DB 失败；
- 历史回填幂等、可恢复、无跨租户读取；
- Web 与企业微信对同一会话事实生成相同 plan hash；
- 所有 Provider 请求均有 ContextReceipt，实际 Token 不超过模型硬上限。

发布门禁为：迁移测试、后端相关全量测试、覆盖率、文件/函数/复杂度阈值、安全扫描、历史回填 dry-run 和生产前 smoke 全部通过。
