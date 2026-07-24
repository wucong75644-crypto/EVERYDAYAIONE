# AGENT 12：Subagents / Background 子代理与后台任务

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：委派、上下文、能力、权限、并发、后台执行、取消和恢复
> 后续专项：Persistence、Protocol/UI、Observability、Testing 继续核验

## 1. 结论摘要

子 Agent 的行业定位是：

```text
Subagent = 独立上下文中的受限 Agent Run
         + 明确输入合同
         + 独立工具与预算
         + 结构化结果回传
```

它解决三类问题：

- 上下文隔离：研究或审查不挤占主 Agent 工作集。
- 并行：多个真正独立的子问题同时执行。
- 专业化：使用只读研究、计划、评审等不同 AgentDefinition。

它不应承担：

- 顺序依赖工作流的状态机。
- Provider 异步任务等待。
- 核心权限、积分和幂等治理。
- 为了“显得智能”而拆分简单任务。

推荐总体关系：

```text
Goal / SkillRun
  ├─ Parent Agent Run
  ├─ SubRun A（独立研究）
  ├─ SubRun B（独立验证）
  └─ Background Action（媒体 / MCP / 外部任务）
```

Grok Build 对子 Agent 的上下文、Agent 类型、能力模式、后台化、恢复、worktree 和 UI
已经形成完整链路；但主要状态仍由进程内 Coordinator 和子 Session 承载。项目现有
Conversation Actor、数据库 claim/lease/fencing 和媒体异步任务更适合 SaaS 持久执行，
应在此基础上新增 `SubRun`，不照搬本地隐藏 Session。

## 2. Grok 子 Agent 模型

### 2.1 独立 Session

主 Agent 调用 `spawn_subagent`，子 Agent 获得：

- 独立 context window。
- 独立 child session 和 transcript。
- 指定 Agent type 的 system prompt 和 toolset。
- 可选 model、reasoning effort、capability mode、cwd 或 worktree。
- 完成后向父级回传摘要。

父上下文不需要承载子 Agent 的完整推理和所有 ToolOutput，只接收生命周期、状态和
最终结果。用户仍可以打开子 Session 查看详细 transcript。

### 2.2 Agent 与 Persona

Agent 定义完整 Session：

- model。
- tools。
- prompt mode/body。
- skills。

Persona 是只用于子 Agent 的行为叠加：

- instructions。
- input/output contract。
- model/effort 默认值。
- isolation 默认值。

Persona 不改变基础 Agent 工具和权限。模型/effort 解析优先级：

```text
显式 spawn override
> Role 默认
> Persona 默认
> Parent Session
```

Isolation 不继承 Parent，缺省为 `none`。

### 2.3 内置类型

| 类型 | 用途 | 能力 |
|---|---|---|
| `general-purpose` | 通用任务 | 完整工具集 |
| `explore` | 代码调查 | 搜索、读取、shell，不编辑 |
| `plan` | 形成实施方案 | 搜索、读取、shell，不编辑 |

项目和用户定义可按名字覆盖内置类型。对 SaaS 来说，租户 AgentDefinition 不应覆盖
平台保留类型而不保留限定名和审核来源。

## 3. Spawn 协议

Grok 参数：

| 参数 | 语义 |
|---|---|
| `prompt` | 完整子任务 |
| `description` | 3～5 词 UI 标签 |
| `subagent_type` | Agent 类型，默认 general-purpose |
| `background` | 是否立即返回 child ID |
| `capability_mode` | read-only/read-write/execute/all |
| `isolation` | none/worktree |
| `resume_from` | 从已完成 peer 恢复 |
| `cwd` | 子 Agent 工作目录 |

`cwd` 与 worktree 互斥；resume 时忽略新 cwd，继承来源。请求的 Agent/Persona 无法
解析、指令文件不可读或类型不允许时，spawn 失败，不静默退回 general-purpose。

### 3.1 Capability Mode

| 模式 | Read | Write | Execute |
|---|---:|---:|---:|
| read-only | 是 | 否 | 否 |
| read-write | 是 | 是 | 否 |
| execute | 是 | 否 | 是 |
| all | 是 | 是 | 是 |

这是粗粒度工具过滤，不等于 ToolCall 授权。子 Agent 的能力最终仍必须受父权限、
AgentDefinition、Policy 和环境范围约束。

### 3.2 深度

