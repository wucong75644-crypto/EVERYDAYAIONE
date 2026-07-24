# AGENT 08：Goal Orchestrator 与持续目标编排

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：Goal 状态、Planner、Completion Verifier、Stall Strategist、Continuation
> 后续专项：Context、Skills、MCP、Persistence、UI Event 和端到端链路继续核验

## 1. 结论摘要

Goal Orchestrator 不应成为第二个“全能 Agent”。Grok Build 的真实结构是：

```text
模型负责执行工作
Harness 负责：
  Goal 状态
  → 首次计划契约
  → 每轮是否继续
  → 独立完成验证
  → 无进展检测
  → 暂停 / 恢复 / 预算终止
```

这套设计直观的原因是 Orchestrator 不决定每个 ToolCall，只保证 Agent 不会：

- 做一轮就擅自宣布结束。
- 用自己的总结证明自己完成。
- 对同一错误无限重试。
- 在基础设施异常后继续自驱。
- 忘记目标、计划、缺口和预算。

EVERYDAYAIONE 已有 `ExecutionBudget`、`StopPolicy`、循环检测、ERP Plan、plan 模式和
Conversation Actor，但这些能力分别服务于单次工具循环、ERP 参数提取或权限模式，
没有一个跨 Turn、跨 Worker、可持久恢复的 Goal 状态机。

推荐采用 Grok 的“小 Harness、大 Worker”方向，但不照搬昂贵的默认三名 verifier：

1. 普通聊天继续走一次 Run，不自动升级为 Goal。
2. 只有用户明确要求持续完成，或系统判断任务包含长链路/异步等待时创建 Goal。
3. Planner 只把目标冻结为验收契约和初始步骤，不生成不可变的全部 ToolCall。
4. Worker 仍使用统一 Agent + ToolBridge。
5. Completion Verifier 使用风险分级：确定性证据优先，模型审查按需启用。
6. Stall Strategist 只在重复缺口时建议结构调整，不直接修改目标契约。
7. Continuation Controller 是确定性状态机，不靠模型一句“我会继续”。

## 2. Grok Build 的 Goal 状态

### 2.1 GoalTracker

源码：`xai-grok-shell/src/session/goal_tracker.rs`

`GoalTracker` 是无异步 I/O 的纯状态机，由 Session Actor 持有。阶段：

- `Idle`
- `Planning`
- `Executing`

状态：

- `Active`
- `UserPaused`
- `BackOffPaused`
- `NoProgressPaused`
- `InfraPaused`
- `Blocked`
- `BudgetLimited`
- `Complete`

未知持久化状态恢复为 `UserPaused`，不会恢复为自动执行中的 Active。这是安全恢复
原则：新版本无法理解的状态只能停住等待用户，不能自行续跑。

### 2.2 GoalOrchestration

持久状态包含：

- goal ID、objective、status、phase。
- 可选 token budget、token baseline、累计 token。
- elapsed time 与 active_since。
- worker/verify 轮数。
- 当前子 Agent 和角色。
- planner/verifier/strategist 文件路径。
- classifier 次数、最近 verdict 和 gap。
- 连续相同 gap fingerprint。
- 连续 NotAchieved 次数和 strategist 触发点。
- pause message 和最多 64 条 History。
- UI 使用的 live context、turn、tool count。

状态不仅是 `running/completed`，而是足够恢复“为什么还在执行”和“下一步为什么
继续”。

### 2.3 状态转换

`create_goal()` 建立 Active 目标和计时；`pause()` 只允许从 Active 进入具体暂停
原因；`resume()` 清理 pause message、验证计数、stall 和 strategist 临时状态；
`complete()` 与 `budget_limit()` 是终态；`clear()` 删除目标及临时验证目录。

进程恢复时，原 Active Goal 会转成暂停态，而不是无人监督地自动继续。对本项目
可调整为：有持久 Worker lease 且策略允许时恢复排队，否则转 `recovery_paused`。

## 3. Grok Planner

### 3.1 Planner 角色

源码：

- `session/goal_planner.rs`
- `templates/goal_planner_prompt.md`

Planner 是独立 general-purpose 子 Agent，读取 Workspace 并写 `goal/plan.md`。
终端响应必须严格为 `Done`；真正成功还要求计划文件存在。

计划内容区分：

- Objective。
- Goal kind：code-change、analysis/research 等。
- 原子、可验证的 Acceptance Criteria。
- Verification Plan：gating 与 evidence。
- Non-goals。
- Assumed scope。
- 实现方式建议。
- 3～8 项 Task Checklist。
- Risks/Contradictions。

