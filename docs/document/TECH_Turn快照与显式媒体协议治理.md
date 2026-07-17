# Turn 快照与显式媒体协议治理

> 版本：v1.0
> 日期：2026-07-17
> 等级：A级
> 状态：分阶段实施中（任务 1-4 已完成）

## 1. 目标与范围

本次治理同时解决两个根因一致的问题：

1. 普通文本中的图片、视频或 `[FILE]` 标记被扫描并升级为结构化媒体，导致代码示例、JSON URL 被持久化成幽灵媒体卡片。
2. 同一会话的生成任务缺少输入锚点、Turn 归属和历史版本，任务从可变全量历史构造上下文，并在工具循环中覆盖共享 Redis messages，存在上下文串线和丢失更新风险。

目标约束：

- 内容类型由可信生产者声明，不从自然语言推断。
- 一个生成任务必须绑定一个确定的用户输入和 Turn。
- 任务启动时获得不可变上下文快照；重试不得重新读取更新后的会话历史。
- 数据库追加式消息是事实来源；Redis 只缓存带版本的闭合历史。
- 普通聊天默认串行；显式并行任务使用相同基线版本的独立分支快照。
- 不迁移或清洗既有错误消息数据。
- 不保留 `[FILE]...[/FILE]` 兼容解析。

## 2. 项目上下文

### 2.1 架构现状

- `PromptBuilder` 是 Web 聊天模型上下文的统一入口，负责并行加载 memory、summary、history，并在末尾执行预算压缩。
- `history_loader.build_context_messages()` 从 messages 表读取已完成或已中断的用户/助手消息，但没有输入消息 ID 或历史截止点；尾部去重依赖文本相等。
- `conversation_cache` 以 `conv:msgs:{org}:{conversation}` 保存可变 messages 数组；`ChatHandler` 在工具循环和完成阶段写回完整数组。
- 沙盒已经提供 `emit_image/emit_file/emit_chart/emit_table` 结构化协议，但聊天完成阶段仍对普通模型文本调用 `extract_media_parts()`。
- 前端已使用 `ContentPart[]` 和运行时校验，但 `AiImageGrid` 仍可能按请求数量而非实际 ImagePart 数量建立格子。

### 2.2 可复用模块

- `services/sandbox/emit_protocol.py`：正式结构化产物生产协议。
- `ChatToolMixin._pending_emit_payloads`：任务私有的结构化产物暂存入口。
- `schemas.message.ContentPart`：后端显式类型边界。
- 前端 `ContentPart` Zod 运行时边界和 `messageUtils` 类型筛选。
- `client_request_id/client_task_id`：请求幂等基础。
- `context_compressor`：只在固定快照内部执行预算压缩，不负责并发一致性。

### 2.3 设计约束

- 保持现有消息 API 响应兼容；新增数据库字段初期均允许空值。
- 不把工具循环的私有中间 messages 写入共享会话缓存。
- 不引入新第三方依赖。
- 数据库变更使用 Supabase PostgreSQL SQL migration，并提供 rollback SQL。
- 所有新增日志必须包含 `conversation_id/task_id/turn_id/base_revision`。

### 2.4 潜在冲突

- `TECH_上下文工程重构.md` 的 V3.3 Redis 完整 messages 设计被本方案取代；预算和摘要算法可以保留。
- `TECH_沙盒文件生成与下载.md` 中 `[FILE]` marker 设计已过期；正式入口改为 emit payload。
- 当前 `rate_limit_conversation_tasks` 允许同会话多个活动任务；治理时不能默认为天然线性历史。
- 历史任务缺少新字段，只能通过旧逻辑读取，不能获得并发快照保证。

## 3. 方案选择

| 维度 | 全会话串行 Actor | Turn 固定快照 + 显式分支 |
|---|---|---|
| 实现思路 | 同一 conversation 一次只运行一个 Turn | 每个任务绑定输入和基线 revision |
| 并行能力 | 无 | 保留显式并行任务 |
| 侵入性 | 中 | 中高 |
| 一致性 | 强线性一致 | 快照一致，分支显式 |
| 长任务体验 | 后续输入被阻塞 | 独立任务可并行 |
| 恢复与审计 | 简单 | 信息最完整 |

采用组合方案：普通聊天使用会话执行锁串行；产品明确允许的并行 Agent 任务创建分支快照。两种模式共用 Turn、revision 和 snapshot 数据模型。

## 4. 目标数据模型

### 4.1 messages 新增字段

