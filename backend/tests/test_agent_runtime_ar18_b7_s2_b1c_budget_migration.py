from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_34_agent_runtime_scheduled_run_credit_budget.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_34_agent_runtime_scheduled_run_credit_budget_rollback.sql").read_text()


def test_budget_contract_is_additive_narrow_and_failure_closed() -> None:
    assert "CREATE TABLE agent_runtime_scheduled_run_credit_budgets" in SQL
    assert "CREATE TABLE agent_runtime_scheduled_model_credit_allocations" in SQL
    assert "CREATE TABLE agent_runtime_scheduled_credit_overages" in SQL
    assert SQL.count("FORCE ROW LEVEL SECURITY") == 3
    assert "CREATE FUNCTION prepare_model_attempt(" in SQL
    assert "_prepare_model_attempt_without_scheduled_budget_v1" in SQL
    assert "AGENT_RUNTIME_SCHEDULED_BUDGET_EXHAUSTED" in SQL
    assert "agent_runtime_scheduled_run_bindings" in SQL
    assert "config_snapshot->'scheduled_budget'" in SQL
    assert "budget_snapshot" in SQL
    assert "GRANT EXECUTE ON FUNCTION prepare_model_attempt(" in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "GRANT SELECT" not in SQL
    assert "SET search_path=pg_catalog,public" in SQL
    assert "AGENT_RUNTIME_SCHEDULED_BUDGET_HISTORICAL_FACTS_EXIST" in SQL
    assert "INSERT INTO agent_runtime_scheduled_run_credit_budgets(runtime_run_id" not in SQL.split(
        "CREATE FUNCTION prepare_model_attempt(", 1
    )[0]


def test_budget_rollback_is_guarded_and_restores_prepare_contract() -> None:
    assert "AGENT_RUNTIME_SCHEDULED_BUDGET_ROLLBACK_FACTS_EXIST" in ROLLBACK
    assert ROLLBACK.index("AGENT_RUNTIME_SCHEDULED_BUDGET_ROLLBACK_FACTS_EXIST") < ROLLBACK.index("DROP TABLE")
    assert "GRANT EXECUTE ON FUNCTION prepare_model_attempt(" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_model_credit_allocations" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_credit_overages" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_run_credit_budgets" in ROLLBACK


def test_budget_projection_preserves_existing_wallet_as_source_of_truth() -> None:
    assert "UPDATE users" not in SQL
    assert "INSERT INTO credits_history" not in SQL
    assert "INSERT INTO credit_transactions" not in SQL
    assert "_settle_agent_model_credits_without_scheduled_budget_v1" in SQL
    assert "_release_agent_model_credits_without_scheduled_budget_v1" in SQL
    assert "_adjust_model_attempt_credits_without_scheduled_budget_v1" in SQL
    assert "agent_model_credit_settlements" in SQL


def test_overage_fact_is_secret_free_immutable_and_conservative() -> None:
    assert "provider_actual_credits=user_charge_credits+overage_credits" in SQL
    assert "AGENT_RUNTIME_SCHEDULED_OVERAGE_IMMUTABLE" in SQL
    assert "LEAST(p_actual_credits,GREATEST" in SQL
    for forbidden in ("provider_payload", "credential", "api_key", "storage_ref"):
        assert forbidden not in SQL


def test_pending_replay_and_frozen_budget_cannot_regress() -> None:
    pending_guard = SQL.index("IF c.status='adjustment_pending' THEN")
    legacy_adjust = SQL.index("result:=_adjust_model_attempt_credits_without_scheduled_budget_v1")
    assert pending_guard < legacy_adjust
    assert "max_value:=(e.budget_snapshot->>'max_credits')::INTEGER" in SQL
    assert re.search(r"\bt\.max_credits\b", SQL) is None
