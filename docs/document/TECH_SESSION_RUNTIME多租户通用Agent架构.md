# 多租户通用 Agent Session Runtime 技术设计

> 状态：已确认
> 日期：2026-07-23
> 方案：A — 在现有 Conversation Actor 上增量建立 Session Runtime
> Grok Build 对标基线：`xai-org/grok-build@a5727c5960452e7527a154b25cb5bf00cda0545e`

## 1. 文档定位

本文记录 2026-07-23 结合当前代码、生产运行状态和最新 Grok Build 基线完成的架构决策。

本文是多租户产品差异、配置继承、Session Build 和实施顺序的总入口，不另起第二套
Runtime 物理模型。下列既有文档继续作为详细合同：

- `TECH_AGENT_RUNTIME目标架构与模块边界.md`
- `TECH_AGENT_RUNTIME核心状态机.md`
- `TECH_AGENT_RUNTIME数据库模型.md`
- `TECH_AGENT_RUNTIME数据库RPC与原子边界.md`
- `TECH_AGENT_RUNTIME交互与Goal状态机附录.md`
- `TECH_AGENT_RUNTIME_Policy授权成本与副作用.md`
- `TECH_AGENT_RUNTIME_扩展运行时Skill_MCP_Plugin_Hook.md`
- `TECH_AGENT_RUNTIME_Subagent与后台任务.md`
- `TECH_AGENT_RUNTIME_多通道Projection与交互协议.md`
- `TECH_AGENT_RUNTIME_测试灰度发布与回滚.md`

本文中的 `SessionBuild`、`CapabilitySnapshot`、`ConfigSnapshot` 是运行时合同概念。
物理表必须扩展既有 `agent_*` 模型，禁止新增同义的
`runtime_sessions`、`runtime_goals` 或第二套 Event Store。

## 2. 已确认的业务边界

系统维持三类相互隔离的主体：

1. 企业。
2. 企业内受企业管理的员工。
3. 不属于企业上下文的散客。

企业员工同时拥有个人配置、个人 Workspace、个人 Skill/MCP 和个人 Memory。个人能力在企业
Session 中使用时仍必须与企业策略求交，不能绕过企业限制。

系统唯一明确区别于 Grok Build 的位置是多层发布与治理：

```text
全局管理员
├── 全局策略与默认配置
├── 系统推荐/自动/强制安装的 Skill
├── 全局 MCP Catalog
└── 默认 AgentDefinition
        │
        ▼
企业
├── 企业策略与配置
├── 企业 Skill/MCP/Agent 发布
├── 部门、职位、角色和成员授权
└── 企业 Workspace/资源
        │
        ▼
用户
├── 个人配置
├── 个人 Skill/MCP
├── 个人 Memory
└── 个人 Workspace
        │
        ▼
Session
└── 冻结后的 Agent、Capability、Config、Policy 和 Extension 版本
```

除这套层级外，Session、Tool、Context、Memory、Skill、MCP、Goal、Subagent、Persistence 和
Projection 的运行语义以 Grok Build 的通用 Runtime 为对标方向。

## 3. 项目现状与可复用资产

### 3.1 架构现状

当前 Web 和企业微信已统一进入 PostgreSQL Conversation Actor。Actor 具备串行/分支 claim、
lease、fencing、取消、Turn revision 和原子终态，是现有最可靠的执行核心。

Context 已具备固定 revision 快照、ConversationItem、Artifact、Receipt、Pruning 和
Compaction；Workspace 已按 `org_id + user_id` 隔离并提供持久文件、预览和 Kernel。

不足是这些能力仍分散在请求/任务链路中，尚未由持久 Session 持有统一的 AgentDefinition、
Capability、Extension、Goal 和恢复状态。

### 3.2 直接复用

- `services/conversation_execution.py`
- `services/conversation_worker.py`
- `services/conversation_runtime.py`
- `services/handlers/chat/execution_engine.py`
- `services/handlers/context_snapshot.py`
- `services/agent/runtime/context/`
- `services/agent/runtime/artifacts/`
- 现有 Tool Loop、Workspace、Kernel、OrgContext、WebSocket Redis 和 Projection
- 既有 `agent_*` Runtime 数据模型和状态机设计

### 3.3 不允许破坏

