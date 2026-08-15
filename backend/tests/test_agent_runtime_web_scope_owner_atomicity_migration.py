from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = (ROOT / "migrations/228_08j_agent_runtime_web_scope_owner_atomicity.sql").read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_08j_agent_runtime_web_scope_owner_atomicity_rollback.sql"
).read_text()


def test_legacy_user_scopes_are_audited_backfilled_and_guarded() -> None:
    assert "CREATE TABLE agent_runtime_conversation_scope_adoptions" in SQL
    assert "prior_scope_id TEXT" in SQL
    assert "NULLIF(BTRIM(scope_id), '') IS NULL" in SQL
    assert "source = 'web'" in SQL
    assert "source <> 'web'" in SQL
    assert "SET scope_id = adoption.adopted_scope_id" in SQL
    assert "conversations_user_scope_id_matches_owner_check" in SQL
    assert "scope_id IS NOT NULL" in SQL
    assert "scope_id = user_id::TEXT" in SQL


def test_runtime_required_web_task_starts_unclaimable_by_actor() -> None:
    pending = "{\"actor\":false,\"runtime\":false,\"runtime_pending\":true}"
    assert pending in SQL
    assert "'runtime_pending', FALSE" in SQL
    assert "CREATE OR REPLACE FUNCTION mark_prepared_task_runtime_owned" in SQL
    assert "CREATE OR REPLACE FUNCTION runtime_submit_ingress_v6_required" in SQL
    assert (
        "PUBLIC, everydayai_worker, everydayai_runtime, everydayai_wecom_runtime"
        in SQL
    )


def test_rollback_restores_prior_scope_and_function_contracts() -> None:
    assert "SET scope_id = adoption.prior_scope_id" in ROLLBACK
    assert "DROP CONSTRAINT conversations_user_scope_id_matches_owner_check" in ROLLBACK
    assert "DROP TABLE agent_runtime_conversation_scope_adoptions" in ROLLBACK
    assert "CREATE OR REPLACE FUNCTION mark_prepared_task_runtime_owned" in ROLLBACK
    assert "CREATE OR REPLACE FUNCTION runtime_submit_ingress_v6_required" in ROLLBACK
    assert "runtime_pending" not in ROLLBACK
