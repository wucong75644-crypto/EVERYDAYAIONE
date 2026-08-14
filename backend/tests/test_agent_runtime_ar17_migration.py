from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/224_01_agent_runtime_ar17_core.sql").read_text()
SEED = (ROOT / "migrations/224_02_agent_runtime_ar17_version_seed.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/224_01_agent_runtime_ar17_core_rollback.sql").read_text()
SEED_ROLLBACK = (ROOT / "migrations/rollback/224_02_agent_runtime_ar17_version_seed_rollback.sql").read_text()


def test_ar17_migration_is_additive_and_fail_closed() -> None:
    assert "219_" not in SQL and "223_" not in SQL
    assert "SECURITY DEFINER" in SQL
    assert "SET search_path = pg_catalog, public" in SQL
    assert "runtime_submit_ingress_v2" in SQL
    assert "get_agent_runtime_model_context_v2" in SQL
    assert "enqueue_wecom_runtime_turn_v4" in SQL
    assert "GRANT EXECUTE" in SQL
    assert "REVOKE ALL ON TABLE agent_runtime_definition_facts" in SQL
    assert "AGENT_RUNTIME_224_ROLLBACK_GUARD_FACTS_EXIST" in ROLLBACK
    assert "DROP FUNCTION" in ROLLBACK
    assert "definition_document" in SEED and "system_prompt" in SEED
    assert "AGENT_RUNTIME_224_ROLLBACK_GUARD_FACTS_EXIST" in SEED_ROLLBACK


def test_v2_envelope_contains_all_frozen_binding_facts() -> None:
    for field in (
        "base_context_revision", "through_message_id", "agent_definition_hash",
        "effective_toolset_revision", "effective_toolset_hash", "release_revision",
        "binding_hash", "request_identity",
    ):
        assert field in SQL
