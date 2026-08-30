# ChangeSet 变更事务内核契约

版本：`changeset.v1`  
迁移标识：`248_change_sets.sql`  
实现提交：本任务尚未提交（按要求不提交、不推送、不部署）。

## 1. 边界

ChangeSet 是一次 AI 候选变更交易的事实记录，不是业务对象的通用替代表。业务表继续由业务模块维护；内核只保存候选快照、检查、状态和事件。`commit` / `restore` 必须由资源适配器在自己的事务中执行，内核不会根据 JSON 生成业务 SQL，也不会按 `resource_type` 反射更新任意表。

当前批次不接入 `scheduled_tasks`。现有 `scheduled_task_drafts`、`scheduled_task_preflight_runs`、`confirm_scheduled_task_draft` 和聊天表单 RPC 保持原状，聊天消息/表单仍只是其原有流程的投影。

## 2. 持久化表

### `change_sets`

一次变更交易一行。核心字段：

| 字段 | 说明 |
| --- | --- |
| `resource_type/resource_id/operation` | 业务适配器标识的资源和操作 |
| `base_revision/base_snapshot` | 适配器读取的提交基线；提交时必须再次校验版本 |
| `proposed_snapshot/patch/diff` | AI 候选、机器 patch 和业务语义 Diff |
| `risk_level/policy_snapshot` | 风险分级和冻结的授权/策略快照 |
| `plan_snapshot/tool_policy_snapshot/check_summary` | 为 Planner Framework 预留的标准快照字段 |
| `status` | 状态机当前状态 |
| `idempotency_key/expires_at` | 组织内幂等边界和过期时间 |
| `created_by/created_by_type/updated_by/audit_subject` | 审计主体 |
| `revision/committed_revision/error_*/conflict` | 并发、提交结果和可恢复失败信息 |

幂等唯一键是 `(org_id, idempotency_key)`。相同幂等键但候选主体不同会拒绝为冲突，不覆盖原交易。

### `change_checks`

记录 `authorization`、`validation`、`preflight`、`approval`、`conflict`、`commit`、`restore` 节点。每次记录都包含 `input`、`result`、`status`、检查主体和时间；通过 RPC 与事件一起写入。

### `change_events`

按 `(change_set_id, sequence)` 唯一排序的追加式时间线。状态迁移和检查节点都写事件，前端恢复不依赖聊天消息或浏览器状态。

三张表均纳入 `OrgScopedDB.TENANT_TABLES`，启用并强制 RLS，仅允许现有 `everydayai` 服务账号访问。

## 3. 状态契约

正常路径：

```text
draft → resolving → proposed → validating → preflighting
      → awaiting_approval → committing → applied
```

终态：`cancelled`、`rejected`、`failed`、`expired`、`conflicted`，以及成功终态 `applied`。状态迁移由数据库 `transition_change_set` 在行锁内校验期望状态并追加事件；过期优先于人工后续操作，`committing` 不被过期抢占。

失败恢复不把 `failed` 改回可执行状态，而是由 `recover_failed` 复制基线/候选创建新的 `draft`，原失败交易和时间线保持不可变。进程在业务提交后崩溃而停留 `committing` 时，由执行器调用 `resume_committing`，适配器必须按幂等键安全重试。

## 4. 适配器接口

接口位于 `backend/services/changeset/contracts.py`，所有方法均为异步：

```text
resolve(ResolveRequest) -> ResolveResult
normalize(NormalizeRequest) -> NormalizeResult
authorize(AuthorizeRequest) -> AuthorizationResult
validate(ValidateRequest) -> ValidationResult
diff(DiffRequest) -> DiffResult
preflight(PreflightRequest) -> PreflightResult
commit(CommitRequest) -> CommitResult
restore(RestoreRequest) -> RestoreResult
render(RenderRequest) -> RenderResult
```

`ChangeSetContext` 向适配器提供资源标识、基线/候选快照、patch、Diff、策略、计划和检查摘要。`CommitRequest` 额外提供 ChangeSet 幂等键；适配器必须在业务事务内锁定资源、校验 `base_revision`，版本不一致返回 `conflict`，不能只比较草案哈希。`restore` 也必须使用业务自己的恢复语义。

## 5. 风险策略

`DefaultRiskPolicy` 提供 `low/medium/high/critical` 四级 `RiskAssessment`：只读为 low，普通持久化变更为 medium，外部影响或破坏性操作为 high，影响多对象为 critical。high/critical 默认需要一次确认；策略版本和理由冻结到 `policy_snapshot`。本批不实现多人审批、审批链编排或可视化编排器。

## 6. 前端 DTO/API

前端类型位于 `frontend/src/types/changeset.ts`，客户端位于 `frontend/src/services/changeSet.ts`。稳定端点：

| 方法 | 路径 | 语义 |
| --- | --- | --- |
| `GET` | `/api/change-sets/{id}` | 返回 ChangeSet 及检查节点 |
| `GET` | `/api/change-sets/{id}/timeline` | 返回按 `sequence` 排序的完整事件 |
| `POST` | `/api/change-sets/{id}/cancel` | 发起人取消，已取消请求幂等返回 |
| `POST` | `/api/change-sets/{id}/recover` | 从 `failed` 创建新的 `draft`，可带幂等键 |

详情 DTO 至少稳定包含：`id/resource_type/resource_id/operation/status/risk_level/base_revision/proposed_snapshot/patch/diff/policy_snapshot/plan_snapshot/tool_policy_snapshot/check_summary/expires_at/audit_subject/revision/checks`。时间线 DTO 为 `{ change_set_id, events[] }`，事件含 `sequence/event_type/from_status/to_status/actor/payload/created_at`。

确认/提交执行器使用 `ChangeSetService.confirm`；重复确认在 `applied` 直接回放，抢占失败者对 `committing/applied` 回放，不重复调用业务提交。定时任务旧确认 API 不改为该端点，第二批再实现适配器接入。

## 7. 迁移与回滚

`248_change_sets.sql` 创建三表、索引、RLS 和三个通用 RPC。`248_change_sets_rollback.sql` 在任一 ChangeSet 数据存在时拒绝执行，只有审计数据已迁移/清理并由运维确认后才会删除函数和三表；代码侧需同步移除 `TENANT_TABLES` 中的三项。回滚不会触碰 `scheduled_task_drafts`、`scheduled_tasks` 或任何旧聊天表单函数。

第二批依赖入口：`changeset.v1@248_change_sets.sql`、`ChangeSetAdapter`、`ChangeSetService.confirm/resume_committing`、上述 GET/timeline DTO。第三批可在不改变通用表的前提下增加适配器注册、Planner 运行器和更多资源类型。
