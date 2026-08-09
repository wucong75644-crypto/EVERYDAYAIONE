from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "migrations/227_31_agent_runtime_scheduled_terminal_intents.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_31_agent_runtime_scheduled_terminal_intents_rollback.sql").read_text()


def test_terminal_intent_is_additive_durable_and_worker_narrow() -> None:
    assert "AFTER UPDATE OF status ON agent_runs" in MIGRATION
    assert "OLD.status IN('completed','failed','cancelled')" in MIGRATION
    assert "NEW.run_kind<>'scheduled'" in MIGRATION
    assert "(q.task_id,q.org_id,q.status)" in MIGRATION
    assert "IS DISTINCT FROM(b.scheduled_task_id,b.org_id,'running')" in MIGRATION
    assert "FOR UPDATE SKIP LOCKED LIMIT 1" in MIGRATION
    assert "claim_lease_expires_at<=clock_timestamp()" in MIGRATION
    assert "PERFORM _assert_agent_runtime_actor(TRUE)" in MIGRATION
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION claim_next_agent_runtime_scheduled_finalization_v1" in MIGRATION
    assert "TO everydayai_agent_runtime_worker" in MIGRATION
    assert "UPDATE scheduled_task_runs SET status" not in MIGRATION
    assert "UPDATE scheduled_tasks SET" not in MIGRATION
    assert "credits_history" not in MIGRATION


def test_rollback_guards_history_before_exact_removal() -> None:
    guard = ROLLBACK.index("AGENT_RUNTIME_SCHEDULED_FINALIZATION_ROLLBACK_FACTS_EXIST")
    drop = ROLLBACK.index("DROP TABLE agent_runtime_scheduled_finalization_intents")
    assert guard < drop
    assert "r.status IN('completed','failed','cancelled')" in ROLLBACK
    assert "227_28" not in MIGRATION + ROLLBACK
    assert "227_29" not in MIGRATION + ROLLBACK
    assert "227_30" not in MIGRATION + ROLLBACK
