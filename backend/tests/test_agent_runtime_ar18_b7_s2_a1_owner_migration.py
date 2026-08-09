from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_29_agent_runtime_scheduled_execution_owner.sql"
ROLLBACK = ROOT / "migrations/rollback/227_29_agent_runtime_scheduled_execution_owner_rollback.sql"


def test_owner_facts_are_additive_private_and_failure_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "agent_runtime_scheduled_execution_profiles" in sql
    assert "agent_runtime_scheduled_run_bindings" in sql
    assert sql.count("ENABLE ROW LEVEL SECURITY") == 2
    assert sql.count("FORCE ROW LEVEL SECURITY") == 2
    assert sql.count("SET search_path=pg_catalog,public") >= 8
    assert "session_user<>'everydayai_agent_runtime_worker'" in sql
    assert "app.access_kind" in sql
    assert "TO everydayai_agent_runtime_worker" in sql
    assert "TO everydayai_worker" not in sql
    assert "source_effective_toolset_hash" in sql
    assert "p_target_toolset_snapshot IS DISTINCT FROM target" in sql
    assert "p_canonical_hash_input::JSONB IS DISTINCT FROM hash_facts" in sql
    assert "p_canonical_hash_input IS DISTINCT FROM _agent_runtime_scheduled_canonical_json(hash_facts)" in sql
    assert "_agent_runtime_scheduled_canonical_json(JSONB)" in sql
    assert "digest(convert_to(p_canonical_hash_input,'UTF8'),'sha256')" in sql
    assert "digest(target::TEXT::BYTEA" not in sql
    assert "'entitled_groups'" in sql
    assert "manage_scheduled_task'=ANY(source_names)" in sql
    assert "approved<@source_names" in sql
    assert "z->>'safety_level' IS DISTINCT FROM 'safe'" in sql
    assert "z->>'side_effect' IS DISTINCT FROM 'none'" in sql
    assert "z->>'authorization_requirement' IS DISTINCT FROM 'none'" in sql
    assert "SCHEDULED_RUN_RUNTIME_PROFILE_UNBOUND" in sql
    assert "_agent_runtime_scheduled_owner_gate" in sql
    assert "AGENT_RUNTIME_SCHEDULED_CONTEXT_ENVELOPE_REQUIRED" in sql
    assert "r.run_kind IS DISTINCT FROM 'scheduled'" in sql
    assert "c.command_type IS DISTINCT FROM 'submit_input'" in sql
    assert "_agent_run_request_hash(c.id,'scheduled'" in sql
    assert "profile_state_version" in sql and "task_status" in sql
    assert "scheduled-run-owner:" in sql and "scheduled-trigger-owner:" in sql
    assert sql.index("scheduled-run-owner:") < sql.index("scheduled-trigger-owner:")
    assert "agent_runtime_tenant_gate_controls" in sql
    assert "runtime_submit_ingress" not in sql
    assert "INSERT INTO agent_session_commands" not in sql
    assert "INSERT INTO agent_runs" not in sql
    assert "production_ready" not in sql


def test_owner_facts_rollback_is_exact_and_guarded() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AR_18_B7_S2_A1_ROLLBACK_OWNER_FACTS_EXIST" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduled_run_bindings" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduled_execution_profiles" in rollback
    assert "DROP TABLE IF EXISTS scheduled_tasks" not in rollback
    assert "GRANT" not in rollback
    assert "227_28" not in rollback
