from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_09_agent_runtime_claim_fence_ambiguity_fix.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_09_agent_runtime_claim_fence_ambiguity_fix_rollback.sql").read_text()


def test_claim_fence_fix_is_additive_and_qualified() -> None:
    assert "227_07" not in SQL
    assert "CREATE OR REPLACE FUNCTION claim_ready_agent_action_snapshots_v2" in SQL
    assert "CREATE OR REPLACE FUNCTION claim_ready_agent_actions_v2" in SQL
    assert "gate_control.org_id=action_row.org_id" in SQL
    assert "JOIN agent_runtime_tenant_gate_controls g ON g.org_id" not in SQL
    assert "SET search_path=pg_catalog,public" in SQL
    assert "RUNTIME_KILL_EPOCH_FENCED" in SQL
    assert "CREATE OR REPLACE FUNCTION" in ROLLBACK
