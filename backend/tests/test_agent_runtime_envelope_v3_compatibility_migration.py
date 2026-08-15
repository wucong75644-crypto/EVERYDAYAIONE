from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT / "migrations/228_08m_agent_runtime_envelope_v3_compatibility.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_08m_agent_runtime_envelope_v3_compatibility_rollback.sql"
).read_text()


def test_model_and_read_context_accept_only_frozen_v2_and_v3_envelopes() -> None:
    assert MIGRATION.count("schema_revision' NOT IN ('2','3')") == 2
    assert MIGRATION.count("schema_revision' IS NULL") == 2
    assert "get_agent_runtime_model_context_v2" in MIGRATION
    assert "_agent_runtime_read_context" in MIGRATION
    assert "SECURITY DEFINER" in MIGRATION
    assert "search_path = pg_catalog, public" in MIGRATION
    assert "TO everydayai_agent_runtime_worker" in MIGRATION


def test_rollback_restores_v2_only_contract_with_drift_guards() -> None:
    assert ROLLBACK.count("IS DISTINCT FROM ''2''") == 2
    assert "MODEL_CONTEXT_ROLLBACK_DRIFT" in ROLLBACK
    assert "READ_CONTEXT_ROLLBACK_DRIFT" in ROLLBACK
