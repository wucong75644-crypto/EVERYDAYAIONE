"""Migration 210 orphan recovery capability contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/210_worker_orphan_task_recovery_capability.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "migrations/rollback"
    / "210_worker_orphan_task_recovery_capability_rollback.sql"
).read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert match
    return match.group(0)


def test_scope_requires_exact_actorless_worker() -> None:
    body = _function("_assert_worker_orphan_recovery_scope")

    assert "session_user <> 'everydayai_worker'" in body
    assert "app.access_kind" in body
    assert "IS DISTINCT FROM 'worker'" in body
    assert "app.actor_user_id" in body
    assert "app.org_id" in body
    assert "app.request_id" in body


def test_claim_is_bounded_locked_and_excludes_actor_tasks() -> None:
    body = _function("worker_claim_orphan_tasks")

    assert "FOR UPDATE SKIP LOCKED" in body
    assert "p_limit NOT BETWEEN 1 AND 500" in body
    assert "p_lease_seconds NOT BETWEEN 15 AND 300" in body
    assert """delivery_context @> '{"actor": true}'::JSONB""" in body
    assert "execution_token = gen_random_uuid()" in body
    assert "lease_expires_at = NOW()" in body
    assert "credit_transaction_id" not in body
    assert "'user_id'" not in body
    assert "'org_id'" not in body


def test_complete_fences_and_commits_message_with_task() -> None:
    body = _function("worker_complete_orphan_task")

    assert "FOR UPDATE" in body
    assert "execution_token IS DISTINCT FROM p_execution_token" in body
    assert "lease_expires_at <= NOW()" in body
    assert "UPDATE public.messages" in body
    assert "INSERT INTO public.messages" in body
    assert "UPDATE public.tasks" in body
    assert "p_content::TEXT" in body
    assert "placeholder_message_id::TEXT::UUID" in body
    assert "status = 'interrupted'" in body
    assert "startup_recovered_partial" in body


def test_fail_fences_and_reuses_atomic_refund() -> None:
    body = _function("worker_fail_orphan_task")

    assert "execution_token IS DISTINCT FROM p_execution_token" in body
    assert "lease_expires_at <= NOW()" in body
    assert "public.atomic_refund_credits" in body
    assert "status = 'failed'" in body
    assert "startup_recovery_failed" in body
    assert "UPDATE public.users" not in body
    assert "INSERT INTO public.credits_history" not in body


def test_only_worker_gets_execute_without_table_grants() -> None:
    grants = SQL[SQL.index("REVOKE ALL ON FUNCTION"):]

    assert "TO everydayai_worker;" in grants
    for privilege in ("GRANT SELECT", "GRANT INSERT", "GRANT UPDATE"):
        assert privilege not in grants


def test_rollback_revokes_before_dropping_every_capability() -> None:
    assert ROLLBACK.index("REVOKE ALL") < ROLLBACK.index("DROP FUNCTION")
    for name in (
        "worker_claim_orphan_tasks",
        "worker_complete_orphan_task",
        "worker_fail_orphan_task",
        "_assert_worker_orphan_recovery_scope",
    ):
        assert f"DROP FUNCTION IF EXISTS {name}" in ROLLBACK


def test_application_has_no_cross_tenant_table_access() -> None:
    source = (
        ROOT / "services/task_recovery.py"
    ).read_text(encoding="utf-8")

    assert '.table("tasks")' not in source
    assert '.table("messages")' not in source
    assert "atomic_refund_credits" not in source
