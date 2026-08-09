from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_30_agent_runtime_scheduled_submission.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/227_30_agent_runtime_scheduled_submission_rollback.sql"
).read_text()


def test_submission_contract_is_narrow_hidden_and_disabled() -> None:
    assert "mode TEXT NOT NULL DEFAULT 'disabled'" in SQL
    assert "worker_claim_due_scheduled_executions_v1" in SQL
    assert "request_agent_runtime_scheduled_execution_v1" in SQL
    assert "create_runtime_scheduled_profile_after_insert" in SQL
    assert "AGENT_RUNTIME_SCHEDULED_PROFILE_BINDING_INCOMPLETE" in SQL
    assert "scheduled-manual-request:" in SQL
    assert "SCHEDULED_MANUAL_IDEMPOTENCY_CONFLICT" in SQL
    assert "read_agent_runtime_scheduled_submission_v1" in SQL
    assert "source,'scheduler'" in SQL or "'scheduler','user'" in SQL
    assert "NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles" in SQL
    assert "SCHEDULED_RUN_RUNTIME_OWNED" in SQL
    assert "worker_list_stale_scheduled_tasks" in SQL
    assert "RETURN jsonb_build_object('outcome','runtime_owned')" in SQL
    assert "_agent_runtime_scheduled_submission_enabled() OR NOT EXISTS(" in SQL
    assert "TO everydayai_worker" in SQL
    assert "GRANT EXECUTE ON FUNCTION request_agent_runtime_scheduled_execution_v1" in SQL
    assert "GRANT SELECT" not in SQL
    assert "SET search_path=pg_catalog,public" in SQL


def test_submission_rollback_is_guarded_and_restores_legacy_entrypoints() -> None:
    assert "SCHEDULED_SUBMISSION_ROLLBACK_FACTS_EXIST" in ROLLBACK
    assert ROLLBACK.index("ROLLBACK_FACTS_EXIST") < ROLLBACK.index(
        "DROP TABLE agent_runtime_scheduled_submission_intents"
    )
    assert "CREATE OR REPLACE FUNCTION worker_claim_due_scheduled_tasks" in ROLLBACK
    assert "CREATE OR REPLACE FUNCTION worker_create_scheduled_run" in ROLLBACK
    assert "NOT IN('user','continuation')" in ROLLBACK
