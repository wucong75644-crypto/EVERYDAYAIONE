# Agent Runtime AR-00 技术基线与迁移边界

> 状态：AR-00 冻结基线
> 日期：2026-07-26
> 代码基线：`35cefe1eab4390642b5451a416ac11c39da53625`
> 适用：AR-01～AR-04 及后续 Agent Runtime 实施
> 本文范围：当前事实、目录、租户与配置继承、模型映射、RPC 责任和迁移边界

## 1. 基线结论

1. Agent Runtime 唯一主实现目录是 `backend/services/agent/runtime/`。
   禁止创建 `backend/services/agent_runtime/` 或其他平行 Runtime 根目录。
2. Conversation Actor、Context、Memory、Artifact 是已实施并投入现有主链的基础设施，
   但不等于目标 Agent Runtime 持久模型已经实施。
3. 持久化 Agent Session、Run、ModelStep、Action、RuntimeEvent、Goal、Skill、MCP
   和 Subagent 尚未实施。相关数据库及 RPC 文档描述目标合同，不是现行生产 Schema。
4. 现有 Conversation/Task/Message 在迁移期继续承担生产事实和用户投影；新模型只能
   additive 接入，并通过显式映射迁移。
5. PostgreSQL 继续是持久业务事实源；Redis 只负责唤醒、缓存、限流和锁，不决定终态。
6. 任一阶段只能有一个终态、副作用和费用 Owner。Shadow 写不得执行外部副作用、
   扣费、退款或生成第二份用户终态。

## 2. 代码、迁移与生产事实

### 2.1 已实施基础设施

| 能力 | 现有证据 | 当前责任 |
|---|---|---|
| Conversation Actor | `backend/migrations/121_*`～`129_*`、`backend/services/conversation_*` | 持久队列、claim、lease、fencing、进度、原子终态和投递 |
| Context | `backend/migrations/138_unified_conversation_context.sql`、`backend/services/agent/runtime/context/` | ContextItem、Receipt、ProviderPlan、Pruning、Compaction |
| Memory | `backend/migrations/113_*`、`140_*`、`142_*`～`144_*` | Memory Atom、Session Log、Consolidation 和人工记忆 |
| Artifact | `conversation_artifacts`、`backend/services/agent/runtime/artifacts/` | 工具事实规范化、存储、读取、投影和 Actor 提交 |
| 租户数据库防线 | `backend/migrations/150_*`、`151_*` 及后续角色迁移 | 现有会话、上下文、资产和记忆表的 Scope/RLS/最小授权 |

`backend/migrations/150_agent_runtime_tenant_defense.sql` 所称“Agent Runtime 首组 13 表”
是 Conversation Context、Attachment、Memory、Asset 等既有基础设施表，不是下节的
目标 Session/Run/Action/Event 表。

截至本基线，生产记录显示 Conversation Actor 与上述基础设施已经部署，服务角色和 RLS
已切换；这只能证明既有链路可作为迁移底座，不能证明目标 Agent Runtime Schema 已存在。

### 2.2 尚未实施的目标对象

下列对象在当前迁移目录和运行时代码中均没有持久主实现：

| 目标能力 | 尚未实施内容 |
|---|---|
| Session | `agent_runtime_sessions`、Session Command、持久 continuation owner |
| Run | `agent_runs`、RunAttempt、独立 Run lease/fencing |
| ModelStep | `agent_model_steps`、确定请求/响应和 stop reason 的持久记录 |
| Action | `agent_actions`、ActionAttempt/Result、Unknown/Reconcile |
| RuntimeEvent | 持久有序事件、Snapshot、统一 Projection Outbox |
| Goal | Goal/Round、Continuation Controller |
| Skill | 产品 Skill Registry、绑定、版本、运行记录 |
| MCP | 多租户 Server 配置、Catalog、调用与回执 |
| Subagent | Child Run/SubRun、父子预算、隔离 Workspace/Context |

现有代码中的 `model_step` 字段、ContextReceipt、Validation Receipt、工具循环和
Conversation Actor 事件只是可复用输入，不得改名或描述成这些持久对象已经完成。

## 3. 唯一目录与依赖规则

所有新增 Runtime 核心代码必须放入：

```text
backend/services/agent/runtime/
  domain/          # 状态、不可变量和无框架协议
  application/     # Session、Run、ModelStep、Action 等用例
  ports/           # Repository、Model、Executor、Event、Projection SPI
  infrastructure/  # PostgreSQL、Redis、Provider、MCP 适配
  projections/     # Message、Web、WeCom、审计投影
  compatibility/   # Conversation/Task/Message 单向兼容映射
```

