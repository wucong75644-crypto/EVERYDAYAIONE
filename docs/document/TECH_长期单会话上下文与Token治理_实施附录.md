# 长期单会话上下文与 Token 治理实施附录

> 主文档：`TECH_长期单会话上下文与Token治理.md`
> 日期：2026-07-18
> 状态：Phase 0–4 与统一观测已完成；独立上下文灰度已取消，等待生产迁移与验收

Phase 1 当前已完成 Evidence 分级 `model_view`、固定
`conversation_id + base_revision` 的受控 Search/Get，以及正常 completed Turn 最近 3 个
用户回合的安全 tool pair 恢复。

Phase 2 已完成摘要覆盖闭环：摘要仅选择连续闭合 Turn，并通过
`expected_summary_revision` CAS 原子提交 `summary_revision +
summary_through_message_id`；ContextSnapshot 只加载
`(summary_revision, base_revision]` 的原始历史。Redis 闭合历史使用独立 v4 key，
精确绑定摘要下界、任务上界和闭合消息边界，旧 key 按 TTL 自然退出。
PromptBuilder 始终按 `active summary → recent history` 消费，即使摘要后的历史为
0～5 条也不会丢失摘要；只存在摘要时仍启用当前用户消息优先约束。

Phase 2 最终职责边界：

- `conversations.context_summary`：跨 Turn 持久工作摘要，必须带 revision 覆盖边界。
- `[工具循环摘要]`：只存在于当前 Run，只总结本次实际删除的 stale tool messages。
- Memory V2：用户级长期事实，不作为工具消息的替代文本。
- Evidence/Artifact：可复用业务事实、结构化数据和稳定原文引用。

旧请求级 Session Memory 仅在 Web 流式链路提取，企微/Headless 不消费；且内容可能
混入仍被保留的近期轮次。该模块、初始化与每轮后台 LLM 提取已删除，循环摘要不再因
通道不同而改变输入或产生额外提取调用。

Phase 3 子任务 1 已建立 `ContextBudget`：模型注册表的 `context_window` 与
`max_tokens` 是能力事实源，按已确认公式计算 output reserve、safety margin、
usable input 及 75%/85%/92% 阈值。`model_id` 从统一 Chat 执行入口透传至
PromptBuilder，初始 messages 使用 85% hard budget；Web/企微来源不再参与预算选择。
同一 `ContextBudget` 随 `PreparedChatStream` 进入流式与 Headless 工具循环：75% soft
阈值触发旧工具结果归档，85% hard 阈值用于工具桶、历史桶与循环摘要，92% emergency
阈值执行保尾总量兜底。工具循环不再读取 conversation source，也不再使用 Web/企微
固定 Token 常量。

Phase 3 子任务 3 已将 Prompt 顺序固定为静态规则、会话稳定层、活动摘要、recent
history、Evidence、动态时间/位置、当前资源与用户输入。DashScope、Google 和
OpenRouter 的实际缓存命中/创建 Token 通过统一 `StreamChunk` 进入流式及 Headless
usage，并随既有任务结果持久化；Provider 未返回字段时保持为零，不影响语义执行。

Phase 4 子任务 1 已为当前 Run 循环摘要增加 `task_id + stale prefix fingerprint`
协调：相同 prefix 只允许一个 in-flight 摘要，主模型与 fallback 都失败后本 Run 不再
重试该 prefix；摘要返回后再次核对 fingerprint，变化则丢弃摘要且不删除原消息。
Run 结束清理 scope，异常取消也释放 in-flight；状态最多保留 1024 个失败 prefix。

Phase 4 子任务 2 已在跨 Turn DB 摘要模型调用前增加 Redis 协调：

- prefix 固定为 `conversation_id + summary_revision + through_revision` 的 SHA-256，
  Redis key 不暴露原会话 ID。
- 分布式锁 TTL 为 60 秒；竞争者不等待，直接跳过本次 fire-and-forget 更新。
- 主模型和 fallback 均无结果时写入 300 秒 suppression；revision 推进后 prefix
  自动变化，不受旧失败抑制。