Grok `MAX_SUBAGENT_DEPTH=1`。只有顶层 Session 可以 spawn；子 Agent 的 task tool 被
移除，不能递归生成孙 Agent。

扁平树是合理默认：可避免指数级并发、权限链难以解释、成本失控和取消传播复杂化。

## 4. Context 继承

Grok 有三种初始上下文：

- `New`：全新 Session，只带显式任务。
- `Forked`：父历史作为 `<background_context>`。
- `Resumed`：继承已完成 peer 的 transcript、tool state 和 model。

Resume 的限制：

- 来源必须 completed。
- 必须属于当前 Parent Session。
- Agent type 必须相同。
- system prompt 和 tools 按当前 AgentDefinition 重新渲染。

这避免把旧工具 schema 和旧系统提示盲目复制到新 Run，但也意味着恢复必须记录：
“事实 transcript”与“当前控制面”是两类内容。

### 4.1 继承的运行资源

Grok 子 Session 可共享或继承：

- filesystem、terminal、environment。
- hunk tracker。
- parent MCP pool 和 tool definition snapshot。
- Skills（AgentDefinition 允许时）。
- memory config。
- hooks 和 permission handle。
- scheduler/background task backend。
- traceparent 和 token usage attribution。

共享资源提高体验，也放大隔离风险。`capability_mode=read-only` 不能只隐藏 Edit 工具，
还要确保共享 terminal、MCP、Skill 和后台接口没有旁路写入。

### 4.2 Memory 限制

子 Agent 专属 memory 读取最多 200 行、25KB。它说明长期记忆也必须预算化，不能因为
子上下文独立就无限注入。

## 5. 前台与后台

### 5.1 前台等待

`background=false` 时父 ToolCall 等待 child 完成。默认 await budget 为 600 秒，可用
`GROK_SUBAGENT_AWAIT_BUDGET_MS` 覆盖。

出现以下情况会非破坏性转后台：

- 超过 await budget。
- 父 ToolCall/result receiver 被丢弃。

Child 不因父轮次结束自动取消；Coordinator 标记 backgrounded，结果稍后通知 Parent。
这比让 Parent 的一次模型请求阻塞无限时间可靠。

### 5.2 后台命令

Grok 每 Session 的 background task registry 默认最多 10 个。输出：

- 内存保留可能截断的 preview。
- 完整内容写入 Session 文件。

Session 退出时把仍运行任务写入 `background_tasks_manifest.json`；恢复时提示这些任务
当时仍在运行。这个 Manifest 主要是事实提醒，不等于重新接管任意已丢失 OS 进程。

### 5.3 后台归属转移

子 Agent 完成后仍存活的 monitor、background command、scheduled task 会重新绑定
Parent 的 terminal backend 和 notification handle，使事件继续回到父 Session。

本项目不应依赖内存 reparent。后台工作必须从创建时就绑定稳定
`org_id/user_id/parent_run_id/goal_id`，Worker 只是执行者。

## 6. 取消与终态

Grok 支持按 subagent ID 或 parent prompt ID 取消，CancellationToken 传播到 child。
状态区分 completed、failed、cancelled、max-turns、backgrounded。

查询可选择 block，默认等待上限 30 秒并每 200ms 轮询。取消请求返回的是：

- live child 已取消，后续仍会产生真实 finished event。
- 已完成。
- 未找到。

因此“发出 cancel”不是终态提交。父级只能在子状态真正落为 cancelled/terminal 后完成
聚合。

父 Turn 被取消时需要同时处理：

- 正在等待的 foreground child。
- 已后台化 child。
- child 创建的后台任务。
- worktree 或临时资源。
- 尚未折算到父账单的 token usage。

## 7. Worktree 隔离

`isolation=worktree` 为修改任务创建独立 Git worktree。Child result 带 worktree path，
通过扩展操作把变更应用回 Parent。

适用：

- 多个编码 Agent 并行编辑。
- 审查未知变更。
- 需要放弃整组修改。

不适用：

- SaaS 用户普通聊天。
- OSS/数据库/ERP 等非 Git Artifact。
- 需要实时共享同一 Workspace 文件的流程。

本项目应抽象为 `WorkspaceIsolation`，Git worktree 只是代码场景的一种实现；文件、
媒体、报表可使用独立 staging revision。

## 8. EVERYDAYAIONE 现状

