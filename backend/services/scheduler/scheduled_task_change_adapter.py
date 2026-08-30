"""scheduled_tasks 的 ChangeSet 业务适配器。

该模块是定时任务新入口的唯一业务写入边界。所有真实写入都进入
``commit_scheduled_task_changeset`` 这个固定参数 RPC；通用 ChangeSet
内核和 Planner 都不会根据模型 JSON 生成 SQL。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from services.changeset.contracts import (
    AuthorizationResult,
    AuthorizeRequest,
    ChangeSetContext,
    ChangeSetAdapter,
    CommitRequest,
    CommitResult,
    DiffRequest,
    DiffResult,
    NormalizeRequest,
    NormalizeResult,
    PreflightRequest,
    PreflightResult,
    RenderRequest,
    RenderResult,
    ResolveRequest,
    ResolveResult,
    RestoreRequest,
    RestoreResult,
    ValidateRequest,
    ValidationResult,
)
from services.changeset.repository import ChangeSetRepository
from services.changeset.risk import DefaultRiskPolicy
from services.changeset.service import ChangeSetService
from services.planner import (
    CapabilityRegistry,
    PlanCandidate,
    PlanRelease,
    PlanStep,
    PlannerFramework,
)
from services.scheduler.cron_utils import (
    calc_next_run,
    compose_cron,
    parse_cron_readable,
    validate_cron,
)


TASK_RESOURCE_TYPE = "scheduled_task"
TASK_OPERATIONS = frozenset({"create", "update", "pause", "resume", "delete"})
TASK_FIELDS = (
    "name", "prompt", "cron_expr", "schedule_type", "weekdays", "day_of_month",
    "run_at", "timezone", "push_target", "template_file", "max_credits",
    "retry_count", "timeout_sec", "next_run_at", "status", "execution_policy",
    "plan_snapshot", "data_scope",
)


class ScheduledTaskChangeError(ValueError):
    def __init__(self, message: str, *, status_code: int = 422, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details or []


def task_snapshot(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {key: row[key] for key in TASK_FIELDS if key in row}


def _schedule_label(snapshot: Mapping[str, Any]) -> str:
    cron = snapshot.get("cron_expr")
    if cron:
        try:
            return parse_cron_readable(str(cron))
        except Exception:
            return str(cron)
    return "单次执行" if snapshot.get("schedule_type") == "once" else str(snapshot.get("schedule_type") or "未设置")


def _tool_scope(snapshot: Mapping[str, Any]) -> list[str]:
    policy = snapshot.get("execution_policy") or {}
    return sorted({str(name) for name in policy.get("allowed_tools", []) if str(name).strip()})


def build_scheduled_task_diff(
    base: Mapping[str, Any], proposed: Mapping[str, Any], *, operation: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """生成同时适合 UI 展示和机器审计的语义 Diff。"""
    paths = {
        "name": "name", "prompt": "prompt", "cron_expr": "schedule",
        "schedule_type": "schedule", "weekdays": "schedule", "day_of_month": "schedule",
        "run_at": "schedule", "timezone": "schedule", "push_target": "recipient",
        "template_file": "template_file", "max_credits": "limits", "retry_count": "limits",
        "timeout_sec": "limits", "next_run_at": "next_run_at", "status": "status",
        "execution_policy": "tool_scope", "data_scope": "data_scope",
    }
    patch: list[dict[str, Any]] = []
    changed: dict[str, Any] = {}
    for key in TASK_FIELDS:
        before = base.get(key)
        after = proposed.get(key)
        if before == after:
            continue
        patch.append({"op": "replace" if key in base else "add", "path": f"/{key}", "value": after})
        changed[key] = {"before": before, "after": after, "category": paths.get(key, key)}

    def pair(key: str, before: Any, after: Any) -> dict[str, Any]:
        return {"before": before, "after": after, "changed": before != after}

    diff = {
        "version": "scheduled_task.diff.v1",
        "operation": operation,
        "fields": changed,
        "frequency": pair("frequency", _schedule_label(base), _schedule_label(proposed)),
        "time": pair("time", base.get("run_at") or base.get("cron_expr"), proposed.get("run_at") or proposed.get("cron_expr")),
        "task_instruction": pair("task_instruction", base.get("prompt"), proposed.get("prompt")),
        "tool_scope": pair("tool_scope", _tool_scope(base), _tool_scope(proposed)),
        "data_scope": pair("data_scope", base.get("data_scope", {"kind": "task_prompt"}), proposed.get("data_scope", {"kind": "task_prompt"})),
        "recipient": pair("recipient", base.get("push_target"), proposed.get("push_target")),
        "next_run_at": pair("next_run_at", base.get("next_run_at"), proposed.get("next_run_at")),
    }
    return patch, diff


class ScheduledTaskChangeAdapter(ChangeSetAdapter):
    def __init__(self, db: Any, *, user_id: str, org_id: str) -> None:
        self.db = db
        self.user_id = str(user_id)
        self.org_id = str(org_id)

    async def resolve(self, request: ResolveRequest) -> ResolveResult:
        return ResolveResult(proposed_snapshot=dict(request.context.proposed_snapshot))

    async def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        value = dict(request.proposed_snapshot)
        operation = request.context.operation
        if operation in {"create", "update"}:
            for key in ("name", "prompt", "timezone", "push_target"):
                if not value.get(key):
                    raise ScheduledTaskChangeError(f"定时任务缺少 {key}")
            if not isinstance(value.get("push_target"), Mapping):
                raise ScheduledTaskChangeError("推送目标必须是对象")
            if not value.get("schedule_type"):
                raise ScheduledTaskChangeError("定时任务缺少 schedule_type")
            schedule_type = str(value["schedule_type"]).lower().strip()
            if schedule_type not in {"once", "daily", "weekly", "monthly", "cron"}:
                raise ScheduledTaskChangeError(f"不支持的 schedule_type: {schedule_type}")
            value["schedule_type"] = schedule_type
            if schedule_type == "once":
                raw_run_at = value.get("run_at")
                if not raw_run_at:
                    raise ScheduledTaskChangeError("单次任务缺少 run_at")
                try:
                    run_at = datetime.fromisoformat(str(raw_run_at).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ScheduledTaskChangeError("run_at 格式无效") from exc
                if run_at.tzinfo is None:
                    raise ScheduledTaskChangeError("run_at 必须包含时区")
                value["run_at"] = run_at.isoformat()
                value["cron_expr"] = None
                value["next_run_at"] = run_at.astimezone(timezone.utc).isoformat()
            else:
                cron_expr = value.get("cron_expr")
                if not cron_expr and schedule_type != "cron":
                    try:
                        cron_expr = compose_cron(
                            schedule_type=schedule_type,
                            time_str=str(value.get("time_str") or ""),
                            weekdays=value.get("weekdays"),
                            day_of_month=value.get("day_of_month"),
                        )
                    except ValueError as exc:
                        raise ScheduledTaskChangeError(str(exc)) from exc
                if not cron_expr or not validate_cron(str(cron_expr)):
                    raise ScheduledTaskChangeError("cron_expr 无效")
                value["cron_expr"] = str(cron_expr)
                value["run_at"] = None
                try:
                    value["next_run_at"] = calc_next_run(
                        value["cron_expr"], str(value.get("timezone") or "Asia/Shanghai"),
                    ).isoformat()
                except Exception as exc:
                    raise ScheduledTaskChangeError("无法计算下次执行时间") from exc
            if "data_scope" not in value:
                value["data_scope"] = request.context.base_snapshot.get(
                    "data_scope", {"kind": "task_prompt"},
                )
            if not isinstance(value["data_scope"], Mapping):
                raise ScheduledTaskChangeError("数据范围必须是对象")
        if operation == "resume" and not value.get("next_run_at"):
            raise ScheduledTaskChangeError("恢复任务缺少 next_run_at")
        return NormalizeResult(proposed_snapshot=value, patch=())

    async def authorize(self, request: AuthorizeRequest) -> AuthorizationResult:
        permission = {
            "create": "task.create", "update": "task.edit", "pause": "task.edit",
            "resume": "task.edit", "delete": "task.delete",
        }.get(request.context.operation)
        if not permission:
            return AuthorizationResult(False, {}, ("unsupported_operation",))
        from services.permissions.checker import check_permission

        resource = dict(request.context.base_snapshot)
        if request.context.operation == "create":
            resource = {**request.context.proposed_snapshot, "user_id": self.user_id, "org_id": self.org_id}
        allowed = await check_permission(self.db, self.user_id, self.org_id, permission, resource)
        reasons: list[str] = []
        if not allowed:
            reasons.append(f"permission_denied:{permission}")
        target = request.context.proposed_snapshot.get("push_target") or {}
        if request.context.operation in {"create", "update"} and not await self._is_self_target(target):
            can_push = await check_permission(self.db, self.user_id, self.org_id, "task.push_to_others", resource)
            if not can_push:
                reasons.append("permission_denied:task.push_to_others")
        return AuthorizationResult(
            allowed=not reasons,
            policy_snapshot={"version": "permission.v1", "permissions_checked": [permission], "actor_id": request.actor_id},
            reasons=tuple(reasons),
        )

    async def validate(self, request: ValidateRequest) -> ValidationResult:
        context = request.context
        if context.operation not in TASK_OPERATIONS:
            return ValidationResult(False, {}, ("unsupported_operation",))
        if context.operation != "create":
            current = self.db.table("scheduled_tasks").select("id, org_id, revision, status").eq(
                "id", context.resource_id,
            ).eq("org_id", self.org_id).limit(1).execute()
            row = (current.data or [None])[0]
            if not row:
                return ValidationResult(False, {}, ("task_not_found",))
            if str(row.get("revision", 0)) != str(context.base_revision):
                return ValidationResult(False, {"current_revision": str(row.get("revision", 0))}, ("base_revision_conflict",))
            if row.get("status") == "running" and context.operation in {"update", "pause", "resume", "delete"}:
                return ValidationResult(False, {}, ("task_running",))
        if context.operation == "delete" and context.proposed_snapshot:
            return ValidationResult(True, {"destructive": True})
        return ValidationResult(True, {"validated": True})

    async def diff(self, request: DiffRequest) -> DiffResult:
        patch, diff = build_scheduled_task_diff(
            request.context.base_snapshot, request.context.proposed_snapshot,
            operation=request.context.operation,
        )
        return DiffResult(patch=patch, diff=diff)

    async def preflight(self, request: PreflightRequest) -> PreflightResult:
        context = request.context
        if context.operation not in {"create", "update"}:
            current = self.db.table("scheduled_tasks").select("id, revision, status").eq(
                "id", context.resource_id,
            ).eq("org_id", self.org_id).limit(1).execute()
            row = (current.data or [None])[0]
            passed = bool(row and str(row.get("revision", 0)) == str(context.base_revision))
            return PreflightResult(
                passed=passed,
                result={"mode": "deterministic_read_only", "full_run": False},
                reasons=() if passed else ("resource_changed",),
            )

        release = context.plan_snapshot or {}
        tool_policy = dict(release.get("tool_policy") or context.tool_policy_snapshot or {})
        execution_policy = {
            **tool_policy,
            "allowed_tools": tool_policy.get("allowed_tools", []),
            "required_tools": tool_policy.get("required_tools", []),
        }
        task = {
            "id": context.resource_id,
            "org_id": self.org_id,
            "user_id": self.user_id,
            **dict(context.proposed_snapshot),
            "execution_policy": execution_policy,
            "plan_snapshot": release,
        }
        try:
            from services.agent.scheduled_task_agent import ScheduledTaskAgent
            result = await ScheduledTaskAgent(self.db, task, execution_mode="preflight").execute()
        except Exception as exc:
            return PreflightResult(False, {"mode": "scheduled_task_agent", "error": str(exc)[:500]}, ("preflight_exception",))
        gate = result.completion_gate or {}
        passed = result.status == "success" and bool(gate.get("passed"))
        return PreflightResult(
            passed=passed,
            result={
                "mode": "scheduled_task_agent", "full_run": True,
                "status": result.status, "summary": result.summary or result.text[:500],
                "completion_gate": gate, "tools_called": result.tools_called,
            },
            reasons=() if passed else tuple(gate.get("reasons", [])) or (result.error_message or "preflight_failed",),
        )

    async def commit(self, request: CommitRequest) -> CommitResult:
        context = request.context
        response = self.db.rpc("commit_scheduled_task_changeset", {
            "p_change_set_id": context.id,
            "p_org_id": self.org_id,
            "p_user_id": self.user_id,
            "p_task_id": context.resource_id,
            "p_operation": context.operation,
            "p_base_revision": int(context.base_revision),
            "p_definition": dict(context.proposed_snapshot),
            "p_idempotency_key": request.idempotency_key,
        }).execute()
        data = response.data if response else None
        if not isinstance(data, Mapping):
            raise RuntimeError("SCHEDULED_TASK_CHANGE_RESULT_INVALID")
        if data.get("outcome") == "conflict":
            return CommitResult(False, None, conflict=dict(data))
        if data.get("outcome") not in {"created", "updated", "paused", "resumed", "deleted", "duplicate"}:
            raise RuntimeError(str(data.get("error") or "SCHEDULED_TASK_CHANGE_FAILED"))
        return CommitResult(
            applied=True,
            new_revision=str(data.get("new_revision")) if data.get("new_revision") is not None else None,
            receipt={"operation": context.operation, "task_id": context.resource_id, "outcome": data.get("outcome"), "task": data.get("task")},
        )

    async def restore(self, request: RestoreRequest) -> RestoreResult:
        return RestoreResult(False, None, conflict={"reason": "scheduled_task_restore_not_supported"})

    async def render(self, request: RenderRequest) -> RenderResult:
        diff = request.context.diff
        changed = [str(value.get("category")) for value in (diff.get("fields") or {}).values() if isinstance(value, Mapping)]
        title = f"定时任务{request.context.operation}变更"
        summary = f"将变更定时任务「{request.context.proposed_snapshot.get('name') or request.context.base_snapshot.get('name') or request.context.resource_id}」"
        return RenderResult(title=title, summary=summary, sections=[
            {"key": "diff", "title": "变更内容", "value": diff},
            {"key": "changed_categories", "title": "变更类别", "value": sorted(set(changed))},
        ])

    async def _is_self_target(self, target: Any) -> bool:
        if not isinstance(target, Mapping):
            return False
        if target.get("type") == "web":
            return target.get("user_id") == self.user_id
        if target.get("type") == "wecom_user" and target.get("wecom_userid"):
            result = self.db.table("wecom_user_mappings").select("wecom_userid").eq(
                "user_id", self.user_id,
            ).eq("org_id", self.org_id).eq(
                "wecom_userid", target["wecom_userid"],
            ).limit(1).execute()
            return bool(result.data)
        return False


class ScheduledTaskChangeSetService:
    """把 Planner + 适配器检查结果写入第一批 ChangeSet 内核。"""

    def __init__(self, db: Any, *, user_id: str, org_id: str) -> None:
        self.db = db
        self.user_id = str(user_id)
        self.org_id = str(org_id)
        self.adapter = ScheduledTaskChangeAdapter(db, user_id=user_id, org_id=org_id)

    async def propose(
        self,
        *,
        operation: str,
        proposed_snapshot: Mapping[str, Any],
        base_snapshot: Mapping[str, Any] | None = None,
        resource_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        operation = operation.lower().strip()
        if operation not in TASK_OPERATIONS:
            raise ScheduledTaskChangeError(f"不支持的定时任务操作: {operation}")
        base = task_snapshot(base_snapshot)
        resource_id = str(resource_id or uuid4())
        base_revision = str((base_snapshot or {}).get("revision", 0))
        context = ChangeSetContext(
            id=str(uuid4()), org_id=self.org_id, resource_type=TASK_RESOURCE_TYPE,
            resource_id=resource_id, operation=operation, base_revision=base_revision,
            base_snapshot=base, proposed_snapshot=dict(proposed_snapshot), patch=(), diff={},
            policy_snapshot={},
        )
        resolved = await self.adapter.resolve(ResolveRequest(context=context, intent=dict(proposed_snapshot)))
        normalized = await self.adapter.normalize(NormalizeRequest(context=context, proposed_snapshot=resolved.proposed_snapshot))
        normalized_snapshot = dict(normalized.proposed_snapshot)

        external = not await self.adapter._is_self_target(normalized_snapshot.get("push_target"))
        assessment = DefaultRiskPolicy().assess(
            resource_type=TASK_RESOURCE_TYPE,
            operation=operation,
            context={
                "destructive": operation == "delete",
                "external_effect": operation == "delete" or external,
                "affects_many": normalized_snapshot.get("push_target", {}).get("type") == "multi",
                "tool_scope_expanded": operation == "create" or bool(
                    set(_tool_scope(normalized_snapshot)) - set(_tool_scope(base))
                ),
            },
        )
        release = await self._build_release(operation, normalized_snapshot, assessment.as_snapshot())
        # 工具范围由发布计划决定；对 update 重新比较一次，避免模型在计划阶段
        # 扩大工具白名单却仍沿用低风险标签。
        released_tools = set(release.tool_policy.get("allowed_tools", []))
        final_assessment = DefaultRiskPolicy().assess(
            resource_type=TASK_RESOURCE_TYPE,
            operation=operation,
            context={
                "destructive": operation == "delete",
                "external_effect": operation == "delete" or external,
                "affects_many": normalized_snapshot.get("push_target", {}).get("type") == "multi",
                "tool_scope_expanded": operation == "create" or bool(
                    released_tools - set(_tool_scope(base))
                ),
            },
        )
        assessment = final_assessment
        release = replace(
            release,
            candidate={**dict(release.candidate), "risk_info": assessment.as_snapshot()},
        )
        # 计划发布后，执行策略作为 ChangeSet 候选的一部分固化；提交 RPC
        # 只会消费这个明确字段，不会从模型输出临时推导权限。
        if operation in {"create", "update"}:
            normalized_snapshot["execution_policy"] = dict(release.tool_policy)
            normalized_snapshot["plan_snapshot"] = release.as_dict()
        context = replace(
            context,
            proposed_snapshot=normalized_snapshot,
            plan_snapshot=release.as_dict(),
            tool_policy_snapshot=release.tool_policy,
        )
        authorization = await self.adapter.authorize(AuthorizeRequest(context=context, actor_id=self.user_id, actor_type="user"))
        validation = await self.adapter.validate(ValidateRequest(context=context))
        if not authorization.allowed:
            raise ScheduledTaskChangeError("无权执行定时任务变更", status_code=403, details=list(authorization.reasons))
        if not validation.passed:
            raise ScheduledTaskChangeError("定时任务校验失败", details=list(validation.reasons))
        diff = await self.adapter.diff(DiffRequest(context=context))
        context = replace(
            context,
            patch=diff.patch,
            diff=diff.diff,
            policy_snapshot={**assessment.as_snapshot(), **authorization.policy_snapshot},
            check_summary={"authorization": authorization.reasons, "validation": validation.result},
        )
        preflight = await self.adapter.preflight(PreflightRequest(context=context))
        check_summary = {
            "authorization": {"passed": authorization.allowed, "reasons": list(authorization.reasons)},
            "validation": {"passed": validation.passed, "result": dict(validation.result), "reasons": list(validation.reasons)},
            "preflight": {"passed": preflight.passed, "result": dict(preflight.result), "reasons": list(preflight.reasons)},
            "risk": assessment.as_snapshot(),
        }
        repo = ChangeSetRepository(self.db)
        row = repo.create({
            "org_id": self.org_id, "resource_type": TASK_RESOURCE_TYPE, "resource_id": resource_id,
            "operation": operation, "base_revision": base_revision, "base_snapshot": base,
            "proposed_snapshot": normalized_snapshot, "patch": list(diff.patch), "diff": dict(diff.diff),
            "risk_level": assessment.level.value, "policy_snapshot": context.policy_snapshot,
            "plan_snapshot": release.as_dict(), "tool_policy_snapshot": release.tool_policy,
            "check_summary": check_summary, "idempotency_key": idempotency_key or f"scheduled-task:{uuid4()}",
            "actor_id": self.user_id, "actor_type": "user",
            "audit_subject": {"user_id": self.user_id, "resource_type": TASK_RESOURCE_TYPE},
        })
        change_set_id = str(row["id"])
        service = ChangeSetService(repo)
        await self._transition(service, change_set_id, "draft", "resolving", "resolved")
        await self._transition(service, change_set_id, "resolving", "proposed", "proposed")
        await self._record(repo, change_set_id, "authorization", authorization.allowed, authorization.policy_snapshot, authorization.reasons)
        await self._transition(service, change_set_id, "proposed", "validating", "validation_started")
        await self._record(repo, change_set_id, "validation", validation.passed, validation.result, validation.reasons)
        await self._transition(service, change_set_id, "validating", "preflighting", "preflight_started")
        await self._record(repo, change_set_id, "preflight", preflight.passed, preflight.result, preflight.reasons,
                           status="passed" if preflight.passed and preflight.result.get("full_run") else "skipped" if preflight.passed else "failed")
        if not preflight.passed:
            await self._transition(service, change_set_id, "preflighting", "rejected", "preflight_rejected", {"reasons": list(preflight.reasons)})
        else:
            await self._transition(service, change_set_id, "preflighting", "awaiting_approval", "awaiting_approval")
        return self._with_projection(repo.get(change_set_id, self.org_id), repo)

    async def _build_release(self, operation: str, snapshot: Mapping[str, Any], risk: Mapping[str, Any]) -> PlanRelease:
        if operation in {"create", "update"}:
            from services.scheduler.scheduled_task_workflow import create_plan
            legacy_plan, legacy_policy = await create_plan(db=self.db, org_id=self.org_id, definition=dict(snapshot))
            candidate = PlanCandidate(
                target={"resource_type": TASK_RESOURCE_TYPE, "operation": operation},
                input_contract={"type": "scheduled_task_definition"},
                output_contract=legacy_plan.get("output_contract") or {},
                steps=tuple(PlanStep(
                    step_id=str(step.get("id") or f"step-{index}"), intent=str(step.get("intent") or "执行任务"),
                    tools=tuple(step.get("tools") or []), required=bool(step.get("required", True)),
                    verification=str(step.get("verify") or ""),
                ) for index, step in enumerate(legacy_plan.get("steps") or [], start=1)),
                candidate_tools=tuple(sorted(legacy_policy.allowed_tools)),
                verification_conditions=tuple(legacy_plan.get("verification_conditions") or []),
                risk_info=dict(risk),
            )
            from config.chat_tools import get_core_tools
            framework = PlannerFramework(CapabilityRegistry.from_tool_schemas(get_core_tools(org_id=self.org_id)))
            # legacy_plan 已由 scheduled_task_workflow 的白名单校验过；这里再次经过通用 Registry。
            return framework.release(candidate, execution_mode="scheduled")
        candidate = PlanCandidate(
            target={"resource_type": TASK_RESOURCE_TYPE, "operation": operation},
            input_contract={"type": "scheduled_task_definition"}, output_contract={"result": "change_set"},
            steps=(), candidate_tools=(), verification_conditions=("提交时再次校验 revision",), risk_info=dict(risk),
        )
        return PlannerFramework(CapabilityRegistry()).release(candidate, execution_mode="scheduled")

    async def _transition(self, service: ChangeSetService, change_set_id: str, expected: str, target: str, event: str, payload: Mapping[str, Any] | None = None) -> None:
        repo = service.repository
        repo.transition(change_set_id=change_set_id, org_id=self.org_id, expected_status=expected, next_status=target,
                       actor_id=self.user_id, actor_type="user", event_type=event, payload=payload)

    async def _record(self, repo: ChangeSetRepository, change_set_id: str, key: str, passed: bool, result: Mapping[str, Any], reasons: Any, *, status: str | None = None) -> None:
        repo.record_check(change_set_id=change_set_id, org_id=self.org_id, check_type=key,
                          check_key=key, status=status or ("passed" if passed else "failed"),
                          input_data={}, result={**dict(result), "reasons": list(reasons)},
                          actor_id=self.user_id, actor_type="user")

    def _with_projection(self, row: dict[str, Any], repo: ChangeSetRepository) -> dict[str, Any]:
        row = dict(row)
        row["checks"] = repo.list_checks(str(row["id"]), self.org_id)
        return row


def build_change_set_approval_actions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    status = str(row.get("status"))
    actions: list[dict[str, Any]] = []
    if status == "awaiting_approval":
        actions.append({"action": "confirm", "enabled": True, "method": "POST", "path": f"/api/change-sets/{row.get('id')}/confirm"})
        actions.append({"action": "reject", "enabled": True, "method": "POST", "path": f"/api/change-sets/{row.get('id')}/cancel"})
    elif status in {"draft", "resolving", "proposed", "validating", "preflighting"}:
        actions.append({"action": "cancel", "enabled": True, "method": "POST", "path": f"/api/change-sets/{row.get('id')}/cancel"})
    return actions