| 字段 | 类型 | 约束/默认值 | 说明 |
|---|---|---|---|
| `turn_id` | UUID | NULL | 同一用户输入及其回答共享的逻辑 Turn ID |
| `reply_to_message_id` | UUID | NULL，FK messages(id) ON DELETE SET NULL | assistant 明确回复的 user message |
| `context_revision` | BIGINT | NULL，CHECK >= 0 | 消息进入闭合历史时的版本 |
| `message_kind` | TEXT | NOT NULL DEFAULT `conversation` | `conversation/synthetic/tool_internal`；共享历史只加载 conversation |

索引：

- `(conversation_id, context_revision, created_at)`
- `(conversation_id, turn_id)`
- `reply_to_message_id WHERE reply_to_message_id IS NOT NULL`

### 4.2 tasks 新增字段

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `input_message_id` | UUID | NULL，FK messages(id) ON DELETE SET NULL | 本任务唯一输入锚点 |
| `turn_id` | UUID | NULL | 任务所属 Turn |
| `base_context_revision` | BIGINT | NULL，CHECK >= 0 | 提交时看到的闭合历史版本 |
| `context_through_message_id` | UUID | NULL，FK messages(id) ON DELETE SET NULL | 快照历史截止消息 |
| `execution_mode` | TEXT | NOT NULL DEFAULT `serial` | `serial/branch` |

索引：

- `(conversation_id, turn_id)`
- `input_message_id WHERE input_message_id IS NOT NULL`

### 4.3 conversations 新增字段

| 字段 | 类型 | 约束/默认值 | 说明 |
|---|---|---|---|
| `context_revision` | BIGINT | NOT NULL DEFAULT 0 | 最新闭合历史版本 |
| `last_closed_message_id` | UUID | NULL，FK messages(id) ON DELETE SET NULL | 最新闭合消息锚点 |
| `summary_revision` | BIGINT | NOT NULL DEFAULT 0 | context_summary 覆盖到的版本 |
| `summary_through_message_id` | UUID | NULL，FK messages(id) ON DELETE SET NULL | 摘要截止消息 |

### 4.4 数据不变量

1. 新任务的 `input_message_id` 必须指向同会话的 user message。
2. 新 assistant 消息的 `reply_to_message_id` 必须等于任务的 `input_message_id`。
3. `base_context_revision` 在任务生命周期中不可修改。
4. task-private tool 消息不得进入共享 closed-history cache。
5. revision 只在 Turn 闭合事务中推进，不在流式 chunk 或工具步骤中推进。
6. summary 只能覆盖 `summary_revision` 及之前的闭合 Turn。

## 5. 数据库事务设计

新增两个 RPC，避免 API 层分步操作产生竞态。

### 5.1 `bind_generation_turn`

输入：`conversation_id, task_id, input_message_id, turn_id, execution_mode`。

事务内：

1. `SELECT conversations ... FOR UPDATE`。
2. 校验 user message、task 与 conversation/org 归属一致。
3. 读取 `context_revision/last_closed_message_id`。
4. 将基线信息一次写入 task；已有绑定时仅允许完全相同的幂等调用。
5. 返回 `base_context_revision/context_through_message_id`。

### 5.2 `close_generation_turn`

输入：`conversation_id, task_id, output_message_id`。

事务内：

1. 锁定 conversation 与 task。
2. 校验 task 未闭合且 output 回复 input。
3. `context_revision = context_revision + 1`。
4. 将本 Turn 的 user/assistant conversation 消息写入新 revision。
5. 更新 `last_closed_message_id` 并返回新 revision。
6. 重复关闭返回第一次结果，不重复推进 revision。

分支任务完成顺序不会改变回复关系；revision 表示闭合提交顺序，不代表用户输入归属。

## 6. ContextSnapshot

新增后端值对象：

```text
ContextSnapshot
  conversation_id
  task_id
  turn_id
  input_message_id
  base_revision
  through_message_id
  summary_revision
  history_messages
  conversation_source
```

构造规则：

- 查询条件必须包含 `context_revision <= base_revision`。
- 只加载 `message_kind=conversation` 且状态为 completed/interrupted 的闭合消息。
- 当前 user 通过 `input_message_id` 精确读取并在历史之后追加。
- 删除按 `current_text` 比较并弹出尾消息的逻辑。
- snapshot 构造一次后保存在 handler/task 生命周期；provider 重试复用同一对象。
- 预算压缩只处理 snapshot 的副本，不更新共享历史。
- conversation_source 只选择既有 Web/企微预算档位，不改变历史边界。

旧任务 `input_message_id IS NULL` 时使用 legacy loader；只提供可读兼容，不进入新 cache 写路径。