- Actor 继续作为第一期唯一 Turn Executor 和终态提交者。
- PostgreSQL 是持久事实源；Redis 只用于通知、唤醒和短期缓存。
- Web、企微和未来 SDK 共用同一 Runtime，不产生通道专属业务内核。
- 旧 API、旧消息协议和旧 Actor RPC 在迁移期保持兼容。
- 不允许新旧链同时产生同一个外部副作用。

## 4. 方案决策

### 4.1 采用方案

采用增量 Session Runtime：

```text
Ingress Adapter
    ↓
Session Command Gateway
    ↓
Session Kernel
├── Definition Resolver
├── Policy/Config Resolver
├── Agent Builder
├── Capability Resolver
├── Extension Runtime
├── Event Store
└── Resource Manager
    ↓
Conversation Actor Bridge
    ↓
现有 Conversation Actor / Executor / Atomic Commit
```

不新建独立微服务。第一阶段在现有 Backend 与 Conversation Actor 进程内建立清晰模块边界；
只有出现独立伸缩或故障隔离证据后才评估拆服务。

### 4.2 不采用

- 不重写 Conversation Actor。
- 不把 Session、Goal、MCP 状态继续堆入 `tasks`。
- 不建立与 `agent_*` 平行的第二套 Runtime 表。
- 不按每条用户消息重新对全部工具做语义 Top-N。
- 不将所有 Skill 正文和 MCP Schema 常驻 System Prompt。
- 不以进程内对象作为可恢复状态的唯一来源。

## 5. Session Build

### 5.1 输入

```text
AgentDefinitionRevision
+ Actor(user_id, org_id, org roles)
+ ManagedPolicy revisions
+ ResolvedConfig
+ Workspace resources
+ Extension bindings
+ Memory scope
+ Channel capability
```

### 5.2 输出

Session Build 是不可变快照，至少包含：

- Agent Definition revision
- `actor_user_id`、`org_id`、角色与授权 revision
- Policy snapshot/hash
- Config snapshot/hash
- Skill、MCP、Plugin、Hook 版本
- Tool allow/deny 与 Finalized Capability Set
- Workspace root 和资源 owner
- Permission mode
- Memory scope
- Goal/Subagent 约束
- System Prompt hash
- Synthetic Project Context hash

普通配置变化只影响下一次 Build。以下事件强制使当前 Build 失效：

- 用户被企业移除
- 企业停用
- 全局或企业安全封禁
- MCP 凭证撤销
- Extension 版本被安全撤回
- Workspace 权限收回

## 6. 配置与策略继承

### 6.1 解析顺序

```text
系统默认
→ 全局管理员
→ 企业
→ 当前企业上下文中的个人配置
→ Session 临时配置
```

散客路径跳过企业层。

### 6.2 规则

- `deny` 优先级最高。
- 上层 `locked=true` 后，下层不得覆盖。
- 只有 `user_overridable=true` 的配置允许个人覆盖。
- 系统和企业均可声明推荐、自动、强制和禁止。
- 敏感值只保存 `secret_ref`，不进入普通快照和日志。
- 每个解析结果必须返回来源、revision、锁定状态和被拒绝候选。

示例：

```json
{
  "key": "runtime.permission_mode",
  "value": "ask",
  "source": {
    "scope_type": "organization",
    "scope_id": "org-id",
    "revision": 12
  },
  "locked": true,
  "overridden_candidates": [
    {"scope_type": "user", "reason": "locked_by_organization"}
  ]
}
```

## 7. Capability 与权限

Capability Resolution 固定绑定：

```text
actor_user_id
+ org_id
+ member/role/department/position revision
+ managed policy revision
+ AgentDefinition requirements
+ Extension requirements
+ current resources
```

所有 Tool、MCP、Skill、Workspace 写操作、Goal 和 Subagent 必须经过统一 Policy Gate。
子 Agent 的能力只能等于或小于父 Session，禁止通过委派扩大权限。

现有部门、职位、角色、额外授权和撤销数据必须接入统一决策器，不能只用于定时任务。
ERP、文件、媒体和 MCP 调用必须消费相同的授权结果。

## 8. Skill 共享与安装

作用域：

- 系统发布：全局可发现。
- 企业发布：仅本企业成员可发现。
- 用户发布：默认个人私有。

绑定模式：

