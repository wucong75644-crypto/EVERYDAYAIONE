# Grok 式通用记忆运行时重构

> 状态：方案已确认，待分阶段实施
> 日期：2026-07-19
> 基线：Grok Build 开源 Memory Flush / Session Memory / Dream / Hybrid Retrieval
> 目标：删除 Memory Runtime 内的电商等业务偏向，建立唯一通用记忆生命周期

## 1. 结论

记忆系统收口为通用运行时：

```text
Conversation Facts
  -> Session Capture
  -> Incremental Memory Flush
  -> Session Memory
  -> Consolidation
  -> Curated Memory
  -> Search / Get / Prompt Projection
```

Memory Runtime 只负责生命周期、证据、存储、去重、冲突、时效、召回和审计，不理解
退款率、SKU、平台、利润等业务概念。未来领域差异只能通过受限 Skill Profile 提供；
Skill 不拥有写库权、召回权或 Prompt 注入权。

## 2. 项目上下文

### 2.1 架构现状

当前 `services/memory/` 使用 L1 Atom、L2 Scene、L3 Persona 管道。L1 提取 Prompt 和
Scheduler 内置电商关键词、固定 domain 枚举及业务类型；L2/L3 继续用 LLM 重写 L1
内容。PromptBuilder 首次查询 `MemoryServiceV2`，将 L1 召回和 L3 Persona 作为会话
稳定块。完整对话、ContextSnapshot、Artifact 和 ContextReceipt 已有独立事实边界，
可作为新运行时的来源与观测基础。

### 2.2 可复用模块

- `context_snapshot.py`：固定一次提取可见的闭合 revision。
- `memory_atoms`：迁移后作为 Curated Memory 兼容存储。
- `retrieval_pipeline.py`：复用 pgvector、全文搜索和 RRF 基础设施。
- `pipeline_scheduler.py`：复用持久状态与单用户调度思想，替换业务门禁。
- `ContextReceipt`：扩展为 Memory Flush / Recall Receipt。
- `PromptBuilder`：保留最终上下文装配权。
- `memory_search/get` 目标协议：与 Artifact Search/Get 一致采用稳定 ref。

### 2.3 设计约束

- 原始消息和工具结果是事实源；摘要、Session Memory、Persona 都不是。
- 正式记忆必须能追溯到用户原始消息；assistant/tool 不能单独证明用户事实。
- 任一模型、解析、Embedding、去重或存储失败均不得写入正式记忆。
- Memory Runtime 不包含领域关键词、领域枚举、领域字段或领域 Prompt。
- ToolCall 产生的业务数字进入 Artifact/Evidence，不自动成为长期记忆。
- Web、企微和 Headless 共用同一 Turn 终态后置入口。
- 迁移期间保持现有 API 和 PromptBuilder 调用兼容，可按版本回滚。
- 新增文件不超过 500 行，函数不超过 120 行。

### 2.4 潜在冲突

1. 当前 `l1_dedup.py` 在无候选、解析失败、模型失败时 `store_all`，与 fail-closed 冲突。
2. 当前 L1 Prompt 固定 `ecommerce/finance/tech/product/operations` domain。
3. 当前 Scheduler 使用电商关键词和数字触发，普通分析请求可能被误判为长期事实。
4. L2 Scene 与 L3 Persona 是 LLM 派生文本，却可能作为新的 Prompt 事实进入模型。
5. 当前测试主要验证 JSON 能解析，缺少假设、示例、转述、临时条件和助手推断负例。
6. `memory_service_v2.py` 兼容提取入口缺少 `await get_scheduler(...)`。

## 3. 目标模块边界

### 3.1 Memory Runtime

通用运行时提供：

- Flush trigger 和固定 revision 输入。
- 增量 cursor、单 in-flight、失败 suppression。
- `NO_MEMORY | CANDIDATES` 模型协议。
- 结构、来源和原文引用验证。
- exact/semantic dedup。
- Session Memory 持久化。
- Consolidation 和 Curated Memory 晋升。
- conflict/supersede/expiry。
- hybrid search、时间衰减、MMR 和 Search/Get。
- Memory Receipt、指标和审计。

