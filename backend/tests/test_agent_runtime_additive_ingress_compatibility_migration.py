from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_13_agent_runtime_additive_ingress_compatibility.sql"
ROLLBACK = ROOT / "migrations/rollback/227_13_agent_runtime_additive_ingress_compatibility_rollback.sql"
SIGNATURE = "uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb"


def test_22713_is_the_next_immutable_additive_lane() -> None:
    identities = [item.identity for item in discover_migrations(ROOT / "migrations")]
    assert identities.index("227_13_agent_runtime_additive_ingress_compatibility.sql") > identities.index(
        "227_12_agent_runtime_cost_side_effect_observability.sql"
    )
    assert "227_01" not in MIGRATION.read_text()
    assert "227_07" not in MIGRATION.read_text()


def test_v5_removes_only_the_binding_gate_and_preserves_facts_and_fence() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE FUNCTION runtime_submit_ingress_v5" in sql
    assert "_agent_runtime_ingress_kill_epoch_context" in sql
    assert "_assert_agent_runtime_actor(FALSE)" in sql
    assert "agent_runtime_production_bindings" not in sql
    assert "production_not_ready" not in sql
    assert "agent_runtime_definition_facts" in sql
    assert "agent_runtime_catalog_facts" in sql
    assert "agent_runtime_effective_toolset_facts" in sql
    assert "submit_session_command" in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert f"runtime_submit_ingress_v5({SIGNATURE})" in sql.lower()


def test_permissions_and_failure_closed_rollback_contract() -> None:
    sql = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()
    assert "GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v5" in sql
    assert "everydayai_runtime,everydayai_wecom_runtime" in sql
    assert "GRANT EXECUTE ON FUNCTION get_agent_runtime_ingress_capability" in sql
    assert "AR_17_4_ROLLBACK_BLOCKED_INGRESS_FACTS" in rollback
    assert "DROP FUNCTION runtime_submit_ingress_v5" in rollback
    assert "DROP FUNCTION get_agent_runtime_ingress_capability" in rollback
    assert "DROP FUNCTION runtime_submit_ingress_v4" not in rollback
    assert "DROP FUNCTION runtime_submit_ingress_v3" not in rollback
    assert "DROP TABLE" not in rollback
    assert "227_01" not in rollback and "227_07" not in rollback
