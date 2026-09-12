import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from services.changeset.service import ChangeSetService
from services.changeset.repository import ChangeSetConcurrencyError
from services.scheduler.task_submission import submission_assessment, resume_time
from services.tools import ToolCall, ToolPolicy, build_legacy_catalog
from services.tools.dispatcher import ToolDispatcher, current_dispatch_call_id
from tests.test_changeset_core import FakeRepository, FakeAdapter, _row
from tests.test_tool_policy import context
from tests.test_scheduled_task_changeset_adapter import _Repo, _Db
from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeSetService, ScheduledTaskChangeError


def direct_policy():
    return {"risk_level": "medium", "requires_approval": False, "submission": {
        "version": "scheduled_task.submit.v1", "mode": "apply_if_allowed", "actor_id": "user-1"}}


def direct_repo():
    repo = FakeRepository(_row(status="preflighting", policy_snapshot=direct_policy()))
    repo.checks = [{"check_type": name, "status": "passed", "result": {}}
                   for name in ("authorization", "validation", "preflight")]
    repo.list_checks = lambda *_: deepcopy(repo.checks)
    return repo


@pytest.mark.asyncio
async def test_apply_requested_has_real_receipt_and_no_forged_confirmation():
    repo, adapter = direct_repo(), FakeAdapter()
    service = ChangeSetService(repo)
    for _ in range(2):
        result = await service.apply_requested(change_set_id="cs-1", org_id="org-1", actor_id="user-1", adapter=adapter)
        assert result["status"] == "applied"
    assert adapter.calls == 1
    assert repo.transitions[0] == ("preflighting", "committing", "request_accepted")
    assert not any(c.get("check_type") == "approval" for c in repo.checks)


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["historical", "proposal", "other_actor", "delete", "high", "approval", "missing_check", "failed", "skip_auth"])
async def test_direct_submission_fails_closed(violation):
    repo, adapter = direct_repo(), FakeAdapter()
    row = repo.rows["cs-1"]
    if violation == "historical": row["status"] = "awaiting_approval"
    if violation == "proposal": row["policy_snapshot"]["submission"]["mode"] = "proposal"
    if violation == "other_actor": row["created_by"] = "another-user"
    if violation == "delete": row["operation"] = "delete"
    if violation == "high": row["policy_snapshot"]["risk_level"] = "high"
    if violation == "approval": row["policy_snapshot"]["requires_approval"] = True
    if violation == "missing_check": repo.checks.pop()
    if violation == "failed": repo.checks[-1]["result"] = {"passed": False}
    if violation == "skip_auth": repo.checks[0]["status"] = "skipped"
    with pytest.raises(ChangeSetConcurrencyError):
        await ChangeSetService(repo).apply_requested(change_set_id="cs-1", org_id="org-1", actor_id="user-1", adapter=adapter)
    assert adapter.calls == 0


@pytest.mark.parametrize("mode,entry,permission,expected", [
    ("interactive", "model", "auto", "business_write"),
    ("interactive", "legacy_internal", "auto", "proposal"),
    ("interactive", "model", "plan", "proposal"),
    ("scheduled", "model", "auto", "proposal"),
    ("preflight", "model", "auto", "proposal"),
])
def test_trusted_mode_changes_policy_before_handler(mode, entry, permission, expected):
    ctx = context(execution_mode=mode, entrypoint=entry, permission_mode=permission,
                  feature_flags={"scheduled_task_direct_enabled": True}, task_id="task",
                  authorized_tool_names={"manage_scheduled_task"},
                  authorization_snapshot={"version": 1, "allowed_tools": ["manage_scheduled_task"]})
    decision = ToolPolicy(build_legacy_catalog()).decide("manage_scheduled_task", ctx, {"action": "pause"})
    assert decision.operation == expected
    if expected == "business_write":
        assert not decision.cacheable and not decision.parallelizable
    if mode == "preflight": assert decision.outcome == "deny"


@pytest.mark.asyncio
async def test_dispatch_identity_is_scoped_across_concurrent_calls_and_cancellation():
    registry = build_legacy_catalog()
    spec = registry.get("manage_scheduled_task")
    seen = []
    async def handler(args):
        before = current_dispatch_call_id()
        await asyncio.sleep(0)
        seen.append((before, current_dispatch_call_id()))
        if args.get("cancel"): raise asyncio.CancelledError()
    dispatcher = ToolDispatcher({(spec.executor_type, spec.handler_key): handler})
    decision = ToolPolicy(registry).decide(spec.name, context(), {"action": "pause"})
    async def run(number):
        call = ToolCall(str(number), spec.name, {"action": "pause", "cancel": number == 2})
        await dispatcher.dispatch(dispatcher._approve(call, spec, decision))
    await asyncio.gather(*(run(n) for n in range(3)), return_exceptions=True)
    assert sorted(seen) == [("0", "0"), ("1", "1"), ("2", "2")]
    assert current_dispatch_call_id() is None