### 8.1 已有执行基础

- Conversation Actor：数据库事实队列。
- Conversation Worker：默认并发 5、扫描批次 100、停机等待 10 秒。
- serial/branch claim：同会话顺序与固定快照分支。
- lease + fencing：Worker 丢权后不能提交终态。
- ExecutionBudget/StopPolicy：单 Run 轮次和失败治理。
- BackgroundTaskWorker：媒体 Provider 轮询与超时回收。
- ScheduledTaskAgent：默认 180 秒、12 轮、单工具 30 秒。
- 媒体 tasks：异步状态、回调/轮询、积分和 Artifact。

这些已经覆盖“持久后台 Action”，但没有通用 Subagent：

- 没有 spawn/list/get/cancel SubRun 协议。
- 没有父子 Run、输入/输出合同和结果聚合。
- 没有 Agent type/capability/isolation 解析。
- 没有独立子上下文或 child transcript。
- 没有子预算折算到父 Goal/用户成本。

### 8.2 当前命名问题

`BackgroundTaskWorker` 实际是图片/视频任务轮询器和若干定时扫描的聚合 Worker，不是
通用后台任务运行时。`ScheduledTaskAgent` 是定时触发的 headless Agent，也不是
Parent Agent spawn 的 Subagent。

后续重构必须保留业务行为，但在架构文档和新类型中避免把三者混为一谈。

## 9. 目标模型

```text
Parent Run
  ↓ delegate
SubRunRequest
  ↓ policy + budget reservation
SubRun
  ↓ worker claim/lease/fencing
Child AgentInstance
  ↓ ToolBridge / Executors
SubRunResult
  ↓ result ref + evidence + usage
Parent Run / Goal continuation
```

### 9.1 SubRunRequest

```text
parent_run_id / parent_goal_id / parent_step_id
agent_definition_id / capability_mode
objective / input_refs[] / expected_outputs[]
context_mode: fresh | selected | fork | resume
execution_mode: foreground | background
workspace_isolation
budget
idempotency_key
```

`prompt` 不能是唯一合同。Objective、输入引用、预期输出、权限和预算必须结构化。

### 9.2 SubRun

```text
subrun_id / child_conversation_id
status: queued | running | waiting | completed | failed | cancelled | unknown
agent/model/skill/catalog revisions
effective_capabilities
lease_owner / lease_token / lease_expires_at
usage / artifacts / evidence / result_summary
started_at / completed_at / cancel_requested_at
```

Child 完成提交与 usage、Artifact refs、父 Goal wake event 应在同一事务或 Outbox
边界内，避免“已扣费但 Parent 永远不知道完成”。

## 10. 委派策略

满足以下条件才 spawn：

1. 子任务能用清晰输入/输出合同表达。
2. 与 Parent 当前工作可独立进行。
3. 独立上下文或专业 Agent 带来的收益大于启动成本。
4. 预算、权限和 Workspace 冲突可控。

不 spawn：

- 单次工具调用。
- 必须连续追问用户的工作。
- 强顺序依赖且 Parent 下一步只能等待。
- 付费媒体 Provider 任务；它应是 Background Action。
- 只是为了把普通计划每一步包装成 Agent。

Planner 可以提出 delegation candidates，确定性 `DelegationPolicy` 决定是否允许以及
并发上限，模型不能无限 spawn。

## 11. 权限、预算与并发

```text
ChildCapabilities
= ParentDelegableCapabilities
∩ AgentDefinition
∩ RequestedCapabilityMode
∩ Tenant/Channel Policy
```

父级不能委派自己没有的权限。父级的一次用户授权只有在 grant 明确
`delegable=true` 且限定 action/scope 时才能传给 Child；否则 Child 高风险 ToolCall
需要新的 Policy 判断。

建议初始保护：

| 参数 | 初值 |
|---|---:|
| 最大深度 | 1 |
| 每 Parent 同时活跃 SubRun | 3 |
| 每用户同时活跃 SubRun | 5 |
| 每组织并发 | 套餐/资源池配置 |
| foreground 等待 | 30 秒后转 background |
| Child 默认模式 | read-only |
| Parent Context 回传摘要 | 2K～4K 字符 + refs |

Grok 的 600 秒适合本地 CLI；Web 对话不应等待 10 分钟。UI 应立即显示任务卡，Parent
可以继续响应，Goal 在 Child 完成事件后续跑。

