# 长期单会话上下文与 Token 治理技术设计

> 状态：Phase 0–4 与统一观测已实施；独立上下文灰度已取消
> 日期：2026-07-18
> 任务等级：A级
> 目标：用户可长期在同一个对话中持续工作，已产生的结果可复用，模型输入 Token 有硬上限，缓存失效后仍可稳定恢复
> 对标：Grok Build 官方仓库提交 `8adf9013a0929e5c7f1d4e849492d2387837a28d`

## 1. 最终目标

本方案不追求把全部历史永久塞给模型，而是实现以下用户结果：

1. 当前工具循环内，模型完整看到每次工具调用、成功结果、失败结果和后续修正。
2. 下一 Turn 能直接复用近期工具结果、小文件读取结果和结构化业务数据。
3. 长对话不会随消息数量无限增长；缓存失效后重建的仍是有限活动上下文。
4. 老结果不会消失，而是转为带稳定引用的摘要或证据，模型需要时可精确取回。
5. 新问题与旧任务无关时，旧工具明细默认不进入活动工作集，但用户仍可显式引用。
6. 文件失效、权限撤销、数据过期时返回明确状态，不让摘要冒充可用原文。
7. 每次模型调用都能解释装入了什么、裁剪了什么、消耗多少 Token、缓存命中多少。

目标链路：

```text
完整事实存储
  -> 固定 revision ContextSnapshot
  -> ContextPlan（本步需要什么）
  -> ContextBlock Providers（历史、证据、文件、记忆）
  -> Budget Allocator + Compactor
  -> Stable ContextAssembler
  -> Provider Request
  -> ContextReceipt
```

## 2. 项目上下文

### 2.1 架构现状

系统已经使用 `turn_id + context_revision + base_revision` 固定一次生成能看到的闭合历史，
并以 PostgreSQL 为事实源、Redis 为 revision 精确匹配的缓存。工具结果在当前循环进入
OpenAI 风格 `assistant.tool_calls + role=tool` 数组，完成后又以 `tool_step/tool_result`
ContentPart 保存到 assistant 消息。迁移 135 已把结构化 `AgentResult` 作为
`conversation_data_evidence` 与 Turn 终态原子提交，但下一 Turn 只向模型展示证据 ID、
行数和列名。现有 PromptBuilder、两类摘要和多层 compressor 都能限制部分内容，但还
没有统一的活动工作集、可恢复引用和实际请求回执。

### 2.2 可复用模块

| 模块 | 复用方式 |
|---|---|
| `context_snapshot.py` | 保留为固定 revision 事实入口 |
| `conversation_cache.py` | 保留闭合投影缓存，升级 schema |
| `history_loader.py` | 改造成 HistoryBlock Provider |
| `data_context_snapshot.py` | 改造成 EvidenceBlock Provider |
| `ArtifactLedger/RuntimeState` | 保留结构化数据采集与 Turn 原子投影 |
| `resource_manifest.py` | 保留文件权限与当前任务资源边界 |
| `context_compressor/` | 复用 token、pair 修复、归档和摘要算法 |
| `PromptBuilder` | 保留指令层，逐步移交总装职责 |
| Google/DashScope adapter | 复用 Provider 实际 usage/cache 指标 |

### 2.3 设计约束

- PostgreSQL 是事实源；Redis、摘要和模型缓存都不是事实源。
- 同一 `base_revision + plan_revision` 的重试必须产生相同 block refs/hash。
- ToolCall 与 ToolOutput 不得拆开；未闭合调用必须显式标记 interrupted/unknown。
- 当前用户输入、权限约束和当前未完成 Action 不得被普通压缩淘汰。
- thinking、UI progress、内部日志和凭证不进入下一 Turn。
- 群聊不注入个人 Memory；所有证据 Get 必须重新校验 org/user/resource scope。
- 不修改现有前端 ContentPart 展示协议，不要求 ERP 工具改写公共返回类型。
- Web、企微和未来入口必须调用同一个 Context Runtime，不得分叉预算、压缩或摘要算法。