## 7. Redis 设计

缓存值由数组改为版本信封：

```json
{
  "schema_version": 2,
  "revision": 18,
  "through_message_id": "uuid",
  "closed_messages": []
}
```

规则：

- cache hit 必须同时精确匹配 `revision` 与 `through_message_id`。当前缓存内容不携带逐消息 revision，禁止从较新信封猜测截断边界；旧 revision 任务直接回源数据库。
- v1 数组缓存视为 miss 并删除，不继续写回。
- ContextSnapshot 在 miss 后从 DB 重建并写 v2；新 Turn 使用不同 revision，自动 miss，正确性不依赖跨数据库/Redis 双写失效。
- 工具循环、legacy loader 和 PromptBuilder 不得写共享缓存；任务私有工具消息只存在该任务的 messages 副本。
- 后续性能数据证明必要时再增加 Lua CAS 增量更新；本次不超前实现。
- Redis 故障一律降级 DB，不影响正确性。

## 8. 媒体协议治理

### 8.1 允许的生产者

- `emit_image` → `ImagePart`
- `emit_file` → `FilePart`
- 图片/视频生成 provider 的类型化结果
- 用户上传和工作区附件标准化入口
- 已声明 MIME/type 的工具结果

### 8.2 禁止的生产方式

- 从普通 assistant text 正则扫描 `.jpg/.png/.mp4` URL。
- 从 JSON、Markdown 代码块、HTML 或 ECharts 配置猜测媒体。
- 解析 `[FILE]...[/FILE]`。
- 发现文件扩展名后自动构造下载卡片。

`extract_media_parts()` 从聊天完成链路移除；如果无其他调用方则删除模块。普通模型文本始终产生单一 `TextPart`。显式 emit payload 按原顺序合并到最终 `ContentPart[]`。

### 8.3 前端渲染规则

- `AiImageGrid` 的 cells 只基于过滤后的实际 `ImagePart[]`。
- 生成中可以使用请求数量显示 pending skeleton；任务结束后只展示实际成功图片。
- text/chart/file/image 混合 content 不得使用原数组下标读取图片。
- `FilePart` 保留；删除的是文本 marker，不是正式文件类型。

## 9. 生命周期与边界场景

| 场景 | 处理策略 | 模块 |
|---|---|---|
| 两条用户文本完全相同 | 通过 input_message_id 区分，不做文本去重 | history/snapshot |
| 同会话快速提交两个普通聊天 | 会话锁串行，第二个在第一个闭合后重新绑定基线 | task orchestration |
| 显式并行任务 | 两者可共享 base revision，各自绑定输入和回复 | task/turn |
| 完成顺序相反 | reply_to 保持归属；revision 按提交事务排序 | DB RPC |
| Provider 网络重试 | 复用原 snapshot | handler |
| 用户重新生成 | 新 task/generation，沿用 input_message_id，创建新 turn_id | generation API |
| 用户编辑后重试 | 新 user message、新 turn | message API |
| 取消/超时 | 不关闭 Turn；保存 interrupted 输出但不发布私有 tool history | handler |
| 服务重启 | 从 task 绑定和 base revision 重建相同 snapshot | restore |
| Redis 旧数组 | 删除并回源 DB | cache |
| JSON 中含图片 URL | 保留 TextPart | media |
| `[FILE]` 文本 | 保留普通文本，不生成 FilePart | media |
| emit_file 上传失败 | 不产生 FilePart，保留工具错误并记录 task/turn | sandbox |
| 摘要 revision 超过任务基线 | 忽略该摘要，使用不超过基线的历史 | summary |

## 10. API兼容

- 现有创建消息和生成 API 路径不变。
- `start_generation_task()` 内部增加 turn 绑定，不要求旧前端立即传新字段。
- Message/Task 响应新增字段均为可选，旧前端可忽略。
- 前端后续可利用 `reply_to_message_id/turn_id` 稳定排序，但本次不改变聊天布局。
- 无破坏性 API 变更，不升级 `/v1`。

## 11. 连锁修改清单