Token/积分采用 Parent 预留 + Child 实际结算。所有 Child usage 汇总到 Goal，但保留
subrun 维度用于追踪。

## 12. Context 与结果回传

默认 `selected`，只给 Child：

- Objective 和 output contract。
- 相关 ContextSummary/Goal gap。
- 必需消息、文件、Artifact 和 ToolOutput refs。
- EffectiveCapabilities 与 Policy constraints。

不复制：

- 全部聊天历史。
- 无关 Memory/persona。
- Parent 隐藏推理。
- 未授权资产。
- 所有 MCP/Skill 目录。

结果回 Parent：

```text
status
summary
findings/decisions
artifacts[]
evidence[]
open_questions
usage
child_transcript_ref
```

Parent 不能只相信自然语言 summary。代码任务附 diff/test；查询附来源；媒体附
Artifact；失败附稳定 error class。

## 13. Background Action 与 SubRun 分界

| 场景 | 正确抽象 |
|---|---|
| 调用图片 Provider 等待回调 | Background Action |
| 独立调查三个技术方案 | 并行 SubRun |
| 根据三段提示词生成三图 | 一个 Workflow SkillRun + 三个 Action |
| 研究后实现再审查 | Goal steps，可串联多个 SubRun |
| 定时查 ERP 并发报告 | Scheduled Goal/Run |
| shell 长时间测试 | Background ToolRun，必要时由 Child 发起 |

SubRun 内部可以创建 Background Action；Child 结束不应自动取消已经被 Goal 接管的
Action，但必须明确 reparent 到稳定 parent step。

## 14. 边界场景

| 场景 | 处理 |
|---|---|
| 重复 spawn | parent step + idempotency key 复用 |
| Child 启动失败 | 释放预算，父 Goal 得到结构化 failure |
| Parent Turn 结束 | Background Child 继续，前台等待解除 |
| Parent Goal 取消 | 传播 cancel；不可撤销 Action 标记 cancellation_pending |
| Child 完成事件重复 | SubRun terminal CAS + Outbox 幂等 |
| Child Worker 丢权 | fencing 阻止迟到提交，新 Worker 恢复 |
| Child 需要用户输入 | 转 `waiting_user`，问题由 Parent/UI 统一呈现 |
| 多 Child 修改同一文件 | 禁止共享写或使用隔离 Workspace revision |
| MCP/Skill 热更新 | Child 固定 catalog/skill revision |
| Child 结果过大 | Artifact 化，Parent 只收摘要和 refs |
| Child 返回错误事实 | Parent/Verifier 依据 evidence 验证 |
| 取消与完成竞态 | 数据库终态先到先得，记录 cancel 是否生效 |
| 预算耗尽 | Child 停止并返回 partial evidence，不隐式追加预算 |

## 15. 与 Grok 的取舍

直接采用：

- 独立 child context。
- Agent type + Persona/contract 分离。
- capability mode。
- 深度 1。
- foreground 自动后台化。
- resume 时继承 transcript 但重渲染控制面。
- worktree/Workspace isolation。
- Parent 只接结果摘要、可查看完整 transcript。

保留本项目更优部分：

- DB Actor/Worker、lease、fencing 和原子终态。
- serial/branch ContextSnapshot。
- 媒体任务异步完成、退款和 Artifact。
- 多通道持久 UI 恢复。

不照搬：

- 进程内 Coordinator 作为唯一事实源。
- Web foreground 等待 600 秒。
- 本地 background manifest 代替任务恢复。
- 默认 general-purpose full capability。
- 子 Session 共享 terminal/MCP 就自动获得权限。

## 16. 分阶段落地边界

本轮只形成设计，不修改运行代码、数据库或 API。后续建议：

1. 先定义 SubRunRequest/Result 和 DelegationPolicy。
2. 复用 branch claim 实现只读研究型 SubRun。
3. 接入父 Goal wake event、usage 和 Artifact refs。
4. 增加 list/get/cancel 和 UI 任务卡。
5. 再开放受限 write/execute 与 Workspace isolation。
6. 最后实现 resume/fork、Agent Persona 和多 SubRun 链式合同。

下一层进入 Persistence，统一 Session、Run、Goal、SkillRun、SubRun、Action、Artifact、
事件、Checkpoint、Outbox 和恢复边界。