### 2.4 潜在冲突

1. 2026-07-17 的“历史工具内容污染治理”主动过滤所有正常 completed 工具协议；
   该策略防止代码和失败堆栈污染，却同时删除了下一 Turn 必需的工具事实。
2. `DataContextSnapshot.render_prompt()` 只展示目录，不展示小结果、口径、关键统计或
   `file_ref`，同时项目已删除模型可调用的 `data_compute/get` 路径。
3. 会话摘要虽读取 `summary_revision`，更新逻辑没有同步写入该字段，固定 revision
   Snapshot 很可能拿不到有效摘要。
4. 摘要和历史没有按覆盖范围互斥；可能同时发送已被摘要覆盖的原消息，浪费 Token。
5. 当前时间位于历史之前，严格前缀缓存从动态时间处中断，后续历史难以命中。
6. Web 200K 和企微 32K 是通道常量，不是按实际模型窗口和工具 schema 推导。
7. 工具归档依赖 `staging/...` 文本路径，缺少稳定、可鉴权、可报告 unavailable 的引用。

## 3. 用户需求矩阵

| 场景 | 系统行为 |
|---|---|
| 连续追问刚查出的 ERP 数据 | 直接装入热 Evidence model view，不重复查询 |
| 工具连续失败后修正 | 当前循环保留完整失败 pair；下一 Turn 保留失败摘要和最后一次有效参数 |
| 小文件读取后追问 | 近期完整保留读取结果；转冷后保留 chunk/ref |
| 大文件分析后追问 | 保留 schema、统计、选中行和 Artifact ref，不全量注入 |
| 6/24 小时后恢复 | 从 DB 重建有限活动工作集；KV cache miss 不影响语义 |
| 完全无关的新问题 | 保留会话摘要和长期偏好，旧任务工具明细退出工作集 |
| 追问很久以前的数据 | Search 返回证据目录，Get 精确恢复指定版本 |
| 数据已过期 | 显示生成时间和 query scope；要求“最新”时重新查询 |
| 文件被删除或无权限 | 返回 typed unavailable/forbidden，不假装能读取 |
| 对话持续数月 | 事实持续增长，模型活动上下文维持在固定预算内 |

## 4. Grok Build 对照结论

### 4.1 直接采用的原则

- 完整事实与活动模型上下文分离。
- 当前循环把 ToolCall/ToolResult 作为同一有序数组连续消费。
- 容量正常时保留近期结果，接近阈值后先裁剪旧工具结果。
- 自动 compaction 重建短活动历史，而不是缓存失效后重发无限原文。
- 保持稳定前缀和稳定会话标识，缓存只作为性能优化。
- 压缩失败有 suppression 和单 in-flight 控制。

### 4.2 不直接照搬

- 不使用本地 JSONL 作为 SaaS 主事实源，继续使用 PostgreSQL revision Snapshot。
- 不给所有模型固定 85% 阈值，按模型能力、输出预留和工具 schema 推导。
- 不长期回灌全部 reasoning。
- 不让大业务数据只依赖本地文件路径；必须有受控 Artifact/Evidence ref。
- 不把所有历史工具协议永久回灌；近期完整、较老摘要、归档按需 Get。

## 5. 当前根因

### 5.1 正常完成 Turn 的工具结果被提前删除

`history_loader._row_to_oai_messages()` 对正常 assistant 消息只调用
`extract_text_from_content()`。因此数据库虽然保存了 `tool_step.output` 和
`tool_result.text`，跨 Turn 模型投影仍看不到。

### 5.2 证据目录不可消费

`conversation_data_evidence` 已保存最多 200 行或 `file_ref`，RuntimeState 也会恢复；
但模型只看到：

```text
artifact_id=...; rows=...; columns=...
```

没有小结果正文、查询条件、指标口径、关键统计、文件引用，也没有稳定的
`evidence_get(ref)` 工具。系统“知道证据存在”，模型却无法使用证据。

### 5.3 压缩发生在信息丢失之后