最重要的约束是：计划可以澄清，不能缩小或替换用户 Objective；实现方式和 Checklist
只是 HOW，不是完成合同；Acceptance Criteria 才是 verifier 的共同判断基准。

### 3.2 失败语义

Planner 默认只运行一次，失败时 fail closed：Goal 暂停。若配置了专用 Planner
模型/Agent，首次 spawn 非取消失败会使用当前模型和默认 Harness 重试一次；第二次
失败才暂停。

本项目应保留“Planner 失败不能盲目执行”，但简单 Goal 可使用确定性轻量计划，
不必每次 spawn 子 Agent。

### 3.3 Plan 与权限模式不同

Grok Goal Plan 是验收合同；项目现有 `PermissionMode.PLAN` 是只分析、不执行的
交互权限状态。两者必须分开：

- `GoalPlan`：做什么、如何证明完成。
- `InteractionMode=plan`：当前是否允许执行副作用。

用户确认计划只是授予下一阶段权限，不代表修改 Goal 的验收合同。

## 4. Grok Completion Verifier

### 4.1 独立反证

源码：

- `session/goal_classifier.rs`
- `templates/goal_verifier_prompt.md`

Verifier 不相信 Worker 的最终总结，而是读取：

- 原始 Objective。
- Plan 和 Plan 变更。
- 当前 changed files。
- Worker tests 与捕获证据。
- 最终回答。
- 上轮 gaps。

它的任务是尝试反证完成，而不是重新实现任务。重复验证时优先检查上轮 gap，
禁止每轮抬高标准造成永不收敛。

### 4.2 Skeptic Panel

默认并行运行 3 名 skeptic，配置范围 1～5。多数未反对才通过：

- `Achieved`
- `NotAchieved`
- `Blocked`
- `FailOpenAchieved`

默认 classifier 总轮数 10，环境变量/远程配置只有最小值 1，没有最大值。Diff 最大
256 KB，聚合 Panel 详情最大 512 KB；验证临时目录为每 Goal 独立 0700，并检查
owner、真实目录和路径前缀，防止 `/tmp` symlink 攻击。

### 4.3 失败语义风险

解析层不确定默认反对，防止假完成；但纯基础设施失败会
`FailOpenAchieved`，避免内部 verifier 故障阻塞用户。

本项目不能把这条直接用于付费、部署、删除或 ERP 写入 Goal。推荐：

- 安全/财务/外部副作用：verifier infra failure → `verification_pending`。
- 普通低风险内容任务：允许降级为单 verifier 或确定性检查。
- 永远不能由 verifier 触发新的副作用来“证明”完成。

### 4.4 证据优先

Grok 强调测试驱动真实 shipped path，禁止：

- 硬编码期望值。
- Mock 被测单元自身逻辑。
- 从目标状态中间开始。
- 用重新实现对比重新实现。
- TODO、skip 或生成假 Artifact。

本项目不应照搬编码专用提示词到所有业务，但应采用统一原则：

```text
Completion = 目标条件 + 可验证事实
不是 Worker 自述
```

媒体用 Artifact/Provider/结算事实；ERP 用查询结果或业务单号；文件用 checksum 和
Workspace revision；消息发送用外部 ACK；研究任务以落盘文档和来源覆盖为证据。

## 5. Grok Stall Strategist

### 5.1 两类停滞

GoalTracker 记录：

- 相同 gap fingerprint 连续出现。
- 所有 NotAchieved 的连续次数。

默认相同 gap 连续两次触发 no-progress pause。Strategist 触发后获得额外 3 次
classifier 预算，stall threshold 暂时放宽到 5；仍无进展则停止。

### 5.2 Strategist

源码：

- `session/goal_strategist.rs`
- `templates/goal_strategist_prompt.md`

Strategist 读取计划、Session traces 和各轮证据，只建议一个结构性调整，写入
`goal/strategy.md`。建议内联最多 4096 字符。

它是 best effort、fail open；失败不会暂停 Goal。为防它篡改验收合同，运行前快照
`plan.md`，结束或 future 被取消时由 Guard 恢复。

推荐保留这个职责边界：

- Verifier 说哪里没满足。
- Strategist 说执行方法为什么不收敛。
- 用户或 Planner 才能变更 Objective/Acceptance Criteria。
- Worker 根据 strategy 调整 HOW。

## 6. Grok Continuation Controller

