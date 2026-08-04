from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_15_agent_runtime_owner_rpc_acl_closure.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/227_15_agent_runtime_owner_rpc_acl_closure_rollback.sql"
)


def test_22715_is_additive_and_ordered_after_owner_transition() -> None:
    identities = [item.identity for item in discover_migrations(ROOT / "migrations")]
    assert identities.index("227_15_agent_runtime_owner_rpc_acl_closure.sql") > identities.index(
        "227_14_agent_runtime_owner_transition.sql"
    )
    assert "CREATE FUNCTION" not in MIGRATION.read_text()
    assert "DROP FUNCTION" not in MIGRATION.read_text()


def test_22715_exposes_only_atomic_channel_entrypoints() -> None:
    sql = MIGRATION.read_text()
    assert "runtime_submit_ingress_v5_owner_transition(" in sql
    assert "enqueue_wecom_runtime_turn_v6(" in sql
    assert "FROM everydayai_runtime, everydayai_wecom_runtime" in sql
    assert "FROM PUBLIC, everydayai_worker" in sql
    assert "TO everydayai_runtime;" in sql
    assert "TO everydayai_wecom_runtime;" in sql
    assert "SET LOCAL ROLE everydayai_owner" in sql


def test_22715_rollback_restores_only_prior_acl() -> None:
    rollback = ROLLBACK.read_text()
    assert "runtime_submit_ingress_v5(" in rollback
    assert "restore_prepared_task_to_legacy_actor(" in rollback
    assert "mark_prepared_task_runtime_owned(" in rollback
    assert "runtime_submit_ingress_v5_owner_transition(" in rollback
    assert "enqueue_wecom_runtime_turn_v6(" in rollback
    assert "DROP " not in rollback
    assert "DELETE " not in rollback
    assert "UPDATE " not in rollback
    assert "INSERT " not in rollback