- Redis 获取、读取、写入或释放失败均降级，不影响用户响应；并发生成的最终提交继续
  由数据库 `apply_context_summary` CAS 拒绝旧 revision。

观测子任务已统一无正文事件：

- `gen_ai.context_receipt`：估算输入、工具 Schema、消息类型 Token 分布。
- `gen_ai.context_cache`：固定 revision 闭合历史缓存 hit/miss。
- `gen_ai.context_compaction`：当前 Run 的 unchanged/trimmed/summarized 及前后 Token。
- `gen_ai.context_evidence_search/get`：success/empty/forbidden、返回数量和截断状态。
- 所有事件只接受标量或受控字典字段，不记录消息、工具结果或 Evidence 正文。

独立上下文 rollout policy 已取消。Evidence 自动注入、Evidence Search/Get、当前
Run 与跨 Turn 的 LLM compaction 统一进入 Web、企微和 Headless 共用主链；发布灰度
统一由 Agent Runtime 发布体系负责，不在上下文内部维护第二套 org/channel/model 开关。

## 1. 架构影响与风险

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增 context runtime 门面 | 中 | Provider 接口隔离旧模块 |
| 数据流 | 从消息数组改为有预算投影 | 中 | 回归测试与生产指标验收 |
| 扩展性 | 模型输入固定上限 | 低 | Artifact 分页和 Search/Get |
| 一致性 | summary/evidence revision 需收口 | 中 | Actor 原子提交与 hash |
| 缓存 | 组装顺序调整会短暂冷启动 | 中 | 发布后建立新稳定前缀 |
| 安全 | Evidence Get 扩大读取面 | 中 | 每次 scope/Policy 校验 |
| 发布 | 上下文能力统一进入主链 | 中 | Runtime 发布门禁与监控 |

不存在必须暂停设计的架构高风险。上下文不再保留新旧投影并行分支，发布前通过回归测试
验证，发布后通过既有无正文指标观察 Token、缓存和压缩行为。

## 2. 计划文件

新增：

- `backend/services/agent/runtime/context/types.py`
- `backend/services/agent/runtime/context/planner.py`
- `backend/services/agent/runtime/context/assembler.py`
- `backend/services/agent/runtime/context/providers/history.py`
- `backend/services/agent/runtime/context/providers/evidence.py`
- `backend/services/agent/runtime/context/receipt.py`
- `backend/services/agent/runtime/context/channel_context.py`
- additive migration 与 rollback

修改：

- `backend/services/handlers/context_snapshot.py`
- `backend/services/handlers/data_context_snapshot.py`
- `backend/services/handlers/chat_context/history_loader.py`
- `backend/services/handlers/chat_context/summary_manager.py`
- `backend/services/handlers/conversation_cache.py`
- `backend/services/prompt_builder/builder.py`
- `backend/services/handlers/context_compressor/`
- `backend/services/agent/runtime/artifact_collector.py`
- `backend/services/agent/runtime/runtime_state.py`
- Provider adapters 的 usage/cache 指标传递
- `docs/PROJECT_OVERVIEW.md`
- `docs/FUNCTION_INDEX.md`
- `docs/CURRENT_ISSUES.md`

## 3. 部署与回滚

- 所有数据库修改 additive，先迁移后部署；提供独立 rollback SQL。
- 上下文能力随共用 Chat 主链直接发布，不维护独立 org/channel/model 灰度。
- Evidence 新字段写入失败不得破坏既有 Actor 终态。
- 数据库迁移回滚与 Agent Runtime 发布门禁仍独立有效。

## 4. 模型缓存时间与全通道策略

### 4.1 供应商公开语义

| Provider | 缓存类型 | 时间语义 | 架构假设 |
|---|---|---|---|
| DashScope/千问 | 显式 | 5 分钟；命中后重新计时 | 只用于连续活跃会话 |
| DashScope/千问 | 隐式 | 无固定有效期 | 随时可能 miss |
| Gemini | 隐式内存 | 最长约 24 小时，仍不保证命中 | 24 小时内也按可 miss 设计 |
| Gemini | 显式 | 默认 1 小时，可配置 TTL | 只缓存高复用稳定大前缀 |
| Grok | 自动前缀 | 无承诺 TTL，可能随时驱逐 | 只作为参考 |