| 改动点 | 影响文件 | 同步内容 |
|---|---|---|
| 数据字段/RPC | `backend/migrations/120_*.sql`、rollback | 字段、索引、事务函数、注释 |
| 任务输入锚点 | `message_turn_anchors.py`、`turn_binding.py`、`base.py` | Web/企微统一透传 input/turn/revision；旧 retry 受监控降级 |
| 固定快照 | `prompt_builder/builder.py`、`history_loader.py` | BuildInput 增加锚点，按 revision 加载 |
| 缓存信封 | `conversation_cache.py` | v2 schema、旧值失效、按版本读取 |
| 禁止共享覆盖 | `chat_handler.py` | 删除工具轮和完成阶段整数组 set_messages |
| 显式媒体 | `media_extractor.py`、`chat_generate_mixin.py`、`chat_handler.py` | 删除 URL/marker 扫描，TextPart + emit payload |
| 图片网格 | `AiImageGrid.tsx`、`MessageMedia.tsx` | 只消费实际图片数组 |
| 类型响应 | `backend/schemas/message.py`、`frontend/src/types/message.ts` | 新增可选 turn/reply 字段（如现有响应暴露） |
| 测试 | backend/frontend 相关测试 | 并发、重复文本、cache、媒体边界、恢复 |
| 文档 | overview/index/issues/旧技术文档 | 标注取代关系和新增函数 |

## 12. 实施任务拆分

1. **协议收口**：删除文本媒体扫描和 `[FILE]`，修正前端图片过滤；不依赖数据库迁移，可独立部署。
2. **数据库基础**：增加 nullable 字段、索引、RPC 和 rollback；旧代码不使用新字段，向后兼容。
3. **Turn 绑定**：任务创建绑定 input/turn/base revision，并补幂等测试。
4. **ContextSnapshot（已完成）**：PromptBuilder 使用锚点/revision，删除文本去重；Web/企业微信共用固定快照，企微恢复小预算配置。
5. **缓存与工具隔离（已完成）**：切换 v2 闭合历史信封，旧数组/损坏值失效；按 revision + through-message 精确命中；删除工具循环和 legacy 路径共享整表覆盖。
6. **串行/分支执行策略**：普通聊天加会话锁，显式并行传 execution_mode。
7. **摘要版本化与恢复**：摘要截止版本校验、旧任务降级路径。
8. **前后端全量回归、生产链路测试、文档与观测验收**。

每项完成后单独测试和确认，禁止跨任务一次性修改。

## 13. 可观测性

新增结构化日志事件：

- `turn_bound`
- `context_snapshot_built`
- `context_cache_hit/miss/stale`
- `turn_closed`
- `turn_close_conflict`
- `legacy_context_fallback`
- `untrusted_media_text_preserved`

核心字段：`org_id, conversation_id, task_id, turn_id, input_message_id, base_revision, closed_revision`。

建议指标：snapshot 构建延迟、DB回源率、旧任务降级数、同会话排队时长、revision 冲突数、emit payload 失败数。

## 14. 部署与回滚

部署顺序：

1. 媒体协议收口代码。
2. additive 数据库 migration。
3. 写入新字段但仍保留 legacy read。
4. 切换 snapshot read 和 v2 cache。
5. 开启串行/分支执行策略。

回滚：

- 应用可逐阶段回滚；新字段均 nullable，旧版本忽略。
- 回滚 snapshot 代码前删除 `conv:msgs:*` v2 缓存，避免旧代码把信封当数组。
- 数据库 rollback 仅在应用完全回滚且确认无新任务依赖后执行；删除 RPC、索引和新增列。
- 不回滚或改写历史消息 content。

## 15. 架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增 snapshot/turn 语义，保持 PromptBuilder 为装配入口 | 中 | 值对象独立，handler 只透传 |
| 数据流 | 从可变共享列表改为追加日志和固定快照 | 中高 | 分阶段双读、测试并发不变量 |
| 扩展性 | 查询由全量时间扫描变为 revision 索引范围 | 低 | 联合索引和分页 |
| 耦合度 | task/message/conversation 增加显式关系 | 中 | 关系通过 RPC 原子维护 |
| 一致性 | DB成为唯一事实来源，Redis不参与提交 | 低 | cache stale 回源 |
| 可观测性 | 当前缺少 Turn 维度 | 中 | 新日志和指标 |
| 可回滚性 | additive schema 可兼容旧应用 | 中 | 严格部署顺序和 rollback SQL |

## 16. 验收标准

- 普通文本、JSON、代码块中的任意媒体 URL 和 `[FILE]` 均只保存为 TextPart。
- `emit_image/emit_file` 仍生成对应结构化卡片。
- 相同文本连续提交不丢消息。
- 并发任务看不到其他未闭合 Turn，反序完成也不串回复。
- Provider 重试和服务恢复获得相同 base revision。
- 任务工具循环不再写共享 conversation messages cache。
- Redis 故障或旧缓存不影响正确性。
- 新旧任务、取消、超时、重新生成、摘要边界测试通过。
- 后端、前端全量测试和前端构建通过，无新增失败。
