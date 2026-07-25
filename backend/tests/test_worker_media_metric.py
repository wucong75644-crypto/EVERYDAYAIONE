"""媒体 Worker 知识指标能力合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/175_worker_media_metric.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/175_worker_media_metric_rollback.sql"
).read_text()


def test_metric_capability_derives_tenant_from_task() -> None:
    assert "CREATE FUNCTION worker_record_media_metric" in SQL
    assert "session_user <> 'everydayai_worker'" in SQL
    assert "v_task.user_id, v_task.org_id" in SQL
    assert "type IN ('image', 'video')" in SQL
    assert "GRANT INSERT ON" not in SQL
    assert "DROP FUNCTION worker_record_media_metric" in ROLLBACK
