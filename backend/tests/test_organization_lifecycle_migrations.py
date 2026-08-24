"""Organization lifecycle governance and suspended execution contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = (
    ROOT / "migrations/217_organization_lifecycle_governance.sql"
).read_text(encoding="utf-8")
LIFECYCLE_ROLLBACK = (
    ROOT
    / "migrations/rollback/217_organization_lifecycle_governance_rollback.sql"
).read_text(encoding="utf-8")
FENCE = (
    ROOT / "migrations/218_suspended_organization_execution_fence.sql"
).read_text(encoding="utf-8")
FENCE_ROLLBACK = (
    ROOT
    / "migrations/rollback/218_suspended_organization_execution_fence_rollback.sql"
).read_text(encoding="utf-8")
ROLE_CLOSURE = (
    ROOT / "migrations/232_organization_lifecycle_runtime_role_closure.sql"
).read_text(encoding="utf-8")
ROLE_CLOSURE_ROLLBACK = (
    ROOT
    / "migrations/rollback/232_organization_lifecycle_runtime_role_closure_rollback.sql"
).read_text(encoding="utf-8")


def _function(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE (?:OR REPLACE )?FUNCTION {name}\b.*?\n\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_lifecycle_transitions_are_locked_atomic_and_audited() -> None:
    suspend = _function(LIFECYCLE, "suspend_governed_organization")
    restore = _function(LIFECYCLE, "restore_governed_organization")
    for body, previous, new, action in (
        (suspend, "active", "suspended", "organization.suspend"),
        (restore, "suspended", "active", "organization.restore"),
    ):
        assert "tenant_org_id() IS NOT NULL" in body
        assert "_assert_governance_authority" in body
        assert "FOR UPDATE" in body
        assert f"status <> '{previous}'" in body
        assert f"SET status = '{new}'" in body
        assert "GOVERNANCE_ORG_NOT_FOUND" in body
        assert "GOVERNANCE_ORG_STATUS_CONFLICT" in body
        assert "_record_governance_audit" in body
        assert action in body
        assert "'previous_status'" in body
        assert "'new_status'" in body
        assert "encrypt_key" not in body
        assert "secret" not in body.lower()


def test_lifecycle_acl_is_runtime_only_and_rollback_removes_capability() -> None:
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in LIFECYCLE
    assert "everydayai_worker, everydayai_sync, everydayai;" in LIFECYCLE
    assert re.search(
        r"GRANT EXECUTE ON FUNCTION.*?suspend_governed_organization"
        r".*?restore_governed_organization.*?TO everydayai_runtime;",
        LIFECYCLE,
        re.DOTALL,
    )
    for name in (
        "suspend_governed_organization",
        "restore_governed_organization",
    ):
        assert f"DROP FUNCTION IF EXISTS {name}(UUID)" in LIFECYCLE_ROLLBACK


def test_pending_invitation_discovery_hides_suspended_organizations() -> None:
    body = _function(LIFECYCLE, "list_actor_pending_invitations")
    assert "organization.status = 'active'" in body


def test_service_writes_and_discovery_fail_closed_for_suspended_orgs() -> None:
    guard = _function(FENCE, "reject_suspended_organization_service_write")
    assert "session_user NOT IN" in guard
    assert "organization.status = 'active'" in guard
    assert "ORGANIZATION_EXECUTION_SUSPENDED" in guard
    delivery_guard = _function(
        FENCE, "reject_suspended_delivery_service_write",
    )
    assert "task.id = NEW.task_id" in delivery_guard
    assert "organization.status <> 'active'" in delivery_guard
    for table in (
        "tasks",
        "scheduled_tasks",
        "scheduled_task_runs",
        "agent_runtime_sessions",
        "agent_session_commands",
        "agent_runs",
        "agent_run_attempts",
        "agent_model_steps",
        "agent_runtime_events",
        "agent_projection_outbox",
        "wecom_callback_inbox",
        "conversation_deliveries",
    ):
        assert f"ON {table}" in FENCE
        assert f"ON {table}" in FENCE_ROLLBACK
    for name in (
        "discover_generation_turn_candidates",
        "worker_discover_media_tasks",
        "worker_claim_due_scheduled_tasks",
    ):
        assert "organization.status = 'active'" in _function(FENCE, name)
        assert name in FENCE_ROLLBACK


def test_agent_runtime_roles_are_included_in_the_write_fence() -> None:
    for role_name in (
        "everydayai_agent_runtime_worker",
        "everydayai_projection_worker",
        "everydayai_authorization_worker",
        "everydayai_sandbox_worker",
        "everydayai_runtime_admin",
    ):
        assert role_name in ROLE_CLOSURE
    assert "everydayai_sync" not in _function(
        ROLE_CLOSURE, "reject_suspended_delivery_service_write",
    )
    for function_name in (
        "reject_suspended_organization_service_write",
        "reject_suspended_delivery_service_write",
    ):
        assert (
            f"CREATE OR REPLACE FUNCTION {function_name}"
            in ROLE_CLOSURE_ROLLBACK
        )