历史先降成纯文本，随后才执行 tool/history budget。压缩器无法保护、摘要或归档已经
消失的 ToolOutput。因此不能简单把 `preserve_tool_protocol` 全局改成 `True`，必须先
建立受控 model view，再执行统一预算。

### 5.4 摘要覆盖边界没有真正闭环

会话摘要、循环摘要、Session Memory 和 Memory V2 曾同时描述过去；但没有统一
`from_revision/through_revision` 与权威优先级。摘要可能重复历史，也可能在当前用户
纠正事实后继续出现。

Phase 2 已收口该边界：DB 会话摘要负责跨 Turn 工作状态，循环摘要只负责当前 Run
实际淘汰的工具消息，Memory V2 只负责用户级长期事实，Evidence/Artifact 保存可复用
业务事实和原文引用；请求级 Session Memory 已删除。

## 6. 方案对比

| 维度 | A：仅恢复历史工具结果 | B：活动工作集 + Evidence ref | C：一次性完成全 Runtime |
|---|---|---|---|
| 思路 | completed Turn 全走现有 OAI 展开 | 事实、证据、工作集、模型投影分层 | 同时落地 Run/Action/Event/Context 全模型 |
| 短期修复速度 | 快 | 中 | 慢 |
| Token 可控 | 弱，长对话会膨胀 | 强，固定预算与分层退出 | 强 |
| 文件/大结果恢复 | 仍依赖路径 | 稳定 Search/Get | 稳定 Search/Get |
| 无关主题隔离 | 无 | 有 Working Set Policy | 有 |
| 侵入性 | 低 | 中，可 shadow 渐进 | 高 |
| 可观测性 | 低 | ContextReceipt | 完整 RuntimeEvent |
| 风险 | 由失忆变成 Token 爆炸 | 可灰度、可回滚 | 新旧状态双写风险高 |

推荐方案 B。方案 A 只能作为诊断开关，不允许作为生产最终策略；方案 C 属于更大的
Agent Runtime 迁移，不应阻塞本次用户体验修复。

## 7. 目标信息分层

| 层 | 内容 | 默认进模型 |
|---|---|---|
| L0 Fact | 完整消息、工具结果、文件、证据、事件 | 否 |
| L1 Control | Agent/Policy、当前输入、当前任务状态 | 必须 |
| L2 Hot Working Set | 最近完整 Turn、当前工具 pair、热证据 | 是 |
| L3 Continuation | 结构化摘要、决策、失败、未完成事项 | 按预算 |
| L4 Retrieval | 老证据、文件、旧 ToolOutput、Memory | Search/Get 后 |

任何信息进入 L2/L3 时必须知道：

- 来源 ID 和 revision；
- 内容 hash；
- token estimate；
- 敏感级别和 scope；
- 退出工作集的条件；
- 被淘汰后如何恢复。

## 8. ToolOutput 与 Evidence 四视图

每次结构化工具结果统一产生四个视图：

```text
ActionResult
  ├─ model_view：给模型继续工作的紧凑事实
  ├─ display_view：前端/企微展示
  ├─ artifact_view：完整数据或文件引用
  └─ audit_view：脱敏状态、耗时、来源
```

`model_view` 初始分级：

| 结构化大小 | model_view |
|---|---|
| `<= 8 KB` | 完整结构化结果、query scope、metric definitions |
| `8–64 KB` | schema、确定性统计、选中首尾/高相关行、ref |
| `> 64 KB` | metadata、确定性统计、字段、ref |
| 二进制/媒体 | metadata、OCR/vision 摘要、ref |

失败结果分级：

- 当前循环：完整错误类型、可恢复原因和安全的错误详情。
- 最近 Turn：保留工具名、参数摘要、错误类型、是否重试成功。
- 冷历史：只进入 ContextSummary.failed_attempts，并保留 Action ref。
- 凭证、堆栈和内部路径不得进入跨 Turn model view。

## 9. 活动工作集算法

每个 ModelStep 基于固定 Snapshot 生成 ContextPlan：

