# AGENT 17 附录：全项目差距矩阵与优先级

> 状态：第一轮汇总完成
> 日期：2026-07-18
> 评分说明：`P0` 是统一运行时成立的前提；`P1` 是大厂 Agent 的核心体验；`P2` 是规模化和生态增强
> 证据范围：Grok Build 开源源码与 EVERYDAYAIONE 当前工作区

## 1. 总体判断

EVERYDAYAIONE 的优势集中在多租户 SaaS、积分、媒体、ERP、文件、企微和数据库 Actor；
Grok 的优势集中在统一 Session Runtime、ToolBridge、Skill、Goal、Subagent、MCP、
上下文工程、事件协议和端到端开发者体验。

推荐策略不是复制 Grok 代码，而是：

```text
保留我们的 SaaS 业务执行底座
  + 引入 Grok 式统一 Session/Agent Runtime
  + 用统一 Action/Event 协议连接现有专业执行器
```

## 2. 17 层总矩阵

| 层 | Grok Build | EVERYDAYAIONE | 关键差距 | 优先级 |
|---:|---|---|---|---|
| 01 装配 | CLI/ACP/leader 共享装配 | API、Actor、企微、媒体 Worker 分进程 | 缺 Runtime composition root 和 capability snapshot | P0 |
| 02 Session | Actor 持有 Turn/取消/Goal/Skill/MCP | DB Actor 擅长持久执行 | 缺 Session command 和统一活动状态 | P0 |
| 03 Agent | AgentDefinition + ToolBridge | ChatHandler/AgentLoop/ERPAgent 多形态 | 缺定义、实例和有效能力三层 | P0 |
| 04 Model Loop | 同一循环处理 Tool/compaction/interjection | `execute_chat` 已统一 Web/企微 | 缺 ModelStep 持久记录和统一停止原因 | P0 |
| 05 Policy | plan/hook/permission 多 gate | PermissionMode、积分、工具确认分散 | 缺 AuthorizationGrant 和统一决策记录 | P0 |
| 06 ToolBridge | typed parse/dispatch/result | Tool registry + ToolExecutor mixins | Catalog、筛选、执行、展示仍耦合 | P0 |
| 07 Executor | 内置/MCP/后台统一外壳 | 媒体、ERP、文件专业能力更丰富 | 缺 Action/Attempt/Unknown 统一生命周期 | P0 |
| 08 Goal | Planner/Verifier/Strategist/Continuation | 无通用 Goal Runtime | 长目标不能自主持续和可靠恢复 | P1 |
| 09 Context | assembler/compaction/memory/receipt | ContextSnapshot、压缩、Memory 较强 | 缺 ContextPlan、预算 receipt 和信息分层统一 | P0 |
| 10 Skills | project/user/plugin Skill runtime | 无产品级 Skill Runtime | 不能按说明主动分步工作和 checkpoint | P1 |
| 11 MCP/Plugin/Hook | 动态发现、认证、Hook、插件 | 尚无统一扩展运行时 | 工具生态无法安全接入 | P1 |
| 12 Subagent | spawn/wait/kill/resume/background | ERP 内嵌 Agent，非通用 SubRun | 缺父子上下文、能力子集和结果回流 | P1 |
| 13 Persistence | Session/Goal/Plan/Chunk/Checkpoint | Actor DB、Outbox、媒体 task 强 | 多状态机无统一事件/恢复视图 | P0 |
| 14 Protocol/UI | ACP typed updates + replay | WS + ContentPart + pending restore | 缺 RuntimeEvent sequence 和 Interaction | P0 |
| 15 Obs/Config | typed telemetry + layered config | 多套日志/配置/指标 | 缺统一 correlation、usage ledger、snapshot | P0 |
| 16 Test/Ops | 状态不变量和 Trace fixture 强 | 测试数量多，发布门禁弱 | 缺全链 Trace、真实依赖 CI、canary | P0 |
| 17 E2E | 所有能力进入 Session loop | 主链强、专业链旁路 | 缺一个统一运行时事实模型 | P0 |

## 3. 业务能力矩阵

| 场景 | 当前可用性 | 当前断点 | 目标 |
|---|---|---|---|
| 普通聊天 | 成熟 | 无 Run receipt | 最小 Run |
| 单工具调用 | 可用 | Tool step 非持久 Action | Action lifecycle |
| 多工具调用 | 可分批并行/串行 | 无资源锁、部分成功恢复 | conflict-aware scheduler |
| 写提示词 | 可返回文本 | 无结构化意图区分记录 | intent + no-action receipt |
| 多提示词生图 | 模型可多次调用 | 多 task 无 batch/parent Action | N child Actions |
| 长时视频 | 可异步完成 | 独立媒体状态机 | Accepted/Unknown/reconcile |
| 图表/Mermaid | 结构化 ContentPart | 只是展示产物 | Artifact + projection |
| 文件分析 | file_id/Manifest/Parquet 较强 | 工具步骤跨崩溃不可恢复 | durable Action + Artifact |
| ERP 查询 | ERPAgent 能规划 | 内部步骤对父 Run 不透明 | professional SubRun |
| Skill | 未形成运行时 | 无发现/加载/步骤状态 | SkillRun |
| Goal | 未形成运行时 | 无持续控制器 | Goal Orchestrator |
| Subagent | 仅业务专用内嵌 Agent | 无通用父子协议 | SubRun |
| MCP | 未形成运行时 | 无 Gateway/动态 Catalog | isolated MCP gateway |
| Plugin/Hook | 未形成运行时 | 无 trust/lifecycle | plugin registry + hooks |
| Web 恢复 | Actor/pending task 可恢复 | 无统一 event cursor | snapshot + replay |
| 企微恢复 | Actor + Outbox 较强 | 通道投影独立 | same Run, channel adapter |

