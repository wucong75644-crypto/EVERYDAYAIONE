"""任务与积分事务身份约束迁移契约测试。"""

from pathlib import Path


ROOT = Path(__file__).parent.parent
MIGRATION = ROOT / "migrations/252_tasks_external_id_unique.sql"
ROLLBACK = ROOT / "migrations/rollback/252_tasks_external_id_unique_rollback.sql"


def test_external_provider_id_is_unique_but_deferred_ids_are_allowed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "uq_tasks_external_task_id" in sql
    assert "WHERE external_task_id IS NOT NULL" in sql
    assert "contains duplicates" in sql


def test_credit_transactions_allow_retry_history_but_one_pending_lock() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "DROP INDEX IF EXISTS uq_credit_tx_task_org" in sql
    assert "uq_credit_tx_task_pending_org" in sql
    assert "WHERE status = 'pending'" in sql


def test_rollback_restores_previous_credit_constraint() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "DROP INDEX IF EXISTS uq_credit_tx_task_pending_org" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_tx_task_org" in sql
