from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/230_08_agent_runtime_media_readiness_bootstrap.sql"
ROLLBACK = ROOT / "migrations/rollback/230_08_agent_runtime_media_readiness_bootstrap_rollback.sql"


def test_bootstrap_migration_separates_owner_readiness_from_production_gate() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION record_agent_runtime_media_projection_readiness_v1" in sql
    assert "runtime_control.release_revision=btrim(p_projection_revision)" in sql
    assert "runtime_control.projection_enabled" not in sql
    assert "media_control.production_ready" not in sql
    assert "heartbeat.details->>'media_provider_probe_passed'" in sql
    assert "projection_owner_ready=COALESCE(effective_ready,FALSE)" in sql


def test_bootstrap_rollback_restores_previous_gate_contract() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "runtime_control.projection_enabled" in sql
    assert "media_control.production_ready" in sql
