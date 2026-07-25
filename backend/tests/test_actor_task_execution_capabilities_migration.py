"""Migration 164 Actor Worker execution capability contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/164_actor_task_execution_capabilities.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "migrations/rollback"
    / "164_actor_task_execution_capabilities_rollback.sql"
).read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert match
    return match.group(0)


def test_worker_receives_only_required_business_table_reads() -> None:
    assert (
        "GRANT SELECT ON TABLE conversations, messages "
        "TO everydayai_worker;"
    ) in SQL
    assert "GRANT SELECT ON TABLE tasks" not in SQL
    assert "GRANT INSERT ON TABLE" not in SQL
    assert "GRANT UPDATE ON TABLE" not in SQL
    assert "GRANT DELETE ON TABLE" not in SQL


def test_atomic_dependency_chain_is_not_changed_by_migration() -> None:
    for signature in (
        "renew_generation_lease(UUID, UUID, INTEGER)",
        "update_generation_progress(UUID, UUID, TEXT, JSONB)",
        "fail_generation_turn(UUID, UUID, TEXT, TEXT)",
        "commit_generation_turn_with_context_v2(",
        "close_generation_turn(UUID, UUID, UUID)",
    ):
        assert f"ALTER FUNCTION {signature}" not in SQL
    assert "OWNER TO everydayai_owner" not in SQL


def test_progress_facade_is_task_scoped_and_delegates() -> None:
    body = _function("worker_update_generation_progress")

    assert "SECURITY DEFINER" in body
    assert "_assert_actor_worker_task_scope(p_task_id)" in body
    assert "public.update_generation_progress(" in body


def test_model_update_requires_token_and_live_lease() -> None:
    body = _function("worker_update_generation_model")

    assert "_assert_actor_worker_task_scope(p_task_id)" in body
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in body
    assert "v_task.lease_expires_at <= NOW()" in body
    assert "UPDATE public.tasks" in body


def test_terminal_snapshot_requires_same_fencing_token() -> None:
    body = _function("worker_get_generation_terminal_snapshot")

    assert "_assert_actor_worker_task_scope(p_task_id)" in body
    assert "v_task.status <> 'cancelled'" in body
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in body
    assert "'completed', 'failed', 'cancelled'" in body
    assert "'task', to_jsonb(v_task)" in body


def test_only_worker_can_execute_new_facades() -> None:
    grants = SQL[SQL.index("REVOKE ALL ON FUNCTION worker_update") :]

    assert grants.count("TO everydayai_worker;") == 3
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in grants


def test_rollback_removes_capabilities_and_restores_legacy_owners() -> None:
    for name in (
        "worker_update_generation_progress",
        "worker_update_generation_model",
        "worker_get_generation_terminal_snapshot",
    ):
        assert f"DROP FUNCTION IF EXISTS {name}" in ROLLBACK
    assert (
        "REVOKE SELECT ON TABLE conversations, messages "
        "FROM everydayai_worker;"
    ) in ROLLBACK
    assert "OWNER TO everydayai" not in ROLLBACK
