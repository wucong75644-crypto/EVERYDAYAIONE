"""Worker 错误日志能力合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/174_worker_error_log_capability.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/174_worker_error_log_capability_rollback.sql"
).read_text()


def test_error_log_capability_is_worker_only() -> None:
    assert "CREATE FUNCTION worker_record_error_log" in SQL
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "ON CONFLICT (fingerprint) WHERE is_resolved = FALSE" in SQL
    assert "GRANT EXECUTE ON FUNCTION worker_record_error_log" in SQL
    assert "GRANT INSERT ON" not in SQL
    assert "DROP FUNCTION worker_record_error_log" in ROLLBACK