供应商行为必须通过实际响应的 `cached_tokens` 验证，不能只根据配置判断命中。

### 4.2 全通道时间分档

| 分档 | 距上次模型调用 | 组装逻辑 | 缓存预期 |
|---|---:|---|---|
| Hot | `<= 5 分钟` | 稳定前缀后仅追加本轮内容 | 优先争取完整前缀命中 |
| Warm | `5 分钟～24 小时` | 同一有限活动工作集 | 隐式缓存机会性命中 |
| Cold | `> 24 小时` | 从 DB Snapshot 重建有限工作集 | 默认完整 miss，建立新前缀 |

时间分档不得改变 required/protected 内容。Warm/Cold 不能为了省 Token 删除用户明确引用
的证据；Hot 也不能为了保缓存而延迟必要 compaction。

### 4.3 显式缓存采用条件

不为每个会话默认维护 24 小时显式缓存。只有同时满足以下条件才评估创建：

- 稳定前缀达到 Provider 最低缓存 Token；
- 预计 TTL 内至少复用两次；
- 缓存读取折扣大于创建和存储成本；
- 前缀不包含短期授权、动态时间或易变资源；
- Provider 支持按租户隔离和显式删除。

普通对话默认依赖稳定前缀和隐式缓存；大企业知识、长文件重复分析等场景才适合
显式缓存。

### 4.4 ContextReceipt 缓存字段

```json
{
  "provider": "dashscope",
  "model": "qwen3.5-plus",
  "last_model_call_at": "2026-07-18T10:00:00+08:00",
  "cache_age_seconds": 240,
  "cache_state": "hot",
  "prefix_hash": "sha256:...",
  "prompt_tokens": 18000,
  "cached_tokens": 14000,
  "cache_creation_tokens": 0,
  "cache_hit_ratio": 0.7778
}
```

验收必须包含强制 cache miss：删除/更换 cache key 或等待过期后，语义结果保持一致，
输入仍受活动工作集硬预算限制。

## 5. 全通道唯一 Context Runtime

### 5.1 唯一调用链

```text
Web / 企业微信 / API / 定时任务
  -> ChannelInputNormalizer
  -> Conversation Actor / execute_chat
  -> ContextRuntime.build(ContextBuildRequest)
  -> 同一 ContextPlan / Providers / Compactor / Assembler
  -> Provider Request
  -> ChannelOutputProjection
```

`ContextBuildRequest` 只接收标准字段：

```text
conversation_id / task_id / turn_id / base_revision
model_capability / effective_toolset / policy_scope
current_input / resource_manifest / channel_context
```

任何入口不得直接调用 `history_loader + compressor + PromptBuilder` 拼装自己的模型数组。

### 5.2 渠道可以决定什么

- 用户、组织、群聊和资源作用域；
- 是否允许个人 Memory/persona；
- 当前附件和 ResourceManifest；
- 输入格式规范化；
- 输出分段、Markdown、卡片、文件和媒体投影；
- 渠道选择的模型；选定后预算完全由该模型能力推导。

### 5.3 渠道不能决定什么

- 历史保留 Turn 数；
- tool/history token budget；
- compaction 触发比例；
- Summary 覆盖算法；
- Evidence model view 大小分级；
- Search/Get 预算；
- ToolCall/ToolOutput 配对修复；
- Prompt stable sort 和 ContextReceipt。

`conv_source == "wecom"`、Web 200K/企微 32K 等旧预算分支不得继续作为目标架构的
业务规则或兼容开关。

### 5.4 跨通道一致性验收

对相同 `Snapshot + model + effective toolset + policy scope + current input`：

- Web 与企微生成相同 ContextPlan、block refs、hash 和 token budget；
- 差异只允许出现在 `ChannelContext` 权限/资源和最终输出 Projection；
- 20/100/500 Turn、文件、ERP、失败重试、24 小时恢复用同一组 golden fixtures；
- 任一入口新增时只实现 Normalizer/Projection，不复制 Context Runtime。
