from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_27_agent_runtime_child_run_recursive_cancel.sql"
ROLLBACK = ROOT / "migrations/rollback/227_27_agent_runtime_child_run_recursive_cancel_rollback.sql"
PARENT = ROOT / "migrations/227_25_agent_runtime_model_gateway_cancel_fence.sql"


def _body(path: Path, name: str) -> str:
    text = path.read_text()
    match = re.search(
        rf"CREATE (?:OR REPLACE )?FUNCTION {name}\([^$]+?\$\$(.*?)\$\$;",
        text, re.S,
    )
    assert match is not None
    return " ".join(match.group(1).split())


def test_b6_migration_has_recursive_facts_and_narrow_rpcs() -> None:
    sql = MIGRATION.read_text()
    for value in (
        "agent_runtime_child_run_cancel_intents",
        "FORCE ROW LEVEL SECURITY",
        "create_agent_child_run_strict_v2",
        "read_agent_child_run_binding_v3",
        "claim_next_agent_child_run_cancel_intent_v1",
        "get_claimed_agent_child_run_cancel_intent_v1",
        "apply_agent_child_run_cancel_intent_v1",
        "finalize_agent_action_child_cancel_v1",
        "_seed_agent_child_cancel_intents_v1",
        "reconciliation_parent_run_state_version",
        "_agent_runtime_kill_epoch_context",
    ):
        assert value in sql
    assert "status IN('requested','applied')" in sql
    assert "pending>0 OR descendants>0 THEN kind:=NULL" in sql
    assert "blocking_action_count=0" not in _body(
        MIGRATION, "finalize_agent_action_child_cancel_v1",
    )
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "TO everydayai_agent_runtime_worker" in sql


def test_b6_revokes_old_bypasses_and_rollback_is_exact() -> None:
    sql = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()
    for name in (
        "create_agent_child_run_strict(",
        "read_agent_child_run_strict_v2(",
        "aggregate_agent_child_run_strict(",
        "cancel_agent_child_run_strict_v2(",
    ):
        assert name in sql
    assert "AGENT_CHILD_CANCEL_ROLLBACK_PENDING_FACTS" in rollback
    assert _body(ROLLBACK, "cancel_agent_run") == _body(
        PARENT, "cancel_agent_run",
    )
    assert rollback.index("DROP FUNCTION finalize_agent_action_child_cancel_v1") < rollback.index(
        "DROP TABLE agent_runtime_child_run_cancel_intents"
    )
