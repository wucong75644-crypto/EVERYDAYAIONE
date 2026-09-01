from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.changeset.contracts import ChangeSetContext, CommitRequest, PreflightResult
from services.changeset.repository import ChangeSetIdempotencyConflict
from services.changeset.risk import RiskLevel
from services.planner import PlanCandidate, PlanRelease
from services.scheduler.scheduled_task_change_adapter import (
    ScheduledTaskChangeError,
    ScheduledTaskChangeAdapter,
    ScheduledTaskChangeSetService,
    build_scheduled_task_diff,
)


class _Db:
    def __init__(self, rows=None, rpc_data=None):
        self.rows = rows or []
        self.rpc_data = rpc_data or {"outcome": "updated", "new_revision": 3}

    def table(self, _name):
        builder = MagicMock()
        for method in ("select", "eq", "limit"):
            getattr(builder, method).return_value = builder
        builder.execute.return_value = SimpleNamespace(data=deepcopy(self.rows))
        return builder

    def rpc(self, _name, _params):
        result = MagicMock()
        result.execute.return_value = SimpleNamespace(data=self.rpc_data)
        return result


class _Repo:
    def __init__(self):
        self.row = {
            "id": "cs1", "org_id": "org1", "resource_type": "scheduled_task",
            "resource_id": "task1", "operation": "pause", "base_revision": "2",
            "base_snapshot": {}, "proposed_snapshot": {}, "patch": [], "diff": {},
            "risk_level": "low", "policy_snapshot": {}, "plan_snapshot": {},
            "tool_policy_snapshot": {}, "check_summary": {}, "status": "draft",
            "idempotency_key": "key", "revision": 0,
        }
        self.checks = []

    def create(self, payload):
        self.row = {
            **self.row, **payload, "id": "cs1", "status": "draft",
            "created_by": payload.get("actor_id"),
        }
        return deepcopy(self.row)

    def get(self, _id, _org):
        return deepcopy(self.row)

    def get_by_idempotency_key(self, *, org_id, idempotency_key):
        if self.row.get("org_id") == org_id and self.row.get("idempotency_key") == idempotency_key:
            return deepcopy(self.row)
        return None

    def transition(self, *, next_status, **_kwargs):
        self.row["status"] = next_status
        return deepcopy(self.row)

    def enrich_proposal(self, **kwargs):
        self.row.update({
            "proposed_snapshot": deepcopy(kwargs["proposed_snapshot"]),
            "patch": deepcopy(kwargs["patch"]),
            "diff": deepcopy(kwargs["diff"]),
            "risk_level": kwargs["risk_level"],
            "policy_snapshot": deepcopy(kwargs["policy_snapshot"]),
            "plan_snapshot": deepcopy(kwargs["plan_snapshot"]),
            "tool_policy_snapshot": deepcopy(kwargs["tool_policy_snapshot"]),
            "check_summary": deepcopy(kwargs["check_summary"]),
        })
        return deepcopy(self.row)

    def record_check(self, **kwargs):
        self.checks.append(kwargs)
        return kwargs

    def list_checks(self, *_args):
        return deepcopy(self.checks)


class _RacingRepo(_Repo):
    def get_by_idempotency_key(self, *, org_id, idempotency_key):
        return None

    def create(self, payload):
        self.row = {
            **self.row, **payload, "id": "cs1", "status": "draft",
            "created_by": payload.get("actor_id"),
        }
        raise ChangeSetIdempotencyConflict(
            "idempotency key is already bound to another ChangeSet",
            existing=self.row,
        )


def _release():
    return PlanRelease.create(
        PlanCandidate(
            target={}, input_contract={}, output_contract={}, steps=(), candidate_tools=(),
            verification_conditions=(), risk_info={},
        ), tool_policy={"allowed_tools": [], "required_tools": []}, policy_version="policy.v1",
    )