### 3.2 Skill Profile

可选 Skill 只能提供：

```text
skill_id / version
allowed_kinds
optional attribute schema
extraction guidance
rejection guidance
conflict hints
recall intents
render hint
```

Runtime 对 Skill Profile 做大小、版本和权限限制。没有 Skill 时，通用提取仍能保存用户
明确偏好、长期指令、重要决定、可复用问题解决经验和明确跟踪计划。

Skill 禁止：

- 直接写 Memory 表。
- 自建索引或缓存。
- 绕过 Evidence Validator。
- 直接把内容注入 Prompt。
- 将工具结果升级为长期用户事实。

## 4. 信息模型

### 4.1 Session Memory

Session Memory 是带来源和时效的会话知识，不默认等于永久事实：

```json
{
  "id": "uuid",
  "conversation_id": "uuid",
  "from_revision": 10,
  "through_revision": 18,
  "trigger": "pre_compaction|idle|session_end|manual",
  "content": {
    "decisions": [],
    "reusable_context": [],
    "problems_and_solutions": [],
    "candidate_facts": []
  },
  "source_refs": [],
  "content_hash": "sha256",
  "status": "active|superseded|rejected",
  "model": "model-id",
  "prompt_version": "generic-memory-flush-v1"
}
```

### 4.2 Memory Candidate

```json
{
  "claim": "用户偏好回答先给结论",
  "kind": "preference",
  "scope": "long_term",
  "explicitness": "explicit",
  "evidence": [
    {
      "message_id": "uuid",
      "quote": "以后回答先给我结论"
    }
  ],
  "valid_from": null,
  "valid_until": null,
  "attributes": {}
}
```

模型不得决定最终 priority、status 或写入动作。

### 4.3 Curated Memory

通用 kind 首期限定为：

- `user_profile`
- `preference`
- `instruction`
- `decision`
- `reusable_context`
- `problem_solution`
- `tracked_plan`
- `skill_defined`

状态：

- `active`
- `superseded`
- `conflict`
- `expired`
- `deleted`

### 4.4 派生视图

Topic/Persona 只能从 active Curated Memory 生成，保存关联 ref 和生成版本。派生视图不
能覆盖来源、不参与冲突裁决、不作为无引用的权威事实。普通数据分析默认不注入 Persona。

## 5. Memory Flush

### 5.1 触发

- 上下文压缩阈值前预留 headroom。
- 会话累计达到有效消息阈值。
- 可配置 idle timeout。
- 会话成功结束。
- 用户主动触发。

同一 `conversation + through_revision + prompt_version` 只允许一个成功 Flush。

### 5.2 输入

- 固定闭合 revision。
- 从上次成功 cursor 后读取。
- 最多 20 条最近有效消息。
- 排除 system 指令、内部日志、thinking 和 UI progress。
- assistant 仅供理解上下文，不能作为用户事实的唯一 evidence。
- Flush 模型不提供工具。

### 5.3 输出

只接受：

```json
{"decision":"NO_MEMORY"}
```

或合法 `CANDIDATES`。普通问答、标准流程、临时进度、一次性参数和无新发现会话必须
返回 `NO_MEMORY`。

### 5.4 写入门禁

候选必须通过：

1. Schema 完整且枚举合法。
2. Evidence message 存在且位于固定 revision。
3. 至少一个 evidence 来源为 user。
4. quote 是对应原消息的精确子串。
5. claim 不依赖 assistant/tool 推断才能成立。
6. 不是问题、假设、示例、转述或未确认计划。
7. 一次性条件不进入长期 scope。
8. 与现有 active memory 冲突时不得自动合并。
9. exact hash 和 semantic dedup 通过。
10. 数据库提交成功后才推进 cursor。

任何失败均拒绝写入，不采用 `store_all` 降级。

## 6. Consolidation

Consolidation 对标 Grok Dream：