| 模式 | 用户能否移除 | 用途 |
|---|---:|---|
| 推荐 | 是 | 系统或企业推荐 |
| 自动安装 | 依策略 | 新用户默认获得 |
| 强制安装 | 否 | 合规或标准流程 |
| 禁止 | 不适用 | 安全或企业治理 |

Skill 启动时只注入预算化元数据；正文和资源按需读取。Session 固定具体版本，普通更新在下一次
Build 生效；恶意版本撤回可以强制失效现有 Build。

## 9. MCP Runtime

MCP Server 需要持久 Catalog、作用域、transport、credential reference、schema revision、
health、退避和 capability mapping。

状态机：

```text
disabled → connecting → ready → degraded → reconnecting
                                      └────→ revoked
```

约束：

- socket 不作为持久事实；恢复时按 Session Build 重建。
- Schema 变化建立新 revision，不静默热替换当前调用。
- MCP Tool 必须进入统一 ToolBridge、Policy Gate 和审计。
- 写操作 timeout 返回 unknown，不得盲目自动重试。
- 子 Agent 默认不继承 MCP，必须显式声明。

## 10. Goal 与 Subagent

Goal、Interaction、Run 和 SubRun 状态继续采用
`TECH_AGENT_RUNTIME交互与Goal状态机附录.md` 的冻结合同。

第一阶段支持：

- 创建、暂停、恢复和取消 Goal
- 持久步骤/轮次
- 重启恢复
- 明确 completed/blocked/cancelled 终态
- 受限 Child Run

第一阶段不支持无限自治、未经授权的外部副作用、跨企业 Goal 或子 Agent 自动扩权。

## 11. 数据与迁移

### 11.1 物理模型

复用并扩展：

- `agent_runtime_sessions`
- `agent_runs`
- `agent_run_attempts`
- `agent_model_steps`
- `agent_model_attempts`
- `agent_actions`
- `agent_action_attempts`
- `agent_action_results`
- `agent_interactions`
- `agent_authorization_grants`
- `agent_goals`
- `agent_goal_rounds`
- `agent_artifacts`
- `agent_runtime_events`
- `agent_usage_entries`
- `agent_legacy_mappings`

配置、Agent Definition 和 Extension Catalog 的新增表必须在实施子方案中与上述表建立单向引用，
不得重复 Run、Goal、Event、Artifact 或 Session 事实。

### 11.2 迁移治理先行

当前仓库存在重复迁移编号，生产不存在迁移账本，`deploy.sh` 也没有执行
`RUN_MIGRATIONS`。任何 Runtime 新表之前必须先建立：

- migration identity
- SHA-256 checksum
- applied/failed 状态
- 应用时间与执行者
- 部署前顺序/checksum 校验
- rollback 关联

迁移 ID 不再只依赖数字前缀。

### 11.3 数据库租户边界

当前部分表开启 RLS 但没有 policy，应用角色又是表 owner，因此未形成真实数据库纵深隔离。
实施前必须明确：

- 应用层 OrgContext/Policy Gate 是第一道边界。
- RPC 必须显式校验 `org_id + actor_user_id`。
- 新租户表必须纳入统一租户表清单或使用强制作用域 Repository。
- RLS/owner/FORCE RLS 采用独立迁移和生产验证，不能只写 `ENABLE RLS`。

## 12. API 与兼容

新增 API 使用 `/api/runtime/v1`：

- Session：create/get/build/pause/resume/close/events/capabilities
- Config：resolved/source/explain
- Skill：catalog/publish/install/uninstall/bindings
- MCP：catalog/configure/test/health/revoke
- Goal：create/get/pause/resume/cancel

