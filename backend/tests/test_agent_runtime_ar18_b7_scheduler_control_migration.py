from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_28_agent_runtime_scheduler_control.sql"
ROLLBACK = ROOT / "migrations/rollback/227_28_agent_runtime_scheduler_control_rollback.sql"


def test_b7_control_plane_is_additive_and_worker_rpc_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "agent_runtime_scheduler_operation_intents" in sql
    assert "agent_runtime_scheduler_operation_receipts" in sql
    assert "scheduled_tasks" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert sql.count("SET search_path") >= 8
    assert "_assert_agent_runtime_actor(TRUE)" in sql
    assert "agent_policy_receipts" in sql and "agent_action_dispatch_intents" in sql
    assert "_runtime_scheduler_push_target_allowed" in sql
    assert "_runtime_scheduler_actor_allowed" in sql
    assert "_runtime_scheduler_operation_allowed" in sql
    assert "get_agent_runtime_scheduled_task_resume_context_v1" in sql
    assert "pg_timezone_names" in sql
    assert "department.type IN ('ops','finance','warehouse','service','design','hr')" in sql
    assert "NOT (value ? 'next_run_at')" in sql
    assert "next_run_at=NULL" in sql
    assert "next_run_at=p_resume_next_run_at" in sql
    for field in ("request_hash", "payload_hash", "execution_token", "tenant_kill_epoch", "provider_revision", "state_version"):
        assert field in sql
    assert "GRANT EXECUTE" in sql
    assert "REVOKE EXECUTE ON FUNCTION runtime_mutate_scheduled_task" in sql
    assert "TO everydayai_agent_runtime_worker" in sql
    assert "TO everydayai_worker" not in sql
    assert "TO everydayai_runtime" not in sql


def test_b7_rollback_is_guarded_and_does_not_drop_scheduled_tasks() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AR_18_B7_ROLLBACK_BLOCKED_SCHEDULER_INTENTS" in rollback
    assert "DROP TABLE IF EXISTS scheduled_tasks" not in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduler_operation_receipts" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduler_operation_intents" in rollback
