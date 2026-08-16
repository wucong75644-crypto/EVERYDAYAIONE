from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/230_09_agent_runtime_media_bootstrap_revision_gate.sql"
ROLLBACK = ROOT / "migrations/rollback/230_09_agent_runtime_media_bootstrap_revision_gate_rollback.sql"


def test_bootstrap_does_not_require_closed_production_gate() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION record_agent_runtime_media_projection_readiness_v1" in sql
    assert "runtime_control.projection_enabled" not in sql
    assert "runtime_control.release_revision" not in sql
    assert "heartbeat.release_revision=btrim(p_projection_revision)" in sql
    assert "heartbeat.details->>'media_provider_probe_passed'" in sql


def test_rollback_restores_revision_check() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "runtime_control.release_revision=btrim(p_projection_revision)" in sql
