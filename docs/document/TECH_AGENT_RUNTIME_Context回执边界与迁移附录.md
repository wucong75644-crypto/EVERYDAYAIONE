# Agent Runtime Context 回执、边界与迁移附录

> 主文档：`TECH_AGENT_RUNTIME_Context分层额度与召回.md`
> 日期：2026-07-18

## 1. ContextReceipt

每次 Provider 请求前持久化：

```json
{
  "receipt_id": "uuid",
  "model_step_id": "uuid",
  "plan_id": "uuid",
  "snapshot_revision": 42,
  "blocks": [
    {"block_id": "uuid", "kind": "history_turn", "tokens": 850, "hash": "..."}
  ],
  "retrieval_refs": [],
  "summary_id": "uuid",
  "tool_schema_tokens": 9200,
  "fixed_tokens": 11000,
  "dynamic_tokens": 42000,
  "estimated_total": 62200,
  "provider_reported_input": 62870,
  "trimmed": [],
  "compaction": {"triggered": false, "suppression": "none"}
}
```

Receipt 不保存敏感正文，只保存 refs、hash、token、revision 和原因。Provider 返回 usage 后补充实际值；估算偏差超过 10% 告警并校准 tokenizer 版本。

关键指标：

- `context_tokens{block_kind,model,channel}`
- `context_trim_total{reason,block_kind}`
- `context_compaction_total{outcome,suppression}`
- `context_retrieval_total{source,outcome}`
- `context_estimation_error_ratio`
- `context_required_overflow_total`
- `context_cache_prefix_hit_ratio`

## 2. 边界与极限情况

| 场景 | 处理策略 |
|---|---|
| 无历史 | L1 + 当前输入即可，不制造空摘要 |
| Snapshot revision 缺失 | fail closed，不读取“最新”缓存替代 |
| ToolCall 无结果 | 保留 interrupted/unknown Action block |
| ToolOutput 先到 | 等 Callback Inbox 对账后才进入有效工作集 |
| 文件巨大 | 先建索引，按页/区块 Get，不全量注入 |
| 多文件 | ResourceManifest 先筛，按当前步骤逐个检索 |
| Memory 超时 | 跳过本步并记录 receipt，不持久化“无记忆” |
| 用户快速插话 | 新 snapshot/plan revision；旧 ModelStep 按策略取消 |
| 模型窗口缩小 | 重算 Plan，sticky suppression 清除 |
| 工具 schema 过大 | 缩减 EffectiveToolset，不牺牲当前输入 |
| 摘要模型失败 | 原工作集、确定性 refs、相关度裁剪依次降级 |
| Artifact 删除 | Get 返回 typed unavailable |
| 权限撤销 | 已装入但未发送的 Plan 作废；重新组装 |
| 子 Agent 爆量返回 | 父级只接摘要和 refs |

## 3. 方案对比

| 维度 | A：继续消息数组压缩 | B：复制 Grok Session Context | C：Snapshot + ContextPlan |
|---|---|---|---|
| 实现量 | 低 | 中 | 中 |
| 事实可恢复 | 弱 | 本地产品较强 | SaaS/多 Worker 强 |
| 信息路由 | 隐式 | 较清晰 | 显式 block/ref |
| 多租户权限 | 分散 | 需补 | 原生纳入 |
| 可观测性 | 弱 | 中 | ContextReceipt 完整 |
| Skill/MCP/Subagent | 易膨胀 | 可用 | 可隔离预算 |
| 迁移风险 | 短期低、长期高 | 高 | 可 shadow 渐进 |

推荐 C。不是重写 ContextSnapshot，而是在其后增加统一 Plan、Provider、Assembler 和 Receipt；初期 shadow 记录，确保组装内容与旧链一致后再切换。

## 4. 架构影响

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增 context runtime | 中 | Provider 接口隔离现有模块 |
| 数据流 | 从可变数组改为 block projection | 中 | 先 shadow receipt |
| 扩展性 | 大内容外置、子 Agent 不复制上下文 | 低 | 索引分页与预算上限 |
| 耦合 | Prompt/Memory/Artifact/Tools 均接入 | 中 | ContextBlock 作为唯一契约 |
| 一致性 | 与 Snapshot/Actor 一致 | 低 | 固定 revision/hash |
| 可观测性 | 新增 block/token/trim 指标 | 低 | receipt 与 usage 对账 |
| 可回滚 | 旧 assembler 可并存 | 低 | feature flag 按 channel/model |

不存在需要暂停设计的未决高风险；风险主要在迁移一致性，应通过 shadow diff 和逐通道灰度控制。

## 5. 计划文件与接口

本轮不修改代码。实施阶段预计：

| 路径 | 职责 |
|---|---|
| `backend/services/agent_runtime/context/types.py` | Plan、Block、Receipt 类型 |
| `backend/services/agent_runtime/context/planner.py` | 预算与 provider 计划 |
| `backend/services/agent_runtime/context/assembler.py` | 稳定组装与 Provider 映射 |
| `backend/services/agent_runtime/context/providers/` | History、Memory、Knowledge、Artifact、Skill |
| `backend/services/agent_runtime/context/compactor.py` | 统一压缩与抑制 |
| `backend/services/agent_runtime/context/retrieval.py` | Search/Get 门面 |
| `context_snapshot.py` | 适配为 Snapshot Provider |
| `context_compressor/` | 算法迁移，旧入口兼容 |
| `PromptBuilder` | 指令 Provider，逐步取消总装所有权 |
| 数据库迁移 | summary、plan/receipt、suppression 元数据 |

接口：

- `ContextPlanner.plan(run, snapshot, model) -> ContextPlan`
- `ContextProvider.provide(plan) -> list[ContextBlock]`
- `RetrievalBroker.search/get(...)`
- `ContextCompactor.compact(plan, blocks) -> CompactionResult`
- `ContextAssembler.assemble(plan, blocks) -> ProviderRequest`
- `ContextRecorder.persist_receipt(...)`

## 6. 迁移与验收

1. 先定义 ContextBlock/Receipt，对旧消息组装做 shadow 记录。
2. 用模型 capability 替代通道固定总预算，旧配置保留为上限开关。
3. ToolOutput 先引入稳定 ref，再停止 `[已归档]` 不可恢复路径。
4. Conversation summary 升级为结构化 revision summary。
5. 统一 compressor 并增加 suppression/in-flight/fingerprint。
6. 接入 Memory/Knowledge/Artifact Search/Get。
7. Skill/MCP/Subagent 只通过独立 ContextPlan 接入。
8. 按 Web 小流量、企微、长 Goal、子 Agent 顺序灰度。

验收门禁：

- 同 Snapshot 重试 block refs/hash 一致。
- 不出现孤立 ToolCall/Output。
- 任何大结果被裁剪后均可按 ref 恢复。
- required blocks 永不被静默截断。
- 群聊没有个人 Memory 泄漏。
- 摘要失败不丢原始事实、不重复风暴。
- 200K 会话的子 Agent 不复制完整上下文。
- ContextReceipt 可解释 Provider 实际输入 token，误差稳定低于 10%。
- Web/企微相同 Run 类型使用相同预算算法，仅 Projection 不同。

## 7. 下一层

Context 层协议、路由、预算和参数已经冻结，不进入实现。下一轮衔接 Executor SPI：

- Executor 输入只接 Action 与 Capability，不直接读取全会话；
- 同步读取、本地渲染、异步生成、外部回调、文件和 MCP 的统一接口；
- ActionResult 如何生成 model/display/audit/artifact 四种视图；
- 进度、取消、超时、重试、幂等和对账如何由专业 Executor 声明。
