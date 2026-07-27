"""Migration 218 Runtime prepared-credit refund capability contract."""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
SQL = (MIGRATIONS / "218_runtime_prepared_credit_refund.sql").read_text()
ROLLBACK = (
    MIGRATIONS
    / "rollback"
    / "218_runtime_prepared_credit_refund_rollback.sql"
).read_text()


def test_refund_facade_validates_runtime_tenant_task_and_transaction() -> None:
    assert "SECURITY DEFINER" in SQL
    assert "session_user <> 'everydayai_runtime'" in SQL
    assert "current_setting('app.access_kind', TRUE) <> 'runtime'" in SQL
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in SQL
    assert "v_task.status::TEXT <> 'preparing'" in SQL
    assert "v_task.type::TEXT NOT IN ('image', 'video')" in SQL
    assert "v_task.user_id IS DISTINCT FROM public.tenant_actor_user_id()" in SQL
    assert "v_transaction.task_id IS DISTINCT FROM v_task.id" in SQL
    assert "v_transaction.user_id IS DISTINCT FROM v_task.user_id" in SQL
    assert "v_transaction.org_id IS DISTINCT FROM v_task.org_id" in SQL


def test_only_runtime_receives_facade_not_atomic_refund() -> None:
    assert (
        "GRANT EXECUTE ON FUNCTION refund_prepared_generation_credits(\n"
        "    UUID, UUID, UUID\n"
        ") TO everydayai_runtime;"
    ) in SQL
    assert "GRANT EXECUTE ON FUNCTION atomic_refund_credits" not in SQL
    assert "RETURN public.atomic_refund_credits(p_transaction_id);" in SQL


def test_rollback_removes_runtime_refund_facade() -> None:
    assert "REVOKE ALL ON FUNCTION refund_prepared_generation_credits(" in ROLLBACK
    assert "DROP FUNCTION refund_prepared_generation_credits(" in ROLLBACK
