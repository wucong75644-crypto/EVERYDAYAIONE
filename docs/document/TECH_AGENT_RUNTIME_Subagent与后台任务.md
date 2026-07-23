# Agent Runtime Subagent 与后台任务设计

> 状态：总体设计 / 第一轮冻结
> 日期：2026-07-18
> 前置：Run/Goal 状态机、ContextPlan、Executor SPI、Policy

## 1. 定位

```text
Subagent = 独立上下文中的受限 Child Run
         + 结构化输入/输出合同
         + 独立能力和预算
         + Artifact/Evidence 回传
```

Subagent 用于上下文隔离、真正独立的并行工作和专业 Agent。它不承担顺序工作流、媒体等待、单次工具调用或核心权限治理。

```text
Goal / SkillRun
  ├─ Parent Run
  ├─ SubRun A
  ├─ SubRun B
  └─ Background Action（媒体/MCP/外部任务）
```

## 2. 当前基础与差距

可复用：

- Conversation Actor、serial/branch claim；
- PostgreSQL lease/fencing；
- ExecutionBudget/StopPolicy；
- 媒体异步 Action；
- ScheduledTaskAgent；
- ContextSnapshot、Artifact 和 RuntimeEvent。

缺少：

- spawn/list/get/cancel SubRun；
- 父子 Run、输入输出合同；
- AgentDefinition/capability/isolation 解析；
- 独立 child ContextPlan；
- Child usage 向 Parent Goal 结算；
- terminal 后 parent wake 的原子边界。

`BackgroundTaskWorker` 是媒体轮询器，`ScheduledTaskAgent` 是定时触发 Agent，二者都不是通用 Subagent。

## 3. SubRunRequest

```json
{
  "parent_run_id": "uuid",
  "parent_goal_id": "uuid",
  "parent_step_id": "research-a",
  "agent_definition_id": "research.readonly",
  "objective": "核验供应商数据来源",
  "input_refs": ["artifact:uuid"],
  "expected_outputs": ["report", "evidence"],
  "context_mode": "selected",
  "execution_mode": "background",
  "capability_mode": "read_only",
  "workspace_isolation": "staging_revision",
  "budget": {
    "max_tokens": 50000,
    "max_seconds": 600,
    "max_cost": 20,
    "max_actions": 20
  },
  "idempotency_key": "parent-step:research-a"
}
```

`prompt` 不能作为唯一合同。Objective、输入 ref、预期输出、权限和预算必须结构化。

## 4. 能力与授权

```text
ChildCapabilities
= ParentDelegableCapabilities
 ∩ AgentDefinition
 ∩ RequestedCapabilityMode
 ∩ Tenant/Channel Policy
 ∩ WorkspaceIsolation
```

Capability mode：

| 模式 | 读 | 写 | 执行 |
|---|---:|---:|---:|
| `read_only` | 是 | 否 | 否 |
| `read_write` | 是 | 是 | 否 |
| `execute` | 是 | 否 | 是 |
| `all` | 是 | 是 | 是 |

这是粗筛，不替代 Action Policy。AuthorizationGrant 只有显式 `delegable=true` 且 action/scope/budget 匹配时才可传递。

默认 Child 为 `read_only`。子 Agent 无法自行 spawn 孙 Agent，首期最大深度固定 1。

## 5. Context

默认 `selected`：

- Objective 和 output contract；
- 当前 Goal gap/ContextSummary；
- 必需 Message、Artifact、文件和 ToolOutput refs；
- Capability/Policy constraints；
- 选定的 Skill/MCP catalog subset。

不复制：

- 全部聊天历史；
- Parent 隐藏推理；
- 无关 Memory/persona；
- 未授权资产；
- 全部工具/Skill/MCP 目录。

Context mode：

| 模式 | 使用 |
|---|---|
| `fresh` | 只给合同和显式 refs |
| `selected` | 默认，Planner 选择相关 blocks |
| `fork` | 仅特殊审查场景，复制已筛选闭合历史 |
| `resume` | 恢复同 Parent、同 Agent type 的已完成/暂停 SubRun |

Resume 继承事实 transcript 和 refs，但重新渲染当前 Agent、Policy 和 Tool schemas。

## 6. 生命周期

```text
delegate requested
-> Policy/budget reserve
-> SubRun queued
-> Worker claim/lease
-> child Run running/waiting
-> completed/failed/cancelled
-> result + usage + artifact commit
-> parent wake Outbox
-> Goal continuation
```

SubRun 复用 Run 状态机，不另造平行状态：

- queued、running、waiting_actions、waiting_interaction、paused；
- completed、failed、cancelled。

Parent 关系和 child contract 记录在 SubRun relation。Child terminal、Usage、Artifact refs 和 parent wake 必须同事务或 Transactional Outbox。

## 7. 前台与后台

- `foreground` 只表示 Parent 暂时等待，不改变持久执行。
- Web 前台等待初值 30 秒；超时自动转 background。
- Child 不因 Parent ModelStep 结束而取消。
- UI 立即显示 SubRun 卡，Parent 可继续其他工作。
- Parent Goal 需要 Child 结果时进入 waiting_actions；完成事件触发唯一 Continuation owner。

不采用 Grok 600 秒 Web 前台等待。

## 8. 委派策略

满足全部条件才委派：

1. 子任务有清晰输入/输出合同。
2. 与 Parent 当前工作独立。
3. 独立上下文/专业 Agent 收益高于启动成本。
4. 权限、预算和 Workspace 冲突可控。

禁止委派：

- 单次 ToolCall；
- 强顺序依赖的普通步骤；
- 必须连续询问用户的工作；
- 图片/视频 Provider 等待；
- 仅为了把简单任务包装得复杂。

