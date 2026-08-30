"""ChangeSet 内核定向测试：状态、幂等、取消、过期、失败恢复和业务冲突。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from services.changeset.contracts import ChangeSetStatus, CommitResult
from services.changeset.repository import ChangeSetConcurrencyError
from services.changeset.risk import DefaultRiskPolicy, RiskLevel
from services.changeset.service import ChangeSetService
from services.changeset.state_machine import ChangeSetTransitionError, require_transition


def _row(**overrides):
    row = {
        "id": "cs-1", "org_id": "org-1", "resource_type": "scheduled_task",
        "resource_id": "task-1", "operation": "update", "base_revision": "7",
        "base_snapshot": {"revision": 7}, "proposed_snapshot": {"name": "日报"},
        "patch": [{"op": "replace", "path": "/name", "value": "日报"}],
        "diff": {"name": {"before": "旧日报", "after": "日报"}},
        "risk_level": "medium", "policy_snapshot": {}, "plan_snapshot": None,
        "tool_policy_snapshot": None, "check_summary": None,
        "status": "awaiting_approval", "idempotency_key": "key-1",
        "created_by": "user-1", "created_by_type": "user", "audit_subject": {},
        "revision": 0,
    }
    row.update(overrides)
    return row


class FakeRepository:
    def __init__(self, row=None):
        self.rows = {"cs-1": deepcopy(row or _row())}
        self.checks = []
        self.transitions = []

    def create(self, payload):
        for row in self.rows.values():
            if row["org_id"] == payload["org_id"] and row["idempotency_key"] == payload["idempotency_key"]:
                if row["proposed_snapshot"] != payload.get("proposed_snapshot", {}):
                    raise ChangeSetConcurrencyError("idempotency conflict", current=row)
                return deepcopy(row)
        new_id = payload.get("id", f"recovered-{len(self.rows)}")
        row = _row(
            id=new_id, status="draft", idempotency_key=payload["idempotency_key"],
            base_revision=payload["base_revision"], created_by=payload["actor_id"],
            recovery_of_id=payload.get("recovery_of_id"),
        )
        self.rows[new_id] = row
        return deepcopy(row)

    def get(self, change_set_id, org_id):
        row = self.rows.get(change_set_id)
        if not row or row["org_id"] != org_id:
            raise ChangeSetConcurrencyError("missing")
        return deepcopy(row)

    def transition(self, *, change_set_id, org_id, expected_status, next_status, **kwargs):
        row = self.rows[change_set_id]
        if row["status"] != expected_status:
            raise ChangeSetConcurrencyError("state conflict", current=row)
        require_transition(expected_status, next_status)
        row["status"] = next_status
        row["revision"] += 1
        self.transitions.append((expected_status, next_status, kwargs.get("event_type")))
        return deepcopy(row)

    def record_check(self, **kwargs):
        self.checks.append(kwargs)
        return {"id": f"check-{len(self.checks)}", **kwargs}


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.calls = 0
        self.result = result or CommitResult(applied=True, new_revision="8")
        self.error = error

    async def commit(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_state_machine_rejects_skipping_required_stages():
    with pytest.raises(ChangeSetTransitionError):
        require_transition(ChangeSetStatus.DRAFT, ChangeSetStatus.APPLIED)


def test_default_risk_policy_is_frozen_as_a_single_level_without_multi_approval():
    policy = DefaultRiskPolicy()
    assessment = policy.assess(
        resource_type="scheduled_task", operation="update", context={"external_effect": True},
    )
    assert assessment.level is RiskLevel.HIGH
    assert assessment.requires_approval is True
    assert assessment.as_snapshot()["version"] == "default.v1"


def test_cancel_is_idempotent_and_records_only_one_transition():
    repo = FakeRepository()
    service = ChangeSetService(repo)
    first = service.cancel(change_set_id="cs-1", org_id="org-1", actor_id="user-1")
    second = service.cancel(change_set_id="cs-1", org_id="org-1", actor_id="user-1")
    assert first["status"] == second["status"] == "cancelled"
    assert repo.transitions == [("awaiting_approval", "cancelled", "cancelled")]


def test_expire_is_idempotent():
    repo = FakeRepository(_row(status="proposed"))
    service = ChangeSetService(repo)
    assert service.expire(change_set_id="cs-1", org_id="org-1")["status"] == "expired"
    assert service.expire(change_set_id="cs-1", org_id="org-1")["status"] == "expired"


@pytest.mark.asyncio
async def test_duplicate_confirmation_does_not_commit_twice():
    repo = FakeRepository()
    adapter = FakeAdapter()
    service = ChangeSetService(repo)
    first = await service.confirm(
        change_set_id="cs-1", org_id="org-1", actor_id="user-1", adapter=adapter,
    )
    second = await service.confirm(
        change_set_id="cs-1", org_id="org-1", actor_id="user-1", adapter=adapter,
    )
    assert first["status"] == second["status"] == "applied"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_adapter_base_revision_conflict_becomes_conflicted():
    repo = FakeRepository()
    adapter = FakeAdapter(result=CommitResult(
        applied=False, new_revision=None, conflict={"current_revision": "8"},
    ))
    result = await ChangeSetService(repo).confirm(
        change_set_id="cs-1", org_id="org-1", actor_id="user-1", adapter=adapter,
    )
    assert result["status"] == "conflicted"
    assert repo.checks[-1]["check_type"] == "conflict"


@pytest.mark.asyncio
async def test_failed_commit_can_recover_as_a_new_draft():
    repo = FakeRepository()
    failed = await ChangeSetService(repo).confirm(
        change_set_id="cs-1", org_id="org-1", actor_id="user-1",
        adapter=FakeAdapter(error=RuntimeError("temporary")),
    )
    assert failed["status"] == "failed"
    recovered = ChangeSetService(repo).recover_failed(
        change_set_id="cs-1", org_id="org-1", actor_id="user-1",
        idempotency_key="recovery-key",
    )
    assert recovered["status"] == "draft"
    assert recovered["recovery_of_id"] == "cs-1"


def test_conflicting_terminal_state_cannot_be_cancelled():
    repo = FakeRepository(_row(status="conflicted"))
    with pytest.raises(ChangeSetConcurrencyError):
        ChangeSetService(repo).cancel(
            change_set_id="cs-1", org_id="org-1", actor_id="user-1",
        )