## 4. P0：先消除运行时断层

### 4.1 必须先定义的协议

1. `SessionCommand`
2. `RunRecord`
3. `ModelStepRecord`
4. `ActionRequest / ActionRecord / ActionAttempt`
5. `ActionResult`
6. `ArtifactEnvelope`
7. `RuntimeEvent`
8. `EffectiveConfigSnapshot`
9. `AuthorizationGrant / PolicyDecision`
10. `ContextReceipt`

这些协议先以 v1 additive schema 接入现有 Actor；不先实现 Goal、Skill、MCP。

### 4.2 首批接入能力

按风险从低到高：

```text
纯文本 Chat
  → 只读工具/图表/文件读取
  → 沙盒与 ERP 查询
  → 图片/视频生成
  → 文件写入和定时任务
  → 外部消息/部署/删除
```

每迁移一类，旧 ToolExecutor 仍作为专业 Executor，被 Action Dispatcher 调用。

### 4.3 P0 完成标准

- Web 与企微的同一次生成都产生 Run。
- 每个 Tool Call 都有持久 Action 和稳定幂等键。
- 媒体 task 能映射到 ActionAttempt，而非旁路孤岛。
- UI 能通过 Run Snapshot + sequence replay 恢复。
- 积分预留/确认/退款都关联 Action。
- 所有日志、Trace、Usage、Artifact 都可由 run_id/action_id 关联。
- 测试失败阻断发布，关键 Trace 可确定性重放。

## 5. P1：形成大厂 Agent 体验

### 5.1 Skill Runtime

- Skill Catalog、来源优先级和版本 hash。
- instruction/workflow 两种类型。
- requested tools 与 Policy 权限分离。
- SkillRun step/checkpoint。
- 用户显式调用和模型主动选择。

### 5.2 Goal Orchestrator

- create/pause/resume/complete/block。
- Planner 产物持久化。
- Completion Verifier。
- Stall Strategist。
- 单一 Continuation Controller。
- token/time/cost budget。

### 5.3 Subagent

- 父子 Run。
- 独立 ContextPlan。
- capability subset。
- 并发额度。
- wait/cancel/resume。
- structured SubRunResult。

### 5.4 MCP/Plugin/Hook

- 多租户隔离 Gateway。
- server connection state。
- Catalog delta。
- OAuth/secret reference。
- Plugin trust 与业务授权分离。
- Hook 只能收紧 Policy。

## 6. P2：生态和规模化

- Plugin marketplace/组织私有插件。
- Skill/Tool/Model 自动评测和推荐。
- 多 Agent 协作图。
- 跨 Session Goal。
- 分布式 Action scheduler。
- 跨区域 Artifact/事件复制。
- 管理员 Runtime 控制台和审计查询。
- 自动故障对账和补偿工作台。

## 7. 必须保留的本项目优势

以下能力不应为追求“像 Grok”而退化：

1. PostgreSQL Actor、lease 和 fencing。
2. Web/企微共用固定 ContextSnapshot。
3. 租户派生 ExecutionScope。
4. 积分预留、确认与退款。
5. Provider webhook + polling reconciliation。
6. OSS 持久化和缩略图/原图语义。
7. ResourceManifest、file_id 和 Parquet 数据链。
8. ERP 专业执行能力和数据准确性 Policy。
9. 事务 Outbox 与企微投递 checkpoint。
10. ContentPart 的图表、图片、视频、文件展示能力。

Grok 是本地/开发者 Agent 的优秀参考；我们的最终目标是多租户业务 Agent，因此数据库
事实、成本、安全和多通道能力应比 Grok 开源实现更强。

## 8. 禁止的重构方式

- 一次重写 Chat、媒体、ERP、企微全部链路。
- 新建一个包含所有工具逻辑的万能 Runtime 类。
- 先做 UI 动画，再补持久状态。
- 用模型输出文本扫描决定是否执行。
- 把每一步都做成独立微服务。
- 用消息表 JSON 代替 Run/Action 状态。
- 让新旧链路同时扣费或同时发送外部消息。
- 未建立 shadow comparison 就直接全量切流。
- 为了统一而删除专业 Executor 的内部状态。

## 9. 推荐总体迁移波次

| 波次 | 范围 | 生产行为 |
|---:|---|---|
| 0 | 协议、表、事件、测试基线 | 只写 shadow records |
| 1 | Chat Run + ModelStep | 旧执行，新增记录 |
| 2 | 只读工具 Action | 新旧结果 shadow compare |
| 3 | 沙盒/文件/ERP Action | 小组织 canary |
| 4 | 图片/视频 Action | 双关联 task/action，旧完成器兼容 |
| 5 | RuntimeEvent UI/企微投影 | 新旧 WS 并行校验后切换 |
| 6 | Skill Runtime | opt-in |
| 7 | Goal + Subagent | 小额度、显式开启 |
| 8 | MCP/Plugin/Hook | 组织管理员开启 |
| 9 | 收口旧状态机 | 观察窗口后 contract |

## 10. 下一阶段需要冻结的设计

第一轮调研已经足以进入总体设计，但编码前还必须完成：

1. 目标模块图和依赖方向。
2. Run/Action/Interaction/Goal 状态机。
3. PostgreSQL 表、索引、RPC 和迁移兼容方案。
4. SessionCommand、RuntimeEvent、Artifact API schema。
5. Executor SPI 与 Tool/MCP/Provider adapter。
6. ContextPlan 和 EffectiveConfigSnapshot。
7. Web/企微 Projection 与恢复协议。
8. Policy/成本/幂等/Unknown reconciliation。
9. 测试、灰度、回滚和 Release Evidence。
10. 分波次文件级任务清单。

在上述设计经方案评审确认前，不进入重大重构编码。
