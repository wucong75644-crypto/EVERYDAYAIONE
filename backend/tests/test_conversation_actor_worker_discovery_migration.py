"""Migration 163 Actor Worker discovery and claim capability contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/163_conversation_actor_worker_discovery.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "migrations/rollback"
    / "163_conversation_actor_worker_discovery_rollback.sql"
).read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert match
    return match.group(0)


def test_discovery_requires_exact_actorless_worker_scope() -> None:
    assertion = _function("_assert_actor_worker_discovery_scope")

    assert "session_user <> 'everydayai_worker'" in assertion
    assert "app.access_kind" in assertion
    assert "IS DISTINCT FROM 'worker'" in assertion
    assert "app.actor_user_id" in assertion
    assert "app.org_id" in assertion
    assert "app.request_id" in assertion
    assert "ACTOR_WORKER_DISCOVERY_SCOPE_REQUIRED" in assertion


def test_discovery_is_bounded_and_returns_no_task_payload() -> None:
    discovery = _function("discover_generation_turn_candidates")

    assert "SECURITY DEFINER" in discovery
    assert "p_limit NOT BETWEEN 1 AND 1000" in discovery
    assert "ACTOR_DISCOVERY_LIMIT_INVALID" in discovery
    assert "'task_id'" in discovery
    assert "'conversation_id'" in discovery
    assert "'execution_mode'" in discovery
    for forbidden in (
        "delivery_context',",
        "user_id',",
        "org_id',",
        "input_message_id',",
    ):
        assert forbidden not in discovery


def test_claims_preserve_locking_and_return_exact_scope() -> None:
    serial = _function("worker_claim_next_serial_generation_turn")
    branch = _function("worker_claim_branch_generation_turn")

    assert "FOR UPDATE SKIP LOCKED" in serial
    assert "active_serial_task_id" in serial
    assert "queue_sequence" in serial
    assert "FOR UPDATE" in branch
    assert "execution_token" in serial
    assert "execution_token" in branch
    for body in (serial, branch):
        assert "SECURITY DEFINER" in body
        assert "_assert_actor_worker_discovery_scope" in body
        assert "'user_id', v_task.user_id" in body
        assert "'org_id', v_task.org_id" in body


def test_task_execution_facades_require_exact_claimed_scope() -> None:
    assertion = _function("_assert_actor_worker_task_scope")

    assert "session_user <> 'everydayai_worker'" in assertion
    assert "app.access_kind" in assertion
    assert "app.actor_user_id" in assertion
    assert "IS DISTINCT FROM v_task.user_id" in assertion
    assert "app.org_id" in assertion
    assert "IS DISTINCT FROM v_task.org_id" in assertion
    assert "ACTOR_WORKER_TASK_SCOPE_MISMATCH" in assertion


def test_task_execution_facades_delegate_without_copying_atomic_logic() -> None:
    expected = {
        "worker_renew_generation_lease": "renew_generation_lease",
        "worker_commit_generation_turn_with_context_v2":
            "commit_generation_turn_with_context_v2",
        "worker_fail_generation_turn": "fail_generation_turn",
    }

    for facade, target in expected.items():
        body = _function(facade)
        assert "SECURITY DEFINER" in body
        assert "_assert_actor_worker_task_scope(p_task_id)" in body
        assert f"public.{target}(" in body


def test_claimed_task_read_requires_scope_and_fencing_token() -> None:
    body = _function("worker_get_claimed_generation_task")

    assert "SECURITY DEFINER" in body
    assert "_assert_actor_worker_task_scope(p_task_id)" in body
    assert "v_task.status <> 'running'" in body
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in body
    assert "ACTOR_TASK_READ_OWNERSHIP_LOST" in body
    assert "RETURN to_jsonb(v_task)" in body


def test_only_worker_receives_discovery_and_claim_execute() -> None:
    grants = SQL[SQL.index("REVOKE ALL ON FUNCTION"):]

    assert grants.count("TO everydayai_worker;") == 7
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in grants
    assert "GRANT SELECT" not in grants
    assert "GRANT UPDATE" not in grants


def test_rollback_revokes_then_drops_all_new_capabilities() -> None:
    for name in (
        "worker_claim_branch_generation_turn",
        "worker_claim_next_serial_generation_turn",
        "discover_generation_turn_candidates",
        "worker_renew_generation_lease",
        "worker_get_claimed_generation_task",
        "worker_commit_generation_turn_with_context_v2",
        "worker_fail_generation_turn",
        "_assert_actor_worker_task_scope",
        "_assert_actor_worker_discovery_scope",
    ):
        assert f"DROP FUNCTION IF EXISTS {name}" in ROLLBACK
    assert ROLLBACK.index("REVOKE ALL") < ROLLBACK.index("DROP FUNCTION")