现有 `context/`、`artifacts/`、`validation/` 及 Runtime 根模块继续原地演进。专业能力仍
保留在 `backend/services/agent/`，通过 port/adapter 接入，不迁移复制。

依赖方向固定为：

```text
Ingress/Worker → runtime.application → runtime.domain/ports
runtime.infrastructure/projections/compatibility → runtime.domain/ports
runtime adapter → existing conversation/agent services
```

禁止 domain 依赖 FastAPI、WebSocket、WeCom、Provider SDK 或现有 Handler；禁止旧模块
反向依赖 compatibility 内部实现。

## 4. 三级隔离模型

### 4.1 冻结模型

| 层级 | 稳定身份 | 数据边界 |
|---|---|---|
| 企业 | `org_id` | 企业策略、共享配置、Channel、企业 Skill/MCP 和企业业务数据 |
| 企业员工 | `user_id + org_id` | 员工在指定企业内的个人会话/资产；必须是 active member |
| 散客 | `user_id + org_id=NULL` | 个人配置、Skill、Workspace、Memory；不得读取任何企业资产 |

企业员工的个人资产不会因加入企业自动转为企业资产。相同 `user_id` 在不同 `org_id`
下是不同 Runtime Scope。`org_id=NULL` 只表示散客/个人空间，不是“所有企业”。

Session、Run、ModelStep、Action、Artifact、RuntimeEvent、Goal、SkillRun 和 SubRun
必须继承同一不可变 Scope；子对象不得自行提供或改变租户身份。数据库 RPC 必须从父事实
反查并校验冗余 `org_id/user_id`，不能只信任应用参数。

### 4.2 失败关闭

- 企业或成员停用：禁止新 claim/Action；已接受外部 Action 仅允许受控 reconcile。
- Scope 缺失、冲突或父子不一致：拒绝创建和状态推进。
- 企业 Session 调用个人能力：必须同时满足个人所有权和企业策略。
- Subagent、Skill、MCP 只能缩小能力和数据范围，不能改变父 Session Scope。

## 5. 配置与策略优先级

冻结继承顺序：

```text
全局管理员 → 企业策略/配置 → 个人配置 → Session 临时配置
```

箭头表示从基础层到更具体层；解析有效值时后层优先，但只在前层允许范围内覆盖：

1. 全局管理员定义平台默认、Schema、安全硬约束和可覆盖性。
2. 企业策略/配置在平台允许范围内设置企业值，可锁定或禁止个人覆盖。
3. 个人配置仅在散客空间或企业策略允许时生效。
4. Session 临时配置只在当前 Session 生命周期内生效，不写回个人或企业配置。

企业锁定、安全限制、权限、Secret 可见性和费用上限不能被个人或 Session 放宽。缺失必需
配置、定义版本不一致或 Secret 不可用时失败关闭；允许降级的非必需项按上层默认回退。

创建 Run 时必须冻结 `effective_config_snapshot/revision`。同一 Run 内配置变化不热替换；
新用户输入、显式重载或下一 Run 才读取新 revision。

## 6. 新旧模型映射

| 现有模型 | 目标模型 | 冻结关系 |
|---|---|---|
| Conversation | Agent Session | 一对一扩展；Conversation 继续保存用户可见会话和 revision |
| Chat generation Task | Run | 一次被 Actor 执行的生成任务映射一个 Run |
| 媒体/外部 Task | Action / ActionAttempt | Task 保存专业执行事实；Action 统一编排和终态引用 |
| Message | Projection | 用户输入触发 Run；助手 Message 是 Run/Artifact 的用户可见投影 |
| Tool call/step | Action | 由确定的 ModelStep 产生；稳定 tool call ID 用于幂等映射 |
| ContextReceipt `model_step` | ModelStep receipt | 作为迁移输入，不能单独证明持久 ModelStep 存在 |
| Conversation Actor progress/WS event | RuntimeEvent Projection | 兼容投影输入，不是目标持久 Event Store |
| `conversation_artifacts` | Artifact | 现有持久 Artifact 基础；迁移不得建立第二套平行事实 |

Projection 只能从 Runtime/兼容事实生成 Message、ContentPart、WebSocket 和企微输出，
不得由展示状态反推 Runtime 终态。

## 7. 共用 RPC 与角色责任

以下名称是 AR-01～AR-04 共用的目标合同；在相应迁移和实现落地前不得写成现有 RPC。

