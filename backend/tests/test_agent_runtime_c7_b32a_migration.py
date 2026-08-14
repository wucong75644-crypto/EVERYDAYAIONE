from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_17_agent_runtime_safe_policy_activation.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_17_agent_runtime_safe_policy_activation_rollback.sql").read_text()
SEED_ROLLBACK = (ROOT / "migrations/rollback/227_16_agent_runtime_safe_read_release_rollback.sql").read_text()


def test_safe_activation_migration_has_narrow_security_contract() -> None:
    assert "CREATE TABLE agent_safe_action_activations" in SQL
    assert "ENABLE ROW LEVEL SECURITY" in SQL
    assert "FORCE ROW LEVEL SECURITY" in SQL
    assert "SECURITY DEFINER" in SQL
    assert "SET search_path=pg_catalog,public" in SQL
    assert "session_user='everydayai_agent_runtime_worker'" in SQL
    assert "session_user='everydayai_authorization_worker'" in SQL
    assert "_agent_runtime_kill_epoch_context" in SQL
    assert "record_agent_policy_receipt" in SQL
    assert "gate_agent_action_dispatch_v2" not in SQL
    assert "TO everydayai_agent_runtime_worker,everydayai_authorization_worker" in SQL
    assert "everydayai_worker" in SQL
    assert "TO everydayai_worker" not in SQL


def test_safe_activation_rejects_all_unsafe_or_unfenced_facts() -> None:
    for fact in (
        "safety_level'<>'safe'", "side_effect'<>'none'",
        "authorization_requirement'<>'none'", "request_hash_conflict",
        "ownership_lost", "stale_version", "scope_mismatch",
        "toolset_mismatch", "capability_mismatch", "owner_fence_missing",
    ):
        assert fact in SQL
    assert "SAFE_LOCAL_READ" in SQL
    assert "execution_token" in SQL
    assert "tenant_kill_epoch" in SQL
    assert "capability_kill_epoch" in SQL


def test_rollbacks_are_guarded_before_destructive_statements() -> None:
    guard = ROLLBACK.index("AGENT_SAFE_ACTION_ACTIVATION_FACTS_EXIST")
    assert guard < ROLLBACK.index("REVOKE ALL ON FUNCTION")
    assert guard < ROLLBACK.index("DROP TABLE")
    release_guard = SEED_ROLLBACK.index(
        "AGENT_RUNTIME_SAFE_READ_RELEASE_FACTS_EXIST",
    )
    assert release_guard < SEED_ROLLBACK.index(
        "DELETE FROM agent_runtime_effective_toolset_facts",
    )
