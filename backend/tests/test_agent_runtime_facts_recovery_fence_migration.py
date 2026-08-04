from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_08_agent_runtime_facts_recovery_fence.sql"
ROLLBACK = ROOT / "migrations/rollback/227_08_agent_runtime_facts_recovery_fence_rollback.sql"


def test_c_additive_fact_fences_cover_all_runtime_domains() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert all(f"227_0{i}" not in sql for i in range(2, 8))
    assert "agent_runtime_provider_submission_facts" in sql
    assert "agent_runtime_scheduler_cas_facts" in sql
    assert "agent_sandbox_jobs" in sql
    assert "agent_runtime_child_run_epoch_fence" in sql
    assert "RUNTIME_KILL_EPOCH_FENCED" in sql
    assert "RUNTIME_PROVIDER_KILL_FENCED" in sql
    assert "RUNTIME_CAPABILITY_KILL_FENCED" in sql
    assert "RUNTIME_REVISION_FENCED" in sql
    assert "SET search_path=pg_catalog,public" in sql


def test_c_rollback_is_guarded() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AR_17_3_C_ROLLBACK_BLOCKED_ACTIVE_OWNER_FENCE" in rollback
    assert "DROP TRIGGER agent_runtime_provider_facts_epoch_fence" in rollback
    assert "DROP TRIGGER agent_runtime_scheduler_facts_epoch_fence" in rollback