- 至少 3 份新增 Session Memory。
- 距上次执行至少 4 小时。
- 同一用户单 in-flight。
- 读取现有 Curated Memory 与新增 Session Memory。
- 合并重复、识别明确变更、标记 superseded/conflict。
- 不允许新增无来源事实。
- 输出经过相同 Evidence Validator。

模型失败时保留 Session Memory，稍后重试；不影响对话。

## 7. Retrieval

### 7.1 路径

- 新会话首轮自动检索。
- Context Compaction 后重新检索。
- 模型主动 `memory_search` / `memory_get`。

### 7.2 排序

基础排序复用 hybrid search：

```text
vector_similarity * 0.7 + text_score * 0.3
```

随后应用：

- source/status 权重。
- Session Memory 时间衰减，默认半衰期 7 天。
- Curated Memory 的 valid time。
- conflict/expired/deleted 排除。
- MMR 去冗余。
- 每步最多返回 6 条，自动注入最多 3 条。

### 7.3 注入

PromptBuilder 只接收 Runtime 选择后的 `MemoryBlock`，包含 ref、类型、来源时间和必要的
staleness 提示。Skill 不能直接 append system prompt。

## 8. 数据库迁移

### 8.1 新表 `memory_session_logs`

字段：

| 字段 | 类型 | 约束 |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | NOT NULL |
| conversation_id | UUID | NOT NULL |
| from_revision | BIGINT | NOT NULL |
| through_revision | BIGINT | NOT NULL |
| trigger | TEXT | NOT NULL |
| content | JSONB | NOT NULL |
| source_refs | JSONB | NOT NULL DEFAULT `[]` |
| content_hash | TEXT | NOT NULL |
| status | TEXT | NOT NULL |
| model | TEXT | NOT NULL |
| prompt_version | TEXT | NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now() |

唯一约束：

```text
(conversation_id, from_revision, through_revision, prompt_version)
```

### 8.2 扩展 `memory_atoms`

新增兼容字段：

- `status`
- `source_session_log_id`
- `explicitness`
- `valid_from`
- `valid_until`
- `superseded_by`
- `confirmed_by_user`
- `content_hash`
- `last_recalled_at`
- `recall_count`
- `skill_id`
- `skill_version`

旧 `type` 数据保留并映射到通用 kind。旧 `domain/category/scene_name` 不再由 Runtime
生成；迁移阶段只读兼容，不立即物理删除。

### 8.3 回滚

- 新字段均为 additive。
- 新表可停止写入，不影响旧表。
- PromptBuilder 保留短期版本开关以切回旧读取。
- 稳定完成数据对账后，另立任务删除旧 L2/L3 权威链和无用字段。

## 9. API

现有 `/memories` API 第一阶段保持兼容。后续响应增加：

- `status`
- `kind`
- `source_refs`
- `valid_from/valid_until`
- `confirmed_by_user`

新增内部工具：

- `memory_search(query, filters, limit)`
- `memory_get(ref)`
- `memory_flush(conversation_id)`（仅用户主动或内部授权）

所有响应继续使用项目统一 `success/data/error/meta` 外壳。

## 10. 边界场景

| 场景 | 处理 |
|---|---|
| 空会话/无有效消息 | `NO_MEMORY`，不调用或不写 |
| 模型超时/空输出 | 记录 receipt，不写、不推进 cursor |
| 非法 JSON/未知 kind | 拒绝整批或逐项拒绝，不推测修复 |
| Evidence 缺失/越界 | 拒绝候选 |
| assistant 单独支持 | 拒绝候选 |
| Embedding/去重失败 | 不写候选 |
| 新旧事实冲突 | 标记 conflict，不自动注入 |
| 明确纠正旧事实 | 新记录 active，旧记录 superseded |
| 并发 Flush | 唯一键 + single-flight，只提交一个 |
| Flush 与新 Turn 竞态 | 固定 through_revision，新 Turn 留到下次 |
| 大消息 | 受预算裁剪，但 evidence quote 必须来自保留原文 |
| 用户删除 | 删除 Curated、Session 索引和派生视图引用 |
| Skill 卸载/升级 | 已存事实保留；无 Profile 时按通用视图读取 |
| Context 压缩失败 | Memory Flush 不阻断 Compaction |