### 6.1 下一步生成

源码：

- `session/goal_next_step.rs`
- `acp_session_impl/goal_support.rs`
- `templates/goal_continuation_directive.md`

Controller 每轮从计划中提取第一个未完成 `- [ ]` Checklist，读取最多 8 KB，最终
内联 next step 最多 400 字符。Acceptance Criteria、Non-goals 和 Deviations 不会
被误当下一步。

续跑指令同时包含：

- Objective/status/token/elapsed。
- 上轮 verifier gap。
- strategist 建议。
- 是否要求重新验证。
- 下一个具体步骤。
- 证据与测试纪律。

历史中只保留最新 continuation directive，避免上下文不断累计旧提醒；队列中已有
相同提醒时不重复入队。

### 6.2 防提前停止

`goal_stop_detector.rs` 检测最后一段中的明确放弃、稍后再看、任务仍在运行、已经
提交待评审等模式。命中只说明模型可能提前退出，Controller 会再次续跑；它不是
完成判断。

这些英文正则不适合直接复制到中文 SaaS。更可靠的主判断应是：

```text
Goal status 仍 Active
且 Completion 未通过
且没有 Wait/User/Policy/Budget/Infra blocker
→ 必须继续
```

文本 stop detector 只做辅助遥测。

## 7. EVERYDAYAIONE 当前能力

### 7.1 ExecutionBudget

源码：`backend/services/agent/execution_budget.py`

当前默认：

- max turns 15。
- wall time 600 秒。
- 预留 1 个 wrap-up turn。
- 子 Agent 有独立轮次上限，但共享父剩余墙钟。
- 单工具 timeout 为 `min(per-tool, remaining)`，最低 1 秒。

这是一次 Run 的安全预算，不是跨媒体等待、跨 Worker 的 Goal 预算；重启后
monotonic start、turn count 和子预算无法恢复。

### 7.2 StopPolicy

源码：`backend/services/agent/stop_policy.py`

已有：

- ResultClass：SUCCESS/RETRYABLE/NEEDS_INPUT/AMBIGUOUS/FATAL。
- StopDecision：CONTINUE/WRAP_UP/HARD_FAIL。
- 同类错误默认最多重试 1 次。
- 连续失败 3 次 wrap-up。
- 15 秒 Final Synthesis timeout。
- FailureTracker 使用工具名和错误前 80 字符 hash。

它能防单次工具循环死转，但不能证明 Goal 完成。当前 `HARD_FAIL` 枚举存在，
`evaluate()` 实际不产生该分支；`has_meaningful_progress` 仅被设置，没有进入停止
决策。这些作为既有语义风险记录，不在调研阶段修改。

### 7.3 ToolLoop 循环检测

`ToolLoopExecutor` 对工具名 + 原始 arguments hash 建立 call key，连续三轮完全
相同即 `loop_detected`。这只能检测字节相同调用，无法识别：

- 参数轻微变化但结果相同。
- 工具不同但没有推进目标。
- 重复产生同一 Artifact。
- 同一 verifier gap 未被解决。

Goal 层需要基于目标 gap 和证据变化判断 meaningful progress。

### 7.4 现有 Planner

ERP `ExecutionPlan` 只包含 domain steps、params、dependency 和 compute hint，服务
查询路由；图片 `ecom_plan` 服务用户确认视觉方案；Permission plan 模式控制是否
执行。三者都不是跨能力 Goal Plan，不能直接提升为全局 Orchestrator。

可以复用的只有模式：

- LLM 提取失败后确定性降级。
- step 并行/串行。
- plan 与 execute 分离。
- 计划结果结构化展示。

### 7.5 Conversation Actor

Actor 已提供持久排队、claim/lease/fencing、取消和原子终态，是 Goal Run 的执行
底座。但当前一条 chat generation 完成即终态，没有 Goal 跨多个 generation、
异步 Action 和 verifier round 的父级状态。

## 8. 目标最小架构

```text
Goal
  objective + contract + budget + status
  ↓
GoalRun / Round
  ↓
Worker Agent
  ↓
Actions / Artifacts / Evidence
  ↓
Completion Verifier
  ├─ achieved → complete
  ├─ unmet → continuation
  ├─ blocked → pause
  └─ infra → verification_pending
  ↓
Stall Controller
  ├─ progress → continue
  ├─ same gap → strategist
  └─ still stalled → no_progress_paused
```

顶层仍只有总纲里的 `Goal Orchestrator`，Planner、Verifier、Strategist 和
Continuation 是内部角色，不新增更多一级架构盒子。