| 聚合 | 目标 RPC 族 | 唯一责任 |
|---|---|---|
| Session | `ensure_agent_runtime_session`、`submit_session_command`、`claim/release_session_continuation` | Scope、命令幂等、continuation 单 Owner |
| Run | `create_agent_run`、`claim_agent_run`、`renew_agent_run`、`set_agent_run_waiting`、`wake_agent_run`、`complete_agent_run`、`fail_agent_run`、`cancel_agent_run` | Run lease/fencing、blocker、终态 |
| ModelStep | `create_model_step`、`complete_model_step`、`fail_model_step` | 确定模型请求、响应、usage、stop reason |
| Action | `decide_agent_action`、`claim_agent_action`、`mark_action_accepted`、`complete_agent_action`、`fail/reject/cancel_agent_action`、`mark/resolve_unknown_action` | Policy 后的副作用、Attempt、结果和不确定性 |
| Event | 仅内部 `append_agent_runtime_event` | 在业务 RPC 事务内分配 Session sequence 并追加事件 |
| Projection | `claim/complete/fail_agent_projection` 或等价 Outbox RPC | 可重放投影，不决定业务终态 |

角色边界：

- Ingress/API：认证、解析 Scope、提交 Command；不执行模型或 Tool。
- Runtime Worker：claim Session/Run，协调 ModelStep 和同步 Action。
- Executor Worker：只按受限 Capability 执行 Action，不决定 Run 终态。
- Reconciler：处理 Accepted/Unknown、回调早到和超时对账。
- Projection Worker：生成 Message/Web/WeCom/Audit 投影。
- Migrator/Owner：创建 Schema、RPC、RLS 和 Grant；运行时角色无 DDL 权限。

所有状态 RPC 必须执行父 Scope 校验、幂等检查、状态/CAS、fencing、业务写入、
RuntimeEvent/Outbox 同事务追加，并返回闭合 outcome。外部 IO、OSS、模型、MCP、
WebSocket 和企微发送不得在数据库事务内执行。

## 8. AR-01～AR-04 迁移边界

仓库当前没有 AR-01～AR-04 的既有任务定义，因此本文不臆造任务名称或文件归属，只冻结
所有后续任务必须共同遵守的实施顺序和交接门禁：

1. **Expand**：先新增目标 Schema/RPC/RLS/Grant；不改旧 owner。
2. **Shadow**：旧 Conversation Actor 是唯一生产 owner；新模型只记录可对账事实。
3. **Canary**：按明确能力切换单一 owner；同一 Run/Action 不允许新旧链同时执行。
4. **Projection cutover**：RuntimeEvent 可重放验证通过后，再切 Message/Web/WeCom 读取。
5. **Contract**：全部调用方迁移、回滚窗口结束、无 active/unknown Action 后才删除旧合同。

每个后续任务必须明确自己拥有的表、RPC、应用入口和 Projection，不得跨任务重复创建。
涉及新表或 RPC 时同步更新 `PROJECT_OVERVIEW.md`、`FUNCTION_INDEX.md`、迁移回滚和角色
授权；不得创建第二套 Event Store、Session、Goal、Skill、MCP 或 Subagent 根对象。

## 9. 边界与恢复

| 场景 | 冻结处理 |
|---|---|
| 重复 Command/回调 | 稳定幂等键返回既有 receipt，不重复执行 |
| Worker 丢 lease/fencing | 立即停止本地推进，旧 token 禁止提交 |
| Shadow 写失败 | 不影响旧主链终态；记录对账失败，不重试副作用 |
| RuntimeEvent 追加失败 | 与业务状态同事务回滚 |
| Projection 失败/断线 | 按 sequence 重放，不回滚业务终态 |
| 回调早于 Accepted | 先进入 Callback Inbox，关联 Attempt 后应用 |
| Accepted/Unknown 回滚 | 继续由同版本 Reconciler drain，禁止旧链重提 |
| Session 配置越权 | 解析失败关闭，不静默扩大权限 |
| Scope 状态变化 | 新工作失败关闭；外部已接受工作仅允许对账收口 |

## 10. 验收门禁

- `backend/services/agent_runtime/` 不得作为现有或计划实现路径；仅允许出现在禁止性说明。
- 目标对象必须明确标记“尚未实施”或附真实迁移/代码证据。
- 三级 Scope、四层配置和新旧映射在数据库、RPC、应用与 Projection 文档中一致。
- Shadow/Canary 任一时刻只有一个副作用、费用和终态 Owner。
- 所有新增迁移有 rollback、RLS/Grant/真实 PostgreSQL 契约测试和生产前 plan 门禁。
- 未经独立任务授权，不修改运行时代码，不部署生产。