## 11. 架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 从业务管道收口为通用 Runtime + Skill Profile | 中 | 先加新模块，后移除旧入口 |
| 数据流 | 增加 Session Memory 与晋升阶段 | 中 | revision、cursor、唯一键 |
| 扩展性 | 用户 10x 后 Flush/Embedding 成本增加 | 中 | 增量窗口、NO_MEMORY、异步调度 |
| 耦合度 | PromptBuilder、Actor、DB、Skill Registry | 中 | 稳定 MemoryRuntime Facade |
| 一致性 | 与 Artifact/Context Search/Get 模式一致 | 低 | 复用 ref/receipt 约定 |
| 可观测性 | 当前缺误提取和晋升指标 | 中 | Flush/Validation/Recall Receipt |
| 可回滚性 | 新旧管道切换和数据双读风险 | 中 | additive migration、版本开关、对账 |

无需要暂停设计的高风险项。

## 12. 方案选择

| 维度 | 仅改 Prompt | 完全复制 Grok 文件记忆 | 通用 Runtime + 结构化存储 |
|---|---|---|---|
| 误提取控制 | 弱 | 中 | 强 |
| 现有数据兼容 | 高 | 低 | 高 |
| SaaS 检索与审计 | 弱 | 弱 | 强 |
| 业务硬编码 | 仍存在 | 无 | 无 |
| 可扩展 Skill | 弱 | 中 | 强 |

选择“通用 Runtime + 结构化存储”：复制 Grok 生命周期，不复制其本地 Markdown 存储。

## 13. 文件清单

### 新增

- `backend/services/memory/contracts.py`
- `backend/services/memory/candidate_validator.py`
- `backend/services/memory/session_flush.py`
- `backend/services/memory/embedding.py`
- `backend/services/memory/consolidator.py`
- `backend/services/memory/recall_policy.py`
- `backend/services/memory/skill_profile.py`
- `backend/migrations/140_generic_memory_runtime.sql`
- `backend/migrations/rollback/140_generic_memory_runtime_rollback.sql`
- 相关单元、迁移、集成和 Eval 测试。

### 修改

- `backend/services/memory/l1_extractor.py`
- `backend/services/memory/l1_dedup.py`
- `backend/services/memory/pipeline_scheduler.py`
- `backend/services/memory/retrieval_pipeline.py`
- `backend/services/memory/memory_service_v2.py`
- `backend/services/memory/prompts/l1_extraction.py`
- `backend/services/memory/l2_scene_manager.py`
- `backend/services/memory/l3_persona_generator.py`
- `backend/services/prompt_builder/builder.py`
- `backend/services/handlers/chat_context_mixin.py`
- `docs/PROJECT_OVERVIEW.md`
- `docs/FUNCTION_INDEX.md`
- `docs/CURRENT_ISSUES.md`

## 14. 开发任务

### Phase 0：契约与基线

1. 建立误提取 Eval：假设、示例、临时请求、工具数字、助手推断、冲突和纠正。
2. 新增通用 contracts 和纯函数 Evidence Validator。
3. 固定 fail-closed 测试，不改变生产写入。

### Phase 1：安全写入

1. 替换提取 Prompt 为 `NO_MEMORY | CANDIDATES`。
2. 删除所有 `fallback_store_all`。
3. 接入 Evidence Validator 和严格 parser。
4. 修复 scheduler await 与统一终态入口。

### Phase 2：Session Memory

1. 添加迁移和 repository。
2. 实现 revision cursor、20 消息窗口和 single-flight。
3. exact/semantic dedup 与 Receipt。
4. PromptBuilder shadow 读取并与旧结果对账。

### Phase 3：Consolidation 与召回

当前进度（2026-07-19）：3.1a/3.1b 已完成。Consolidation 仅消费 3–25
份 ready Session Log，间隔至少 4 小时；模型只能返回四类关系，事实内容必须
逐字沿用已验证 Session candidate。迁移 142/143 尚未应用。