def test_diff_has_machine_patch_and_required_user_sections():
    patch, diff = build_scheduled_task_diff(
        {"name": "旧", "prompt": "旧指令", "cron_expr": "0 9 * * *", "push_target": {"type": "web"}},
        {"name": "新", "prompt": "新指令", "cron_expr": "0 10 * * *", "push_target": {"type": "web"}},
        operation="update",
    )
    assert patch
    for key in ("frequency", "time", "task_instruction", "tool_scope", "data_scope", "recipient", "next_run_at"):
        assert key in diff
    assert diff["frequency"]["changed"] is True


@pytest.mark.asyncio
async def test_adapter_commit_uses_fixed_rpc_and_returns_conflict():
    db = _Db(rpc_data={"outcome": "conflict", "reason": "base_revision_mismatch", "current_revision": 4})
    adapter = ScheduledTaskChangeAdapter(db, user_id="u1", org_id="org1")
    context = ChangeSetContext(
        id="cs1", org_id="org1", resource_type="scheduled_task", resource_id="task1",
        operation="update", base_revision="3", base_snapshot={}, proposed_snapshot={"name": "n"},
        patch=(), diff={}, policy_snapshot={}, plan_snapshot={}, tool_policy_snapshot={},
    )
    result = await adapter.commit(CommitRequest(context=context, idempotency_key="k"))
    assert result.applied is False
    assert result.conflict["current_revision"] == 4


@pytest.mark.asyncio
async def test_duplicate_create_replays_without_replanning():
    repo = _Repo()
    db = _Db()
    service = ScheduledTaskChangeSetService(db, user_id="u1", org_id="org1")
    service._build_release = AsyncMock(return_value=_release())
    service.adapter.preflight = AsyncMock(return_value=PreflightResult(
        True, {"full_run": False, "mode": "deterministic_read_only"}, (),
    ))
    definition = {
        "name": "日报", "prompt": "查询淘宝销售额", "timezone": "Asia/Shanghai",
        "push_target": {"type": "web", "user_id": "u1"},
        "schedule_type": "daily", "cron_expr": "0 9 * * *",
    }
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo), \
         patch("services.permissions.checker.check_permission", new=AsyncMock(return_value=True)):
        first = await service.propose(
            operation="create", proposed_snapshot=definition, idempotency_key="same-key",
        )
        second = await service.propose(
            operation="create", proposed_snapshot=definition, idempotency_key="same-key",
        )

    assert first["id"] == second["id"] == "cs1"
    assert service._build_release.await_count == 1


@pytest.mark.asyncio
async def test_begin_persists_draft_before_background_planning():
    repo = _Repo()
    repo.row["idempotency_key"] = "other-key"
    db = _Db()
    service = ScheduledTaskChangeSetService(db, user_id="u1", org_id="org1")
    service._build_release = AsyncMock(return_value=_release())
    definition = {
        "name": "日报", "prompt": "查询淘宝销售额", "timezone": "Asia/Shanghai",
        "push_target": {"type": "web", "user_id": "u1"},
        "schedule_type": "daily", "cron_expr": "0 9 * * *",
    }
    scheduled: list[object] = []

    def schedule(coro):
        scheduled.append(coro)
        return MagicMock()

    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo), \
         patch("services.permissions.checker.check_permission", new=AsyncMock(return_value=True)), \
         patch("asyncio.create_task", side_effect=schedule):
        result = await service.begin(
            operation="create", proposed_snapshot=definition, idempotency_key="new-key",
        )

    assert result["id"] == "cs1"
    assert result["status"] == "draft"
    service._build_release.assert_not_awaited()
    assert scheduled
    scheduled[0].close()


