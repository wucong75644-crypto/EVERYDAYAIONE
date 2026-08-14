from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / (
    "migrations/228_08h_agent_runtime_scheduled_web_projection_claim_ordering.sql"
)
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08h_agent_runtime_scheduled_web_projection_claim_ordering_rollback.sql"
)


def test_claim_ordering_migration_is_narrow_and_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert len(sql.splitlines()) < 160
    assert "ORDER BY i.id ASC" in sql
    assert "WHERE existing.intent_id=i.id" in sql
    assert "pg_advisory_xact_lock(228080036::BIGINT)" in sql
    assert sql.index("IF EXISTS(") < sql.index("pg_advisory_xact_lock")
    assert "LOCK TABLE" not in sql
    assert "session_user<>'everydayai_projection_worker'" in sql
    assert "current_setting('app.access_kind',TRUE)" in sql
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in sql
    assert "TO everydayai_projection_worker" in sql
    assert "_claim_agent_runtime_scheduled_web_projection_227_36_v1" in sql
    assert "AGENT_RUNTIME_228_08H_ROLLBACK_DEPENDENCY_CONFLICT" in rollback
    assert "AGENT_RUNTIME_228_08H_ROLLBACK_ACTIVE_PROJECTION" in rollback
    assert "projection_state IN('pending','claimed','projected')" in rollback
    assert "target.target_type='web'" in rollback