```text
usable_input
= model_context_window
 - reserved_output
 - safety_margin

dynamic_budget
= usable_input
 - stable_instructions
 - effective_tool_schema
 - required_control_blocks
```

首期参数：

| 参数 | 建议初值 |
|---|---:|
| output reserve | `max(model_max_output, window × 12.5%)` |
| safety margin | `max(2048, window × 5%)` |
| soft compaction | usable input 的 75% |
| hard compaction | usable input 的 85% |
| emergency trim | usable input 的 92% |
| 完整 recent Turn | 目标 6，最低 2 |
| 完整工具结果 | 最近 3 个用户 Turn |
| 自动 Evidence | 最多 5 个高相关项 |
| Search 目录 | 最多 10 项 |
| 单步 Get | 最多 5 项 |

75% 比 Grok 默认参考值更保守，因为本项目工具 schema、ERP 多步循环和多模型 Provider
包装成本更高。参数上线后由真实 token/cached-token 数据校准。

装入优先级：

```text
required control/current input
-> current incomplete actions
-> explicitly referenced evidence/files
-> recent complete Turns
-> active task evidence
-> current valid summary
-> relevant old evidence
-> memory/knowledge
```

淘汰顺序：

```text
重复/低价值消息
-> 老 UI 叙事和 Tool Digest
-> 老 ToolOutput 转 ref
-> 已被 summary 覆盖的原 Turn
-> 低相关 ranked history
-> emergency tail-preserving trim
```

## 10. Compaction

### 10.1 微压缩

容量达到 soft threshold 后：

- 最近 3 个用户 Turn 工具结果完整保留；
- 更老大结果替换为 model view + ref；
- ToolCall/ToolOutput pair 结构保持闭合；
- 小而关键的数字结果不因字符短而丢失。

### 10.2 会话压缩

达到 hard threshold 后，对连续、已闭合且尚未覆盖的 revision 生成结构化摘要：

```json
{
  "from_revision": 1,
  "through_revision": 32,
  "objective": "...",
  "decisions": [],
  "completed_actions": [],
  "failed_attempts": [],
  "artifact_refs": [],
  "open_questions": [],
  "next_step": "...",
  "source_refs": []
}
```

应用后活动上下文只保留：

- summary；
- summary 之后的完整 recent Turns；
- 当前未完成 Action；
- 当前问题相关 Evidence refs。

原消息、工具结果和文件不删除，只退出模型工作集。

### 10.3 压缩失败

增加 `none/turn/sticky/until_success` suppression。摘要失败不删除原 blocks；先执行确定性
ref 替换和 ranked trim。相同 conversation + revision 同时只运行一个 compaction，
应用前核对 prefix hash。

## 11. 主题切换

不使用单一 LLM 分类器静默丢弃历史。采用确定性信号和软相关度：

- 用户显式引用“刚才、上面、那个文件、订单结果”时，保护对应 Turn/Evidence。
- 当前输入引用 artifact/file/message ID 时，强制装入。
- 当前存在未完成 Action/Goal 时，保持任务工作集。
- 无显式引用、旧任务已闭合且语义相关度低时，只保留摘要，不保留旧工具明细。
- 用户要求“最新/重新查询”时，旧 Evidence 仅作为比较基线，必须重新调用数据源。
- 产品可提示开始新对话，但不得静默切断用户可恢复的历史。

## 12. Prompt Cache

缓存时间只影响成本和首 Token 延迟，不参与语义内容的保留判断。系统必须按“缓存全部
失效”设计正确性，再利用命中结果优化费用。

稳定组装顺序：

```text
1 stable agent/system instructions
2 stable permission/persona/session memory
3 current active summary
4 stable recent closed history
5 selected evidence model views
6 turn dynamic time/location
7 current user input/resources
8 effective tool schemas（按 Provider 要求映射）
```

要求：