响应保持项目统一结构：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": {
    "request_id": "uuid",
    "policy_revision": 12
  }
}
```

旧 Chat、Media 和 WeCom API 在迁移期保持不变，由 Adapter 转入 Session Command Gateway。

## 13. 计划代码边界

沿用既有 `backend/services/agent_runtime/` 目标目录，不新增同义
`backend/services/runtime/`：

```text
backend/services/agent_runtime/
├── session/
├── definition/
├── policy/
├── capability/
├── extensions/
├── goals/
├── events/
├── projection/
└── compatibility/
```

关键接入点：

- `services/conversation_runtime.py`
- `services/conversation_execution.py`
- `services/handlers/chat/actor_enqueue.py`
- `services/wecom/actor_enqueue.py`
- `services/handlers/chat/execution_engine.py`
- `services/agent/tool_loop_executor.py`
- `core/org_scoped_db.py`
- `api/routes/ws.py`
- `frontend/src/contexts/WebSocketContext.tsx`
- `frontend/src/hooks/useWebSocket.ts`

前端管理界面的最终组件结构在 UI 设计阶段确定；技术设计只冻结 API 和 Store 边界。

## 14. 边界场景

| 场景 | 处理 |
|---|---|
| 企业停用 | 强制失效企业全部活动 Build |
| 员工被移除 | 取消企业 Run；个人 Session 保留 |
| 企业切换 | HTTP/WS/缓存/订阅/Workspace 原子切换 |
| Skill 普通更新 | 旧 Build 固定旧版本 |
| Skill 安全撤回 | 强制失效引用版本 |
| MCP 断线 | 有界退避；写操作不自动重试 |
| MCP Schema 变化 | 建立新 revision |
| Goal 中服务重启 | 从 Event sequence 和 Goal round 恢复 |
| Build 并发 | advisory lock + fingerprint 幂等 |
| 配置冲突 | 返回来源与拒绝原因 |
| 散客加入企业 | 个人资产不迁移，只增加企业上下文 |
| 用户离开企业 | 企业共享能力失效，个人私有能力保留 |
| 外部动作结果未知 | 进入 unknown/reconcile，不重复提交 |

## 15. 实施波次

1. 迁移账本、租户边界和 WebSocket 企业切换修复。
2. 多层 Policy/Config Resolver 与来源解释。
3. AgentDefinition revision、Session Build 和 Capability Snapshot。
4. Skill Catalog、企业共享、推荐/自动/强制安装。
5. MCP Catalog、Gateway、连接生命周期和凭证隔离。
6. Goal/Interaction/SubRun 持久状态。
7. Web 入口 shadow build 和 Actor Bridge。
8. 企业微信接入统一 Session Runtime。
9. ToolBridge、Memory 和 Extension 生命周期收口。
10. 管理端/个人端 UI。
11. Projection、恢复、灰度和旧旁路退出。

每一波独立完成设计确认、实现、测试覆盖、审查、灰度和回滚验证。

当前实施状态（2026-07-23）：第 1 波的 WebSocket 连接身份、任务订阅边界、企业
切换清理、本地/Redis 消息投递复合租户键，以及生产者 `org_id` 贯通已完成。
Tool Confirm 与 Steer 交互等待键的用户/企业绑定也已完成；WebSocket 租户隔离
通过带 TTL 的 Redis 短期队列贯通 Web Worker 与 Conversation Actor，并保留
本地 Event 快速路径；进入最终审查与部署前验证。迁移账本和数据库纵深防御仍未开始。

## 16. 部署与回滚

- 新表和新字段先 expand，旧链保持可运行。
- Runtime 先 shadow write/compare，不产生重复外部副作用。
- 按企业和用户灰度。
- MCP、Skill、Goal 能力可分别关闭。
- 已产生事实的新表回滚时停写保留，禁止直接 DROP。
- 数据库迁移 rollback 只允许删除空表或无事实的新增对象。
- 切换前必须完成 Actor drain、schema/checksum 校验和回放一致性门禁。

## 17. 验收门禁

- 同一输入的新旧上下文和 Capability 对账可解释。
- Session/Run/Action 单终态所有权成立。
- 企业、员工、散客作用域测试全部通过。
- 企业切换后不存在旧 WS 连接和订阅映射。
- Extension 不扩权，子 Agent 不扩权。
- 外部 unknown action 不重复提交。
- Crash Recovery 能从 PostgreSQL 恢复。
- 所有新增文件不超过 500 行、函数不超过 120 行、复杂度不超过 15。
- 迁移、rollback、API、函数索引和项目总览同步更新。

## 18. 设计自检

- [x] 项目上下文、现有调用链和生产状态已核验
- [x] 方案 A/B 已比较并选择 A
- [x] 多角色架构评审已完成
- [x] 多租户企业差异已明确
- [x] Skill/MCP/Goal 已进入第一阶段总体规划
- [x] 与既有 `agent_*` 设计完成冲突消解
- [x] 边界、迁移、回滚和可观测性已纳入
- [x] 未修改业务代码或数据库