@pytest.mark.asyncio
async def test_complete_enriches_same_changeset_and_notifies_client():
    repo = _Repo()
    repo.row.update({
        "operation": "create", "status": "draft", "base_snapshot": {},
        "proposed_snapshot": {
            "name": "日报", "prompt": "查询淘宝销售额", "timezone": "Asia/Shanghai",
            "push_target": {"type": "web", "user_id": "u1"},
            "schedule_type": "daily", "cron_expr": "0 9 * * *",
        },
        "policy_snapshot": {}, "patch": [], "diff": {},
    })
    db = _Db()
    service = ScheduledTaskChangeSetService(db, user_id="u1", org_id="org1")
    service._build_release = AsyncMock(return_value=_release())
    service.adapter.preflight = AsyncMock(return_value=PreflightResult(
        True, {"full_run": False, "mode": "deterministic_read_only"}, (),
    ))
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo), \
         patch("services.permissions.checker.check_permission", new=AsyncMock(return_value=True)), \
         patch("services.websocket_manager.ws_manager.send_to_user", new=AsyncMock()) as notify:
        await service.complete("cs1")

    assert repo.row["id"] == "cs1"
    assert repo.row["status"] == "awaiting_approval"
    assert repo.row["plan_snapshot"]
    notify.assert_awaited_once_with(
        "u1", {"type": "changeset_updated", "payload": {"change_set_id": "cs1", "status": "awaiting_approval"}},
        org_id="org1",
    )


@pytest.mark.asyncio
async def test_idempotency_key_collision_is_a_safe_conflict():
    repo = _Repo()
    repo.row["idempotency_key"] = "same-key"
    repo.row["created_by"] = "u1"
    repo.row["resource_id"] = "fixed-resource"
    db = _Db()
    service = ScheduledTaskChangeSetService(db, user_id="u1", org_id="org1")
    definition = {
        "name": "日报", "prompt": "不同请求", "timezone": "Asia/Shanghai",
        "push_target": {"type": "web", "user_id": "u1"},
        "schedule_type": "daily", "cron_expr": "0 9 * * *",
    }
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo):
        with pytest.raises(ScheduledTaskChangeError) as exc_info:
            await service.propose(
                operation="create", resource_id="fixed-resource",
                proposed_snapshot=definition, idempotency_key="same-key",
            )
    assert getattr(exc_info.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_concurrent_duplicate_create_replays_database_winner():
    repo = _RacingRepo()
    db = _Db()
    service = ScheduledTaskChangeSetService(db, user_id="u1", org_id="org1")
    service._build_release = AsyncMock(return_value=_release())
    service.adapter.preflight = AsyncMock(return_value=PreflightResult(
        True, {"full_run": False, "mode": "deterministic_read_only"}, (),
    ))
    definition = {
        "name": "日报", "prompt": "查询淘宝销售额", "timezone": "Asia/Shanghai",
        "push_target": {"type": "web", "user_id": "u1"},
        "schedule_type": "daily", "cron_expr": "0 9 * * *",
    }
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo), \
         patch("services.permissions.checker.check_permission", new=AsyncMock(return_value=True)):
        result = await service.propose(
            operation="create", proposed_snapshot=definition, idempotency_key="same-key",
        )

    assert result["id"] == "cs1"
    assert service._build_release.await_count == 1


@pytest.mark.asyncio
async def test_all_mutating_operations_create_changeset_without_old_draft_write():
    repo = _Repo()
    db = _Db(rows=[{"id": "task1", "org_id": "org1", "user_id": "u1", "revision": 2, "status": "paused"}])
    service = ScheduledTaskChangeSetService(db, user_id="u1", org_id="org1")
    service._build_release = AsyncMock(return_value=_release())
    service.adapter.preflight = AsyncMock(return_value=PreflightResult(
        True, {"full_run": False, "mode": "deterministic_read_only"}, (),
    ))
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo), \
         patch("services.permissions.checker.check_permission", new=AsyncMock(return_value=True)):
        for operation in ("create", "update", "pause", "resume", "delete"):
            base = {} if operation == "create" else db.rows[0]
            proposed = {"name": "日报", "prompt": "查询", "timezone": "Asia/Shanghai", "push_target": {"type": "web", "user_id": "u1"}, "schedule_type": "daily", "cron_expr": "0 9 * * *", "next_run_at": "2030-01-01T01:00:00+00:00"}
            if operation not in {"create", "update"}:
                proposed = {**base, "next_run_at": "2030-01-01T01:00:00+00:00"}
            row = await service.propose(
                operation=operation, resource_id=None if operation == "create" else "task1",
                base_snapshot=base, proposed_snapshot=proposed,
            )
            assert row["status"] == "awaiting_approval"
    assert repo.checks


