"""迁移 208 的 Worker 周期任务与监控能力合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "backend/migrations/208_worker_periodic_monitor_completion.sql"
)


def test_periodic_jobs_are_worker_scoped_and_leased() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE worker_periodic_job_runs" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "session_user <> 'everydayai_worker'" in sql
    assert "tenant_actor_user_id() IS NOT NULL" in sql
    assert "tenant_org_id() IS NOT NULL" in sql
    assert "FOR UPDATE" in sql
    assert "CREATE FUNCTION worker_renew_periodic_job" in sql
    assert "clock_timestamp() + INTERVAL '5 minutes'" in sql
    assert "WORKER_PERIODIC_LEASE_LOST" in sql
    assert "TO everydayai_worker;" in sql


def test_wecom_snapshot_has_no_worker_table_grants_or_pii_samples() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    snapshot = sql.split(
        "CREATE FUNCTION worker_wecom_identity_health_snapshot()", 1
    )[1].split("CREATE OR REPLACE FUNCTION worker_model_scoring_snapshot", 1)[0]

    assert "wecom_user_mappings" in snapshot
    assert "created_by = 'wecom'" in snapshot
    assert "jsonb_build_object" in snapshot
    assert "nickname" not in snapshot
    assert "GRANT SELECT" not in sql


def test_model_snapshot_filters_non_performance_signals() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "metric.task_type IN ('chat', 'image', 'video')" in sql
    assert "metric.model_id NOT IN ('unknown', 'auto')" in sql
    assert "p_content_hash !~ '^[0-9a-f]{32}$'" in sql
    assert "'outcome', 'already_recorded'" in sql
    assert "CREATE FUNCTION _worker_upsert_model_score_knowledge" in sql