@pytest.mark.asyncio
async def test_direct_pause_uses_same_commit_and_replays_after_task_revision_changes():
    repo = _Repo()
    db = _Db(rows=[{"id": "task1", "org_id": "org1", "user_id": "u1", "revision": 2, "status": "active"}],
             rpc_data={"outcome": "paused", "new_revision": 3})
    service = ScheduledTaskChangeSetService(db, user_id="u1", org_id="org1")
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo), \
         patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)):
        args = dict(operation="pause", resource_id="task1", base_snapshot={"revision": 2, "status": "active"},
                    proposed_snapshot={"status": "paused"}, idempotency_key="new", submission_mode="apply_if_allowed")
        first = await service.begin(**args)
        assert first["status"] == "applied"
        args["base_snapshot"] = {"revision": 3, "status": "paused"}
        assert (await service.begin(**args))["id"] == first["id"]
        args["operation"] = "resume"
        with pytest.raises(ScheduledTaskChangeError): await service.begin(**args)


def test_assessment_keeps_cost_scope_and_external_changes_for_confirmation():
    ordinary = {"schedule_type": "daily", "max_credits": 10, "retry_count": 1, "timeout_sec": 180,
                "push_target": {"type": "web", "user_id": "u"}, "data_scope": {"kind": "task_prompt"},
                "execution_policy": {"version": 1, "allowed_tools": ["erp_agent"]}}
    assert not submission_assessment("create", {}, ordinary, self_target=True).requires_approval
    assert submission_assessment("create", {}, ordinary, self_target=False).requires_approval
    assert submission_assessment("delete", ordinary, ordinary, self_target=True).requires_approval
    for change in ({"max_credits": 20}, {"data_scope": {"kind": "all_shops"}}, {"schedule_type": "cron", "cron_expr": "* * * * *"}):
        assert submission_assessment("update", ordinary, {**ordinary, **change}, self_target=True).requires_approval


def test_resume_past_one_shot_requires_a_new_time():
    with pytest.raises(ValueError, match="时间已过"):
        resume_time({"schedule_type": "once", "run_at": "2020-01-01T09:00:00+08:00"})


@pytest.mark.parametrize("change", [
    {"cron_expr": "*/2 * * * *"}, {"retry_count": 4}, {"timeout_sec": 900},
])
def test_usage_checks_follow_actual_cron_and_creation_budget(change):
    ordinary = {"schedule_type": "daily", "cron_expr": "0 9 * * *", "max_credits": 10,
                "retry_count": 1, "timeout_sec": 180,
                "execution_policy": {"version": 1, "allowed_tools": ["erp_agent"]}}
    assert submission_assessment("create", {}, {**ordinary, **change}, self_target=True).requires_approval
    # Even mislabeled complex schedules must not bypass a review when modified.
    base = {**ordinary, "cron_expr": "*/5 * * * *"}
    proposed = {**base, "cron_expr": "*/2 * * * *"}
    assert "frequency_increased" in submission_assessment("update", base, proposed, self_target=True).reasons


@pytest.mark.asyncio
async def test_resume_http_retry_replays_receipt_before_time_validation():
    from api.routes.scheduled_tasks import _propose_task_change
    from services.scheduler.scheduled_task_workflow import stable_json_hash
    repo = _Repo()
    repo.row.update({"created_by": "u1", "operation": "resume", "status": "applied",
        "policy_snapshot": {"submission": {"mode": "apply_if_allowed", "request_hash": stable_json_hash({
            "operation": "resume", "resource_id": "task1", "mode": "apply_if_allowed", "definition": {},
        })}}})
    task = {"id": "task1", "schedule_type": "once", "run_at": "2020-01-01T09:00:00+08:00"}
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository", return_value=repo):
        result = await _propose_task_change(db=_Db(), scoped_db=_Db(), user_id="u1", org_id="org1",
            operation="resume", task_id="task1", base_task=task, idempotency_key="key", submission_mode="apply_if_allowed")
    assert result["status"] == "applied"
