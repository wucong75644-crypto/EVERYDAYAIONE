from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_14_agent_runtime_owner_transition.sql"
ROLLBACK = ROOT / "migrations/rollback/227_14_agent_runtime_owner_transition_rollback.sql"
SIGNATURE = "uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb"


def test_22714_is_the_next_immutable_additive_lane() -> None:
    identities = [item.identity for item in discover_migrations(ROOT / "migrations")]
    assert identities.index("227_14_agent_runtime_owner_transition.sql") > identities.index(
        "227_13_agent_runtime_additive_ingress_compatibility.sql"
    )
    assert "227_01" not in MIGRATION.read_text()
    assert "227_07" not in MIGRATION.read_text()


def test_v5_removes_only_the_binding_gate_and_preserves_facts_and_fence() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE FUNCTION restore_prepared_task_to_legacy_actor" in sql
    assert "CREATE FUNCTION mark_prepared_task_runtime_owned" in sql
    assert "CREATE FUNCTION runtime_submit_ingress_v5_owner_transition" in sql
    assert "CREATE FUNCTION enqueue_wecom_runtime_turn_v6" in sql
    assert "UPDATE tasks SET delivery_context" in sql
    assert "SET search_path = pg_catalog, public" in sql or "SET search_path=pg_catalog,public" in sql
    assert "runtime_submit_ingress_v5(" in sql


def test_permissions_and_failure_closed_rollback_contract() -> None:
    sql = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()
    assert "GRANT EXECUTE ON FUNCTION restore_prepared_task_to_legacy_actor" in sql
    assert "everydayai_runtime,everydayai_wecom_runtime" in sql
    assert "runtime_submit_ingress_v5_owner_transition(" in sql
    assert "AR_17_4_ROLLBACK_BLOCKED_OWNER_TRANSITIONS" in rollback
    assert "DROP FUNCTION mark_prepared_task_runtime_owned" in rollback
    assert "DROP FUNCTION runtime_submit_ingress_v5(" not in rollback
    assert "DROP FUNCTION runtime_submit_ingress_v4" not in rollback
    assert "DROP FUNCTION runtime_submit_ingress_v3" not in rollback
    assert "DROP TABLE" not in rollback
    assert "227_01" not in rollback and "227_07" not in rollback


def test_owner_transition_is_mutually_exclusive_with_legacy_discovery() -> None:
    sql = MIGRATION.read_text()
    discovery = (
        ROOT / "migrations/218_suspended_organization_execution_fence.sql"
    ).read_text()

    assert "'{\"actor\":false,\"runtime\":true}'::JSONB" in sql
    assert "'{\"actor\":true,\"runtime\":false}'::JSONB" in sql
    assert "task.delivery_context @> '{\"actor\": true}'::JSONB" in discovery