3.2a 已完成通用 Search/Get 内核：查询硬过滤 active、删除状态和有效期，
取消 domain 业务过滤，以向量/BM25 统一相关性、硬阈值、时间衰减和 MMR
形成有界结果；Get 按 atom、组织、用户重新授权并返回 Evidence lineage。
3.2b 已完成：PromptBuilder 首轮自动注入最多 3 条 Curated Memory；
Context Compaction 后先失效旧会话缓存，再按当前问题重新 Search，失败时保持空记忆。
个人上下文获准时模型可主动调用 `memory_search/memory_get`，Search 最多返回 6 条
并使用 `memory:<atom_id>` 稳定 ref；工具执行时仍以 user/org/status/valid time
重新授权。

3.3 已完成：Scheduler 删除业务关键词门禁及 L2/L3 调度、生成入口；PromptBuilder
与 MemoryService 不再读取或注入 Persona，旧 Session cache 中的 Persona 也会被
忽略。历史 Scene/Persona 表与显式管理读取 API 暂保留只读兼容，待迁移应用、
数据对账和回滚窗口结束后再决定物理删除。

1. 实现通用 Consolidation。
2. 实现 status/conflict/supersede/expiry。
3. 实现时间衰减、MMR、Search/Get。
4. 首轮、压缩后和主动检索接入。

### Phase 4：移除业务偏向

1. 删除 Scheduler 电商关键词。
2. 删除 Prompt 固定 domain/category。
3. 停止 L2 Scene/L3 Persona 作为权威写入和默认注入。
4. 增加受限 Skill Profile 扩展点。
5. 旧数据兼容读取、回填、对账后再决定物理清理。

### Phase 4.1 停用实现物理清理（已完成）

零生产调用的 `l2_scene_manager.py`、`l3_persona_generator.py` 及对应 Prompt 已删除；
配置层同步移除 L2/L3 模型与定时字段，通用召回接口移除未参与执行的 `domain`
参数。历史表和显式管理读取 API 暂不删除，仅用于旧数据查看和回滚。

### Phase 4.2a 旧 L1 直写链退出（已完成）

Scheduler 只接受数据库已闭合 revision；缺失 revision 时不读取或更新 pipeline
state。`L1Extractor` 只负责无副作用候选提议，旧 L1 Dedup 服务、Prompt、模型配置
和关键词质量门已删除；去重与关系生命周期统一由 Session Flush 和 Consolidation
执行。历史 Scene/L2/L3 数据库字段继续保留，但 Runtime 不再维护。

### Phase 4.2b 召回旧语义退出（已完成）

Search/Get SQL、`ScoredMemory`、Facade、Agent 工具和 Prompt 注入只使用通用
`kind`，不再读取或输出旧 `type/scene_name`；历史数据缺失 kind 时使用中性
`memory` 标签。零调用的 V2 手动 CRUD 与旧 Atom 直接插入函数同步删除。
公共 `/memories` 与企微管理仍由旧 Mem0 服务承担，迁移归入独立 Phase 4.3。

## 15. 测试与验收

必须覆盖：

- 非法输出写入率为 0。
- 无有效用户 evidence 写入率为 0。
- assistant/tool 单独支持写入率为 0。
- 模型、Embedding、去重失败写入率为 0。
- 假设、问题、示例、一次性条件不晋升。
- 明确长期规则可晋升并召回。
- 明确纠正后旧记忆不再注入。
- Flush 并发、重试和 revision 竞态。
- Web、企微、Headless 行为一致。
- 每条正式记忆可追溯原始消息。

质量目标：

- 核心新增模块覆盖率不低于 90%。
- 负例 Eval 误写率低于 1%，硬门禁类误写率必须为 0。
- 现有自动化基线不新增失败。

## 16. 文档与发布

- 新增文件后更新 `PROJECT_OVERVIEW.md`。
- 新增/修改函数后更新 `FUNCTION_INDEX.md`。
- 每阶段结论和遗留风险更新 `CURRENT_ISSUES.md`。
- 先 shadow、再双读对账、再切读、最后停止旧写入。
- 每阶段独立可回滚，不一次性删除旧表和旧数据。
