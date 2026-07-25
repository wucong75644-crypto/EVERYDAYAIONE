"""迁移 186：媒体失败退款与终态必须保持一个 Worker 能力边界。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
SQL = (
    MIGRATIONS / "186_worker_media_failure_settlement.sql"
).read_text()
ROLLBACK = (
    MIGRATIONS / "rollback"
    / "186_worker_media_failure_settlement_rollback.sql"
).read_text()


def test_failure_settlement_refunds_inside_worker_security_definer() -> None:
    assert "CREATE OR REPLACE FUNCTION worker_settle_media_batch_item" in SQL
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "ELSIF p_status = 'failed'" in SQL
    assert "SELECT public.atomic_refund_credits" in SQL
    assert "TO everydayai_worker;" in SQL
    assert "GRANT EXECUTE ON FUNCTION atomic_refund_credits" not in SQL


def test_rollback_restores_non_refunding_settlement_contract() -> None:
    assert "CREATE OR REPLACE FUNCTION worker_settle_media_batch_item" in ROLLBACK
    assert "SELECT public.atomic_refund_credits" not in ROLLBACK
