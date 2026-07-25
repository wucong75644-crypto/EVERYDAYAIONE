from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "backend/migrations/165_memory_runtime_tenant_boundary.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/165_memory_runtime_tenant_boundary_rollback.sql"
).read_text()

TABLES = (
    "memory_pipeline_state",
    "memory_session_logs",
    "memory_consolidation_runs",
    "memory_atoms",
)


def test_all_memory_runtime_tables_force_rls() -> None:
    for table in TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in MIGRATION
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in MIGRATION
        assert f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY" in ROLLBACK


def test_policies_bind_user_conversation_and_source_logs() -> None:
    assert "tenant_memory_pipeline_state" in MIGRATION
    assert "tenant_user_fact_visible(org_id, user_id)" in MIGRATION
    assert "tenant_conversation_visible(session_id, org_id)" in MIGRATION
    assert "memory_session_logs.conversation_id" in MIGRATION
    assert "memory_session_logs.user_id" in MIGRATION
    assert "memory_consolidation_runs.user_id" in MIGRATION
    assert "LEFT JOIN memory_session_logs session_log" in MIGRATION


def test_runtime_capabilities_are_minimal_and_legacy_is_revoked() -> None:
    assert (
        "GRANT SELECT, INSERT, UPDATE\n"
        "ON TABLE memory_pipeline_state, memory_session_logs"
    ) in MIGRATION
    assert (
        "GRANT SELECT, INSERT\n"
        "ON TABLE memory_consolidation_runs"
    ) in MIGRATION
    assert "FROM PUBLIC;" in MIGRATION
    assert "FROM service_role;" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION commit_memory_session_flush" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION commit_memory_consolidation" in MIGRATION


def test_rollback_restores_pre_165_memory_atom_policy() -> None:
    assert "DROP POLICY IF EXISTS tenant_memory_atoms" in ROLLBACK
    assert (
        "ON memory_atoms TO everydayai_runtime, everydayai_worker"
    ) in ROLLBACK
    assert "USING (tenant_user_fact_visible(org_id, user_id))" in ROLLBACK