Planner 提出 candidate，确定性 DelegationPolicy 决定是否允许和并发；模型不能无限 spawn。

## 9. 参数

| 参数 | 初值 |
|---|---:|
| 最大深度 | 1 |
| 每 Parent 活跃 SubRun | 3 |
| 每用户活跃 SubRun | 5 |
| 每组织并发 | 12 |
| foreground await | 30 秒 |
| 默认 capability | read_only |
| Parent 回传摘要 | 2K～4K 字符 + refs |
| query block wait | 30 秒 |
| lease | 60 秒 |
| lease renew | 20 秒 |
| 默认最大 wall time | 600 秒 |

Token/积分由 Parent 预留、Child 实际结算。Child usage 汇总到 Goal，同时保留 subrun 维度。

## 10. Workspace Isolation

| 类型 | 场景 |
|---|---|
| `none` | 只读研究 |
| `staging_revision` | 文件、报表、媒体 |
| `git_worktree` | 代码修改 |
| `dedicated_workspace` | 高风险/大型任务 |

多 Child 不得共享写同一 resource revision。合并必须通过显式 Artifact apply/merge Action，仍走 Policy。普通 SaaS 对话不默认创建 Git worktree。

## 11. SubRunResult

```json
{
  "subrun_id": "uuid",
  "status": "completed",
  "summary": "...",
  "findings": [],
  "decisions": [],
  "artifact_refs": ["artifact:uuid"],
  "evidence_refs": ["evidence:uuid"],
  "open_questions": [],
  "usage": {},
  "child_transcript_ref": "transcript:uuid"
}
```

Parent 默认只接摘要和 refs。代码任务必须附 diff/test，数据查询附来源，文件/媒体附 Artifact，失败附稳定 error class。Verifier 不只相信自然语言 summary。

## 12. Background Action 分界

| 场景 | 抽象 |
|---|---|
| 图片 Provider 等待回调 | Background Action |
| 并行研究三个独立方案 | 三个 SubRun |
| 三段提示词生成三图 | Workflow SkillRun + 三个 Action |
| 研究→实现→审查 | Goal steps + 多个 SubRun |
| 定时查 ERP 生成报告 | Scheduled Goal/Run |
| shell 长测试 | Background Action，可由 Child 发起 |

Child 内部可创建 Background Action。Child 结束时，仍运行 Action 必须显式归属 Goal/parent step；没有稳定 owner 时 Child 不得提交完成。

## 13. 取消与竞态

- Cancel 是请求，不是终态。
- Parent Goal 取消向 Child 传播。
- Child 已受理的不可撤销 Action 继续 reconcile。
- Child terminal 与 cancel CAS，先到终态为准。
- Worker 丢 lease 后停止提交，fencing 拒绝迟到结果。
- Parent Turn 结束不取消 background Child。
- Child 等待用户输入时转 `waiting_interaction`，由 Parent/UI 展示统一 Interaction。
- Partial evidence 在预算耗尽时可以回传，但状态不得伪装 completed。

## 14. 边界场景

| 场景 | 处理 |
|---|---|
| 重复 spawn | parent step + idempotency key 复用 |
| Child 启动失败 | 释放预算，Parent 得到 typed failure |
| Parent 结束 | background Child 继续 |
| Parent Goal 取消 | 传播 cancel，外部 Action reconcile |
| 重复完成事件 | terminal CAS + Outbox 幂等 |
| Worker 丢权 | 新 Worker 恢复，旧 Worker fencing |
| Child 需用户输入 | 统一 Interaction 投影 |
| 多 Child 写同文件 | 隔离 revision 或拒绝 |
| Skill/MCP 热更新 | 固定 catalog revision |
| 结果过大 | Artifact 化 |
| 错误事实 | Verifier 查 evidence |
| 预算耗尽 | 返回 partial，不隐式追加 |

## 15. 方案与影响

| 方案 | 判断 |
|---|---|
| 进程内 coroutine 子任务 | 无持久恢复，不采用 |
| 每个步骤都做 Subagent | 过度复杂，不采用 |
| DB Child Run + 受限 Context/Capability | 推荐 |

| 维度 | 风险 | 应对 |
|---|---|---|
| 成本爆炸 | 中 | 深度 1、并发/预算预留 |
| 权限传递 | 高 | delegable grant + 能力求交 |
| Context 泄漏 | 中 | selected refs |
| Workspace 冲突 | 中 | isolation + apply Action |
| Parent 唤醒重复 | 中 | unique continuation owner |
| 回滚 | 低 | 新能力默认关闭 |

## 16. 实施范围与验收

计划路径：

- `agent_runtime/subruns/types.py`
- `agent_runtime/subruns/delegation_policy.py`
- `agent_runtime/subruns/service.py`
- `agent_runtime/subruns/result.py`
- `agent_runtime/subruns/workspace_isolation.py`
- SubRun relation/RPC/Outbox migration
- Web/企微 SubRun Projection

迁移：

1. 先建立 read-only SubRun 和 selected Context。
2. 接 Artifact/Evidence/Usage 与 parent wake。
3. 开放三个以内并行研究。
4. 增加 staging revision。
5. 最后评估代码 worktree 和 write/execute capability。

验收：

- Child 不能获得 Parent 没有的工具或数据。
- 同 parent step 重放只创建一个 SubRun。
- Parent/Worker 重启后 Child 和 wake 可恢复。
- Parent 只接摘要/refs，不复制完整 child transcript。
- Child terminal、usage、artifact 和 wake 不断层。
- 取消/完成竞态只有一个终态。
- 媒体等待不会被误建 SubRun。
- 多 Child 不会并发覆盖同一 Workspace 资源。