- 动态时间移到历史之后，避免每轮在前缀中间变化。
- 同一 Snapshot 使用稳定排序和序列化，禁止无意义重排。
- Adapter 记录 `prompt_tokens/cached_tokens/cache_creation_tokens`。
- 缓存未命中只增加成本和延迟，不改变上下文事实或结果质量。
- compaction 会建立一个新的短稳定前缀；之后继续只追加。
- 所有通道按热、温、冷三档观测缓存年龄，但使用同一 ContextPlan 算法。
- 供应商 TTL、全通道时间分档和显式缓存采用条件见实施附录。

## 13. 数据与接口设计

首期不新建完整 Agent Runtime 表，渐进扩展现有能力：

1. `conversation_data_evidence`
   - 增加/规范 `model_view`、`content_hash`、`byte_size`、`expires_at`；
   - `file_ref` 升级为稳定 Artifact ref，不直接暴露绝对路径；
   - 保留现有 unique conversation + artifact_id 和 revision 索引。
2. 结构化 ContextSummary
   - 明确 `from_revision/through_revision/status/source_refs/content_hash`；
   - 单 conversation + through_revision 只允许一个 active summary。
3. ContextReceipt
   - 首期可写现有 telemetry/独立轻量表；
   - 只存 refs/hash/token/reason，不存敏感正文。
4. Retrieval 接口
   - `evidence_search(query, filters, limit)`；
   - `evidence_get(artifact_id, selector, max_tokens)`；
   - 文件和旧 ToolOutput 后续复用同一 Search/Get 门面。

API 和前端消息协议首期不变；模型工具只在存在可访问 Evidence 时动态开放。

## 14. 连锁修改清单

| 改动点 | 影响位置 | 同步要求 |
|---|---|---|
| Evidence model view | ArtifactCollector、RuntimeState、migration、snapshot | 写入、读取、hash、大小策略一致 |
| Evidence Search/Get | tool registry、ToolExecutor、Policy、RuntimeState | 动态开放、scope、预算、审计 |
| History Provider | history_loader、cache、interrupt repair | completed 与 interrupted 行为都覆盖 |
| Summary revision | summary manager、conversation、snapshot | 原子更新覆盖边界，历史与摘要互斥 |
| Budget Planner | model registry、PromptBuilder、compressor | 按模型能力推导并保留旧开关回滚 |
| Stable assembler | PromptBuilder、Provider adapters | 动态时间后移、稳定排序 |
| ContextReceipt | adapters、usage、telemetry | 估算与 Provider 实际 usage 对账 |
| Cache schema | conversation_cache | 升版本并失效旧投影 |

## 15. 分阶段实施

### Phase 0：观测与基线

- 建立 shadow ContextReceipt，不改变模型输入。
- 记录每个 block、总 token、工具 schema token、cached token 和裁剪原因。
- 固定 20/100/500 Turn、ERP、文件、失败重试和 24 小时恢复基线。

### Phase 1：修复证据消费断层

- 为 Evidence 生成分级 model view。
- 下一 Turn 自动注入热 Evidence；新增受控 Search/Get。
- 正常 completed Turn 不全量恢复原始工具日志，只恢复近期安全 pair 或 Evidence view。
- Redis context cache 升级 schema。

### Phase 2：摘要覆盖与活动工作集

- 已修正 summary revision 原子更新。
- History Provider 已排除 active summary 覆盖的旧 Turn。
- 已建立 recent Turns + summary + refs 的有限工作集。
- 已收口跨 Turn 摘要、当前 Run 循环摘要、Memory V2 与 Evidence 的职责边界。

### Phase 3：模型能力预算与稳定缓存

- PromptBuilder 初始输入与工具循环已共用模型 capability 推导的 ContextBudget。
- Web、企微和 Headless 执行不再按通道选择 Token 预算或压缩路径。
- 动态时间已移到摘要、历史和 Evidence 之后，稳定 block 顺序已固定。
- DashScope、Google、OpenRouter 的真实 cached-token 指标已进入统一 usage。

### Phase 4：Compaction 可靠性

