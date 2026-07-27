"""Static migration 217 security and atomic-owner contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PATHS = [
    ROOT / f"migrations/217_0{part}_agent_runtime_model_attempt_{name}.sql"
    for part, name in (
        (1, "foundation"),
        (2, "credits"),
        (3, "lifecycle"),
        (4, "reconciliation"),
    )
]
SQL = "\n".join(path.read_text() for path in PATHS)


def test_tables_force_rls_and_have_zero_direct_grants() -> None:
    for table in (
        "agent_model_attempts",
        "agent_model_credit_settlements",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in SQL
        assert f"REVOKE ALL ON TABLE {table}" in SQL
    assert not re.search(
        r"GRANT\s+(SELECT|INSERT|UPDATE|DELETE|ALL)\s+ON\s+(TABLE\s+)?"
        r"(agent_|users|credits_history|credit_transactions)",
        SQL,
        re.IGNORECASE,
    )


def test_worker_only_attempt_rpcs_and_cancel_signature_are_frozen() -> None:
    assert "TO everydayai_worker;" in SQL
    assert "TO everydayai_runtime, everydayai_wecom_runtime" not in SQL
    assert "CREATE OR REPLACE FUNCTION cancel_agent_run(" in SQL
    assert "p_run_id UUID, p_expected_state_version BIGINT, p_reason TEXT" in SQL
    original = (
        ROOT / "migrations/214_agent_runtime_run_lifecycle_rpcs.sql"
    ).read_text()
    for evidence in (
        "AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH",
        "tenant_org_id() IS DISTINCT FROM v_run.org_id",
        "member.status = 'active'",
    ):
        assert evidence in SQL and evidence in original


def test_tool_handoff_precedes_all_terminal_mutations() -> None:
    body = SQL.split(
        "CREATE FUNCTION _complete_model_attempt_without_actions", 1,
    )[1].split("CREATE FUNCTION _fail_model_attempt_and_step", 1)[0]
    handoff = body.index("p_stop_reason = 'tool_calls'")
    assert handoff < body.index("UPDATE agent_model_attempts")
    assert handoff < body.index("_settle_agent_model_credits")
    assert "complete_model_step(" not in SQL


def test_reconcile_has_no_attempt_token_prewrite() -> None:
    body = SQL.split("CREATE FUNCTION resolve_model_attempt", 1)[1].split(
        "CREATE FUNCTION _adjust_model_attempt_credits", 1,
    )[0]
    assert "SET execution_token" not in body
    assert "_complete_model_attempt_without_actions(" in body
    assert "p_reconciliation_token" in body


def test_adjustment_helper_is_never_granted() -> None:
    assert "CREATE FUNCTION _adjust_model_attempt_credits(" in SQL
    assert "REVOKE ALL ON FUNCTION" in SQL
    assert not re.search(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION(?:(?!;).)*"
        r"_adjust_model_attempt_credits",
        SQL,
        re.IGNORECASE | re.DOTALL,
    )


def test_terminal_and_late_replay_identities_are_persisted_and_compared() -> None:
    for column in (
        "late_actual_credits",
        "late_ambiguity_evidence",
        "terminal_error_code",
    ):
        assert column in PATHS[0].read_text()
    for fact in (
        "v_attempt.response_receipt IS DISTINCT FROM p_response_receipt",
        "v_attempt.usage IS DISTINCT FROM p_usage",
        "v_step.stop_reason IS DISTINCT FROM p_stop_reason",
        "v_settlement.settled_credits IS DISTINCT FROM p_actual_credits",
        "v_attempt.terminal_error_code IS DISTINCT FROM p_error_code",
        "v_attempt.retry_disposition IS DISTINCT FROM p_retry_disposition",
    ):
        assert fact in PATHS[1].read_text()
    reconciliation = PATHS[3].read_text()
    for fact in (
        "v_settlement.adjustment_key IS DISTINCT FROM v_key",
        "v_settlement.adjusted_credits IS DISTINCT FROM p_actual_credits",
        "v_attempt.late_outcome IS DISTINCT FROM p_late_outcome",
        "v_attempt.response_receipt IS DISTINCT FROM p_response_receipt",
        "v_attempt.late_ambiguity_evidence IS DISTINCT FROM",
        "p_ambiguity_evidence",
        "v_attempt.late_actual_credits IS DISTINCT FROM p_actual_credits",
    ):
        assert fact in reconciliation


def test_migration_files_stay_under_structure_limit() -> None:
    assert all(len(path.read_text().splitlines()) <= 500 for path in PATHS)