@pytest.mark.asyncio
async def test_revision_conflict_is_rejected_before_commit():
    db = _Db(rows=[{"id": "task1", "org_id": "org1", "revision": 9, "status": "paused"}])
    adapter = ScheduledTaskChangeAdapter(db, user_id="u1", org_id="org1")
    context = ChangeSetContext(
        id="cs1", org_id="org1", resource_type="scheduled_task", resource_id="task1",
        operation="update", base_revision="8", base_snapshot={}, proposed_snapshot={},
        patch=(), diff={}, policy_snapshot={},
    )
    result = await adapter.validate(SimpleNamespace(context=context))
    assert result.passed is False
    assert "base_revision_conflict" in result.reasons


@pytest.mark.asyncio
async def test_normalize_rejects_invalid_schedule_without_writing():
    adapter = ScheduledTaskChangeAdapter(_Db(), user_id="u1", org_id="org1")
    context = ChangeSetContext(
        id="cs1", org_id="org1", resource_type="scheduled_task", resource_id="task1",
        operation="update", base_revision="1", base_snapshot={}, proposed_snapshot={},
        patch=(), diff={}, policy_snapshot={},
    )
    with pytest.raises(ValueError, match="cron_expr 无效"):
        await adapter.normalize(SimpleNamespace(
            context=context,
            proposed_snapshot={
                "name": "日报", "prompt": "查询", "timezone": "Asia/Shanghai",
                "push_target": {"type": "web", "user_id": "u1"},
                "schedule_type": "cron", "cron_expr": "not-cron",
            },
        ))


@pytest.mark.asyncio
async def test_normalize_completes_create_definition_before_planning():
    adapter = ScheduledTaskChangeAdapter(_Db(), user_id="u1", org_id="org1")
    context = ChangeSetContext(
        id="cs1", org_id="org1", resource_type="scheduled_task", resource_id="task1",
        operation="create", base_revision="0", base_snapshot={}, proposed_snapshot={},
        patch=(), diff={}, policy_snapshot={},
    )
    result = await adapter.normalize(SimpleNamespace(
        context=context,
        proposed_snapshot={
            "name": "日报", "prompt": "查询订单", "timezone": "Asia/Shanghai",
            "push_target": {"type": "web", "user_id": "u1"},
            "schedule_type": "daily", "time_str": "09:00",
        },
    ))

    assert result.proposed_snapshot["max_credits"] == 10
    assert result.proposed_snapshot["retry_count"] == 1
    assert result.proposed_snapshot["timeout_sec"] == 180
    assert result.proposed_snapshot["template_file"] is None
    assert result.proposed_snapshot["cron_expr"] == "0 9 * * *"


@pytest.mark.asyncio
async def test_normalize_update_merges_hidden_limits_from_current_task():
    adapter = ScheduledTaskChangeAdapter(_Db(), user_id="u1", org_id="org1")
    context = ChangeSetContext(
        id="cs1", org_id="org1", resource_type="scheduled_task", resource_id="task1",
        operation="update", base_revision="7", base_snapshot={
            "name": "旧日报", "prompt": "旧查询", "timezone": "Asia/Shanghai",
            "push_target": {"type": "web", "user_id": "u1"},
            "schedule_type": "daily", "cron_expr": "0 9 * * *",
            "max_credits": 42, "retry_count": 3, "timeout_sec": 240,
        }, proposed_snapshot={}, patch=(), diff={}, policy_snapshot={},
    )
    result = await adapter.normalize(SimpleNamespace(
        context=context,
        proposed_snapshot={"name": "新日报", "prompt": "新查询"},
    ))

    assert result.proposed_snapshot["name"] == "新日报"
    assert result.proposed_snapshot["max_credits"] == 42
    assert result.proposed_snapshot["retry_count"] == 3
    assert result.proposed_snapshot["timeout_sec"] == 240
    assert result.proposed_snapshot["cron_expr"] == "0 9 * * *"
