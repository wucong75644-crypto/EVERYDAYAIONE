from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_07_agent_runtime_kill_epoch_fence.sql"
ROLLBACK = ROOT / "migrations/rollback/227_07_agent_runtime_kill_epoch_fence_rollback.sql"


def test_b_additive_lane_and_security_contract() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "227_02" not in sql and "227_03" not in sql
    assert "227_04" not in sql and "227_05" not in sql and "227_06" not in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert "_agent_runtime_kill_epoch_context" in sql
    assert "RUNTIME_KILL_EPOCH_FENCED" in sql
    assert "RUNTIME_PROVIDER_KILL_FENCED" in sql
    assert "RUNTIME_CAPABILITY_KILL_FENCED" in sql
    assert "RUNTIME_REVISION_FENCED" in sql
    assert "claim_ready_agent_action_snapshots_v2" in sql
    assert "gate_agent_action_dispatch_v2" in sql
    assert "everydayai_agent_runtime_worker" in sql
    assert "REVOKE EXECUTE ON FUNCTION claim_ready_agent_actions" in sql


def test_b_rollback_is_guarded_and_does_not_rewrite_prior_lanes() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AR_17_3_B_ROLLBACK_BLOCKED_ACTIVE_OWNER_FENCE" in rollback
    assert "DROP FUNCTION _agent_runtime_kill_epoch_context" in rollback
    assert "227_02" not in rollback and "227_03" not in rollback
    assert "227_04" not in rollback and "227_05" not in rollback and "227_06" not in rollback
