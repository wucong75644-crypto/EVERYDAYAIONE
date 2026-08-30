# AI 任务理解与定时任务 ChangeSet 适配器

版本：`planner.v1` / `changeset.v1`  
范围：第二批，仅接入 `scheduled_tasks`

## 现状与边界

第一批已合入 `origin/main`：`a981ea39` 包含 `3abb4c2c feat: establish AI ChangeSet transaction kernel`。
本批复用 `ChangeSetAdapter`、`ChangeSetRepository`、`ChangeSetService.confirm/resume_committing`、
`change_sets/change_checks/change_events` 和现有 ChangeSet DTO；没有创建第二套 ChangeSet 表或状态机。

旧 `scheduled_task_drafts`、`scheduled_task_preflight_runs` 和聊天表单 RPC 仍保留，
只服务已经打开的旧表单。新入口 `POST /scheduled-tasks/changesets` 和聊天中的暂停/恢复/删除
不写旧 drafts，也不直接更新 `scheduled_tasks`。

## Planner Framework

`backend/services/planner/` 提供：

- `CapabilityDescriptor`：工具输入/输出 schema、读写属性、风险、权限、执行模式和只读预检能力。
- `CapabilityRegistry`：从当前工具 schema 建立能力事实源；未注册工具默认拒绝。
- `PlanCandidate` / `PlanStep`：目标、输入/输出契约、步骤、候选工具、验证条件和风险信息。
- `PlanValidator`：校验工具白名单、步骤范围、执行模式和 JSON 参数类型。
- `PlanRelease`：包含候选计划、能力范围、工具策略、策略版本和计划版本的冻结快照。
- `validate_runtime_tool`：运行时再次校验固化工具策略和 Registry，避免把计划工具列表当成授权来源。

定时任务的 create/update 继续复用已验证的 `ScheduledTaskAgent(execution_mode="preflight")`。
pause/resume/delete 使用确定性只读资源检查，不运行完整 Agent 试跑；删除仍按高风险策略要求确认。

## ChangeSet 数据流

```text
请求 → ScheduledTaskChangeAdapter.resolve/normalize
     → Capability Registry + PlanValidator
     → authorize → validate → semantic diff
     → ScheduledTaskAgent 只读预检（或低成本确定性检查）
     → ChangeSet draft → resolving → proposed → validating → preflighting
     → awaiting_approval → confirm → committing
     → commit_scheduled_task_changeset（锁定任务并校验 revision）
     → applied / conflicted / failed
```

`scheduled_tasks.revision` 由迁移 249 增加，适配器 RPC 在锁内比较 `p_base_revision`，
再以固定字段更新任务并递增版本。`scheduled_task_change_receipts` 仅保存适配器幂等回执，
不是 ChangeSet 表；重复提交按组织和幂等键回放，不重复写任务。

## 新 API / DTO

```http
POST /api/scheduled-tasks/changesets
Content-Type: application/json
```

```json
{
  "operation": "update",
  "task_id": "task-uuid",
  "idempotency_key": "ui-request-20260830-001",
  "definition": {
    "name": "每日销售日报",
    "prompt": "查询昨日销售并推送摘要",
    "schedule_type": "daily",
    "cron_expr": "0 9 * * *",
    "timezone": "Asia/Shanghai",
    "push_target": {"type": "web", "user_id": "user-uuid"},
    "next_run_at": "2030-01-01T01:00:00+00:00"
  }
}
```

返回 `ChangeSetDTO`，其中稳定包含：

```json
{
  "status": "awaiting_approval",
  "base_revision": "7",
  "diff": {
    "frequency": {"before": "每天 09:00", "after": "每天 10:00", "changed": true},
    "time": {"before": "0 9 * * *", "after": "0 10 * * *", "changed": true},
    "task_instruction": {"before": "旧指令", "after": "新指令", "changed": true},
    "tool_scope": {"before": ["erp_agent"], "after": ["erp_agent"], "changed": false},
    "data_scope": {"before": {"kind": "task_prompt"}, "after": {"kind": "task_prompt"}, "changed": false},
    "recipient": {"before": {"type": "web"}, "after": {"type": "web"}, "changed": false},
    "next_run_at": {"before": "...", "after": "...", "changed": true}
  },
  "risk": {"version": "default.v1", "risk_level": "high", "requires_approval": true, "reasons": ["tool_scope_expanded"]},
  "plan": {"release_id": "...", "plan_version": "planner.v1", "capability_names": ["erp_agent"], "tool_policy": {}},
  "checks": [{"check_type": "preflight", "status": "passed"}],
  "approval_actions": [{"action": "confirm", "method": "POST", "path": "/api/change-sets/.../confirm", "enabled": true}],
  "result": {"status": "awaiting_approval", "committed_revision": null}
}
```

确认使用：

```http
POST /api/change-sets/{change_set_id}/confirm
```

过期草案、重复确认、业务 revision 变化分别返回过期/幂等回放/`conflicted`，
都不会覆盖更新后的任务。

## 兼容、迁移与回滚

- 迁移：`249_scheduled_task_changeset_adapter.sql`；执行后新任务版本从 `1` 开始，历史任务从 `0` 开始。
- 兼容：`POST /scheduled-tasks/drafts`、旧聊天表单 `scheduled_task_create/update/confirm` 保持原路径，
  仅用于已打开表单的收尾；新 ChangeSet 入口不双写 drafts。
- 回滚：`rollback/249_scheduled_task_changeset_adapter_rollback.sql` 仅删除适配器回执、触发器和 revision；
  回滚前应先停止新入口并确认没有未完成的适配器回执。第一批 248 的安全回滚仍独立执行。