- 当前 Run 循环摘要已增加 task-scoped suppression、单 in-flight 和应用前 prefix fingerprint 复核。
- 跨 Turn 摘要已按 `conversation + summary_revision + through_revision` 增加 60 秒
  Redis 分布式 single-flight；主/备摘要均失败后，同 prefix 抑制 5 分钟。
- Redis 不可用时继续生成摘要，最终仍由 `apply_context_summary` 的 revision CAS
  决定是否提交；锁冲突和 suppression 直接跳过，不阻塞用户聊天。
- Evidence 已统一 Search/Get；File 与旧 ToolOutput 暂沿用现有受控工具边界，不在本轮
  伪装成同一检索接口。
- 全通道统一消费同一个 assembler；发布控制由 Agent Runtime 发布体系负责。

## 16. 边界与极限情况

| 场景 | 处理 |
|---|---|
| 无历史/无证据 | 只组装控制面和当前输入 |
| Evidence rows 为空 | 标记 empty，不制造 model view 数字 |
| Evidence 超 1 MB | 只保存 Artifact ref 和确定性统计 |
| ToolCall 无结果 | 合成 interrupted/unknown 结果保持协议闭合 |
| 并行工具部分失败 | 每个 call 独立状态，顺序按原 ordinal 固定 |
| 用户快速插话 | 创建新 plan revision，旧未发送 plan 作废 |
| Summary 超时 | 原 blocks + ref trim 降级，记录 suppression |
| Redis 不可用 | DB 回源，结果一致但延迟增加 |
| Provider cache 失效 | 重算有限工作集，不重发无限事实 |
| 模型窗口缩小 | 重新规划，不静默截断 required blocks |
| 权限撤销 | 发送前 plan 作废，Get 返回 forbidden |
| Artifact 删除 | 返回 unavailable，可重新查询时由模型决定 |
| 新问题完全无关 | 旧任务只留摘要，工具正文退出工作集 |
| 用户要求旧明细 | Search/Get 指定 revision，不恢复整段会话 |

## 17. 可观测性和验收指标

当前观测状态：

- 已有 `context_estimated_tokens`、`context_tokens_by_kind`、工具 Schema Token；
- Provider usage 已持久化 `prompt_tokens/cached_tokens/cache_creation_tokens`；
- 已有 `context_cache{outcome}`、`context_compaction{outcome}` 和
  `context_evidence_search/get{outcome}` 结构化事件；
- `context_cache_hit_ratio`、trim/compaction 总量由日志聚合层计算，不在请求进程维护；
- `context_required_overflow_total` 与 `context_requery_avoided_total` 尚无可靠确定性判定，
  不生成虚假指标，保留为后续 Runtime Contract 验收项。

上线门禁：

1. 500 Turn 对话任意下一 Turn 的 Provider 输入不超过可用窗口 85%。
2. 缓存完全失效时仍只重建活动工作集，不读取全部 messages。
3. 最近 ERP 查询下一 Turn 可直接复用；老查询可通过 ref 恢复。
4. 正常、失败、取消工具调用均无孤立 pair。
5. Summary 覆盖范围与 History 不重复装入。
6. 同 Snapshot 重试的 block refs/hash 一致。
7. 文件删除、权限撤销和数据过期均给出明确状态。
8. 普通无工具聊天输出行为不变。
9. Web 与企微对相同模型和事实生成相同 ContextPlan；渠道只影响权限、资源和输出投影。
10. ContextReceipt 估算与 Provider 实际 input token 偏差稳定低于 10%。

## 18. 实施附录

架构影响、计划文件、部署顺序和回滚边界见
`TECH_长期单会话上下文与Token治理_实施附录.md`。

## 19. 结论

本方案的核心不是让模型永久记住全部历史，而是保证：

```text
完整事实一直在
+ 当前工作集始终有限
+ 近期结果直接可用
+ 老结果按引用可恢复
+ 摘要有覆盖边界
+ 缓存失效只影响成本和延迟
+ 每次组装都可解释
```

本轮已按 Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 实施，没有把
`preserve_tool_protocol` 全局开启，也没有恢复 Web/企微双预算。
