from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_20_agent_runtime_model_gateway_dispatch_binding.sql"
ROLLBACK = ROOT / "migrations/rollback/227_20_agent_runtime_model_gateway_dispatch_binding_rollback.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
UNDO = ROLLBACK.read_text(encoding="utf-8")
START = "start_agent_runtime_model_gateway_dispatch"
CLAIM = "claim_agent_runtime_model_gateway_operation_v2"


def test_dispatch_binding_identity_is_unique_additive_lane() -> None:
    matches = [
        migration for migration in discover_migrations(ROOT / "migrations")
        if migration.identity == MIGRATION.name
    ]
    assert [migration.path for migration in matches] == [MIGRATION]
    assert ROLLBACK.exists()
    assert "227_18" not in SQL and "227_19" not in SQL
    for forbidden in ("ALTER TABLE", "CREATE TABLE", "DROP TABLE"):
        assert forbidden not in SQL


def test_atomic_start_and_claim_v2_have_fixed_security_contract() -> None:
    for name in ("_agent_model_gateway_dispatch_fences", START, CLAIM):
        assert f"CREATE FUNCTION {name}" in SQL
    assert SQL.count("SECURITY DEFINER") == 3
    assert SQL.count("search_path=pg_catalog,public") == 3
    assert "_assert_agent_model_gateway_actor('runtime')" in SQL
    assert "_assert_agent_model_gateway_actor('gateway')" in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "TO everydayai_agent_model_gateway" in SQL
    assert "FROM PUBLIC" in SQL


def test_atomic_start_uses_database_facts_and_one_transaction() -> None:
    start = SQL.index(f"CREATE FUNCTION {START}")
    end = SQL.index("END $$;", start)
    body = SQL[start:end]
    locks = [
        body.index("agent_runtime_sessions WHERE id=p_session_id FOR UPDATE"),
        body.index("agent_runs WHERE id=p_run_id FOR UPDATE"),
        body.index("agent_model_steps WHERE id=p_model_step_id FOR UPDATE"),
        body.index("agent_model_attempts WHERE id=p_model_attempt_id FOR UPDATE"),
    ]
    assert locks == sorted(locks)
    assert "_agent_model_gateway_dispatch_fences" in body
    assert "status='dispatching',dispatch_phase='request_started'" in body
    assert body.index("UPDATE agent_model_attempts") < body.index(
        "INSERT INTO agent_runtime_model_gateway_operations"
    )
    assert "p_tenant_kill_epoch" not in body
    assert "p_provider_kill_epoch" not in body
    assert "p_capability_kill_epoch" not in body


def test_claim_v2_requires_dispatch_binding_and_old_entrypoints_are_revoked() -> None:
    claim = SQL[SQL.index(f"CREATE FUNCTION {CLAIM}"):]
    assert "a.status<>'dispatching'" in claim
    assert "a.dispatch_phase<>'request_started'" in claim
    assert "a.state_version<>o.attempt_state_version" in claim
    assert "_agent_model_gateway_fences" in claim
    assert "REVOKE ALL ON FUNCTION submit_agent_runtime_model_gateway_operation" in SQL
    assert "REVOKE ALL ON FUNCTION claim_agent_runtime_model_gateway_operation(" in SQL


def test_rollback_is_guarded_exact_and_restores_prior_acl() -> None:
    guard = UNDO.index("AGENT_MODEL_GATEWAY_DISPATCH_BINDING_FACTS_EXIST")
    assert guard < UNDO.index("REVOKE ALL ON FUNCTION")
    assert guard < UNDO.index("DROP FUNCTION")
    assert f"DROP FUNCTION {START}" in UNDO
    assert f"DROP FUNCTION {CLAIM}" in UNDO
    assert "GRANT EXECUTE ON FUNCTION submit_agent_runtime_model_gateway_operation" in UNDO
    assert "GRANT EXECUTE ON FUNCTION claim_agent_runtime_model_gateway_operation(" in UNDO
    for forbidden in ("DELETE ", "TRUNCATE ", "DROP TABLE"):
        assert forbidden not in UNDO