## 9. 决策规则

### 9.1 何时创建 Goal

不应所有聊天都创建 Goal。候选条件：

- 用户明确说持续完成、不要停、直到验证通过。
- 包含多个有依赖步骤。
- 包含异步任务完成后的后续动作。
- 需要交付多个 Artifact。
- 预计超过一次 Run 的时间/轮次预算。

天气查询、一次搜索、单张图片生成仍是普通 Run/Action。

### 9.2 Planner

- 简单多步目标由 Worker 首轮直接形成结构化 Plan。
- 高风险、长任务或用户要求先确认时使用独立 Planner。
- Objective 原文不可被 Planner 改写。
- Acceptance Contract 与执行 Checklist 分离。
- Planner 失败暂停，不进入盲执行。

### 9.3 Verifier

优先级：

1. 确定性状态和 Schema。
2. 专业 Executor proof。
3. 测试、checksum、外部 ACK、业务单号。
4. 单模型语义审查。
5. 仅高价值复杂任务启用多 verifier。

默认三 skeptic 对普通用户任务成本过高，不采用为全局默认。

### 9.4 Continuation

Controller 只依据持久状态：

- Active + unmet + runnable → enqueue next round。
- waiting external task → 注册唤醒，不占 Worker。
- needs user/policy → pause 并展示请求。
- budget exhausted → budget_limited。
- infra error → infra_paused/retry policy。
- verified achieved → complete。

## 10. 关键风险

1. 模型调用 `update_goal(complete)` 只能提出完成候选，不能绕过 Verifier。
2. Verifier 不能拥有超出 Worker 的读取权限，也不能执行副作用。
3. Plan 不能在失败后悄悄降低 Acceptance Contract。
4. 用户追加要求必须产生 Objective revision，不能无审计改写原 Goal。
5. 异步 Action 完成唤醒必须幂等，避免同时启动多个 Round。
6. Pause 与 cancel 分开：pause 可恢复，cancel 终止未提交 Action。
7. Token budget、金额 budget、wall deadline 和 Action 数量分别计量。
8. Context rotation 不能丢失 contract、未满足 gap 和证据索引。
9. Goal 完成后迟到 Provider 结果仍需 reconcile，但不得重新激活 Goal。

## 11. 验收场景

1. 普通天气查询不创建 Goal，一次 Run 完成。
2. “生成三张图，选最好的一张做视频”：创建 Goal，图片并行，全部完成后评选并生成视频。
3. Worker 在图片等待期间释放；Webhook 唤醒唯一下一 Round。
4. 模型自称完成但视频仍 pending：Verifier 拒绝完成。
5. 三轮都遇到同一 Provider 错误：Strategist 建议换路径，仍失败则 no-progress pause。
6. ERP 写入响应 unknown：Goal 等待 reconcile，不重放写入。
7. 用户暂停后 Worker 重启：Goal 保持 paused，不自动执行。
8. 用户追加“视频改成竖版”：生成 Objective revision 和受影响步骤，不污染已完成图片证据。
9. Verifier 服务故障：部署/扣费 Goal 进入 verification_pending，不 fail-open complete。
10. 达到 token budget：保留已完成 Artifact 和缺口，状态 budget_limited。
11. Context 压缩后：Objective、Contract、未满足 gaps 和 Evidence refs 完整。
12. 完成后迟到重复 Webhook：只做幂等结算，不新增 Round。

## 12. 对总体重构的输入

1. 新增可持久化 Goal/GoalRun/Round/Contract/Evidence 概念，具体表结构留到 Persistence。
2. GoalTracker 使用纯状态机，Session Actor/Worker 负责 I/O 和 claim。
3. Planner、Verifier、Strategist 均是可替换内部角色，不成为一级万能服务。
4. `update_goal` 只能更新进度或提出终态，完成必须通过 Verifier。
5. 复用 ExecutionBudget/StopPolicy，但区分 Run budget 与 Goal budget。
6. Continuation 使用数据库幂等 enqueue 和异步 Action 唤醒。
7. 普通聊天默认不创建 Goal，控制成本和架构复杂度。
8. 下一阶段 Context Engineering 定义 Goal Contract、Evidence、Observation 和历史怎样进入模型上下文。

下一板块进入 Context Engineering，专项核验上下文额度、信息分层、压缩、检索、
ToolOutput/Artifact 引用、Goal 状态和跨 Worker 恢复。
