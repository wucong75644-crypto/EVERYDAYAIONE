from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.changeset.contracts import ChangeSetContext, CommitRequest, PreflightResult
from services.changeset.risk import RiskLevel
from services.planner import PlanCandidate, PlanRelease
from services.scheduler.scheduled_task_change_adapter import (
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
        self.row = {**self.row, **payload, "id": "cs1", "status": "draft"}
        return deepcopy(self.row)

    def get(self, _id, _org):
        return deepcopy(self.row)

    def transition(self, *, next_status, **_kwargs):
        self.row["status"] = next_status
        return deepcopy(self.row)

    def record_check(self, **kwargs):
        self.checks.append(kwargs)
        return kwargs

    def list_checks(self, *_args):
        return deepcopy(self.checks)


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
