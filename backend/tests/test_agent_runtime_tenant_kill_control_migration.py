from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_06_agent_runtime_tenant_kill_control.sql"
ROLLBACK = ROOT / "migrations/rollback/227_06_agent_runtime_tenant_kill_control_rollback.sql"


def test_a_migration_has_three_force_rls_facts_and_no_legacy_lane_edit() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_tenant_gate_controls" in sql
    assert "CREATE TABLE agent_runtime_owner_fences" in sql
    assert "CREATE TABLE agent_runtime_kill_audit" in sql
    assert sql.count("FORCE ROW LEVEL SECURITY") == 3
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "227_02" not in sql and "227_03" not in sql
    assert "227_04" not in sql and "227_05" not in sql
    for forbidden_field in ("secret_value", "credential_value", "provider_payload", "user_prompt"):
        assert forbidden_field not in sql.lower()
    assert "impact_summary::TEXT !~*" in sql


def test_a_admin_contract_is_cas_audited_and_failure_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "set_agent_runtime_tenant_gate" in sql
    assert "stale_version" in sql
    assert "idempotency_conflict" in sql
    assert "already_applied" in sql
    assert "kill_epoch" in sql
    assert "sha256(convert_to" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "agent_runtime_kill_audit_immutable" in sql
    assert "RUNTIME_ADMIN_REQUIRED" in sql
    assert "everydayai_runtime_admin" in sql


def test_a_rollback_refuses_any_new_fact_and_only_removes_227_06() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AR173_A_ROLLBACK_GUARD_FACTS_EXIST" in rollback
    assert "DROP TABLE agent_runtime_kill_audit" in rollback
    assert "DROP TABLE agent_runtime_owner_fences" in rollback
    assert "DROP TABLE agent_runtime_tenant_gate_controls" in rollback
    assert "227_02" not in rollback and "227_03" not in rollback
    assert "227_04" not in rollback and "227_05" not in rollback


def test_a_worker_can_only_read_a_token_bound_owner_fence() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "get_agent_runtime_owner_fence" in sql
    assert "GRANT EXECUTE ON FUNCTION get_agent_runtime_owner_fence" in sql
    assert "GRANT SELECT" not in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
