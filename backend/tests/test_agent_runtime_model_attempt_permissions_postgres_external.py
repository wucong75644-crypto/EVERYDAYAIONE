"""Real PostgreSQL migration ordering, permissions, RLS, and rollback contract."""

from __future__ import annotations

import psycopg
import pytest

from tests.test_agent_runtime_model_attempt_postgres_external import (
    MIGRATIONS,
    ROLLBACKS,
    claim_unknown_attempt,
    create_running_step,
    decoded,
    ensure_database,
    execute,
    execute_file,
    model_state_snapshot,
    prepare_attempt,
    runtime,
    system_state_snapshot,
    worker,
)

pytestmark = pytest.mark.external


def test_permissions_force_rls_and_no_table_grants() -> None:
    ensure_database()
    rows = execute(
        """
        SELECT relname,relforcerowsecurity
          FROM pg_class
         WHERE relname IN ('agent_model_attempts','agent_model_credit_settlements')
         ORDER BY relname
        """
    )
    grants = execute(
        """
        SELECT count(*) FROM information_schema.role_table_grants
         WHERE table_name IN ('agent_model_attempts','agent_model_credit_settlements')
           AND grantee IN (
             'everydayai_runtime','everydayai_wecom_runtime','everydayai_worker',
             'everydayai_sync','everydayai'
           )
        """
    )[0][0]
    assert rows == [
        ("agent_model_attempts", True),
        ("agent_model_credit_settlements", True),
    ]
    assert grants == 0


def test_cancel_grants_and_personal_scope_contract_are_preserved() -> None:
    ensure_database()
    grants = execute(
        """
        SELECT role_name,has_function_privilege(
            role_name,'cancel_agent_run(uuid,bigint,text)','EXECUTE'
        )
          FROM unnest(ARRAY[
            'everydayai_runtime','everydayai_wecom_runtime','everydayai_worker',
            'everydayai_sync','everydayai'
          ]) role_name
        """
    )
    assert grants == [
        ("everydayai_runtime", True),
        ("everydayai_wecom_runtime", True),
        ("everydayai_worker", True),
        ("everydayai_sync", False),
        ("everydayai", False),
    ]
    facts = create_running_step()
    cancelled = decoded(
        runtime(
            "SELECT cancel_agent_run(%s,%s,'runtime_cancel');",
            facts["user_id"],
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    assert cancelled["outcome"] == "cancelled"


def test_worker_cannot_call_internal_adjustment_helper() -> None:
    ensure_database()
    privileges = execute(
        """
        SELECT role_name,has_function_privilege(
            role_name,'_adjust_model_attempt_credits(uuid,text,integer)','EXECUTE'
        )
          FROM unnest(ARRAY[
            'everydayai_runtime','everydayai_wecom_runtime','everydayai_worker',
            'everydayai_sync','everydayai'
          ]) role_name
        """
    )
    assert privileges == [
        ("everydayai_runtime", False),
        ("everydayai_wecom_runtime", False),
        ("everydayai_worker", False),
        ("everydayai_sync", False),
        ("everydayai", False),
    ]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker(
            "SELECT _adjust_model_attempt_credits(%s,%s,7);",
            ("11111111-1111-1111-1111-111111111111", "a" * 64),
        )


def test_reconcile_completed_is_atomic() -> None:
    ensure_database()
    facts = create_running_step()
    attempt, claimed = claim_unknown_attempt(facts)
    readback = decoded(
        worker("SELECT get_model_attempt(%s);", (attempt["attempt_id"],))[-1][0]
    )
    resolved = decoded(
        worker(
            """
            SELECT resolve_model_attempt(
             %s,%s,%s,%s,%s,'completed',%s,'{}',%s,'final',NULL,'{}',8
            );
            """,
            (
                attempt["attempt_id"], facts["run_token"],
                claimed["execution_token"], claimed["state_version"],
                facts["step_version"], "a" * 64, "b" * 64,
            ),
        )[-1][0]
    )
    assert readback["attempt"]["status"] == "unknown"
    assert resolved["outcome"] == "completed"


def test_reconcile_failed_is_atomic() -> None:
    ensure_database()
    facts = create_running_step()
    attempt, claimed = claim_unknown_attempt(facts)
    resolved = decoded(
        worker(
            """
            SELECT resolve_model_attempt(
             %s,%s,%s,%s,%s,'failed',%s,NULL,NULL,NULL,NULL,'{}',0,
             'provider_rejected'
            );
            """,
            (
                attempt["attempt_id"], facts["run_token"],
                claimed["execution_token"], claimed["state_version"],
                facts["step_version"], "a" * 64,
            ),
        )[-1][0]
    )
    statuses = execute(
        """
        SELECT attempt.status,step.status FROM agent_model_attempts attempt
        JOIN agent_model_steps step ON step.id=attempt.model_step_id
        WHERE attempt.id=%s
        """,
        (attempt["attempt_id"],),
    )[0]
    assert resolved["outcome"] == "failed"
    assert statuses == ("failed", "failed")


def test_failed_identical_replay_is_idempotent() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts)
    values = (
        attempt["attempt_id"], facts["run_token"],
        attempt["state_version"], facts["step_version"],
        "a" * 64, "provider_rejected", "forbidden",
    )
    first = decoded(
        worker(
            "SELECT fail_model_attempt_and_step(%s,%s,%s,%s,%s,%s,%s);",
            values,
        )[-1][0]
    )
    before = system_state_snapshot(facts, attempt["attempt_id"])
    replay = decoded(
        worker(
            "SELECT fail_model_attempt_and_step(%s,%s,%s,%s,%s,%s,%s);",
            (
                attempt["attempt_id"], facts["run_token"],
                attempt["state_version"] + 1, facts["step_version"] + 1,
                "a" * 64, "provider_rejected", "forbidden",
            ),
        )[-1][0]
    )
    assert first["outcome"] == "failed"
    assert replay["outcome"] == "already_failed"
    assert system_state_snapshot(facts, attempt["attempt_id"]) == before


@pytest.mark.parametrize(
    ("error_code", "retry_disposition"),
    (("different_error", "forbidden"), ("provider_rejected", "retry_safe")),
)
def test_failed_conflicting_replay_has_zero_mutation(
    error_code: str, retry_disposition: str,
) -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts)
    decoded(
        worker(
            "SELECT fail_model_attempt_and_step(%s,%s,%s,%s,%s,%s,%s);",
            (
                attempt["attempt_id"], facts["run_token"],
                attempt["state_version"], facts["step_version"],
                "a" * 64, "provider_rejected", "forbidden",
            ),
        )[-1][0]
    )
    before = system_state_snapshot(facts, attempt["attempt_id"])
    conflict = decoded(
        worker(
            "SELECT fail_model_attempt_and_step(%s,%s,%s,%s,%s,%s,%s);",
            (
                attempt["attempt_id"], facts["run_token"],
                attempt["state_version"] + 1, facts["step_version"] + 1,
                "a" * 64, error_code, retry_disposition,
            ),
        )[-1][0]
    )
    assert conflict["outcome"] == "terminal_conflict"
    assert system_state_snapshot(facts, attempt["attempt_id"]) == before


@pytest.mark.parametrize(
    ("case", "request_hash", "step_version", "stop_reason", "expected"),
    (
        ("handoff", "a" * 64, 0, "tool_calls", "handoff_tool_calls"),
        ("stale_step", "a" * 64, 99, "final", "stale_version"),
        ("request_conflict", "d" * 64, 0, "final", "request_hash_conflict"),
    ),
)
def test_reconcile_nonterminal_outcomes_have_zero_mutation(
    case: str, request_hash: str, step_version: int,
    stop_reason: str, expected: str,
) -> None:
    ensure_database()
    facts = create_running_step()
    attempt, claimed = claim_unknown_attempt(facts)
    before = model_state_snapshot(facts, attempt["attempt_id"])
    outcome = decoded(
        worker(
            """
            SELECT resolve_model_attempt(
             %s,%s,%s,%s,%s,'completed',%s,'{}',%s,%s,NULL,'{}',8
            );
            """,
            (
                attempt["attempt_id"], facts["run_token"],
                claimed["execution_token"], claimed["state_version"],
                step_version, request_hash, "b" * 64, stop_reason,
            ),
        )[-1][0]
    )
    assert model_state_snapshot(facts, attempt["attempt_id"]) == before
    assert outcome["outcome"] == expected
    if case == "handoff":
        renewed = decoded(
            worker(
                "SELECT renew_model_attempt_reconciliation(%s,%s,%s,120);",
                (
                    attempt["attempt_id"], facts["run_token"],
                    claimed["execution_token"],
                ),
            )[-1][0]
        )
        assert renewed["outcome"] == "renewed"


def test_reconcile_receipt_terminal_conflict_has_zero_mutation() -> None:
    ensure_database()
    facts = create_running_step()
    attempt, claimed = claim_unknown_attempt(facts)
    execute(
        """
        UPDATE agent_model_credit_settlements
           SET status='settled',effective_attempt_id=%s,
               settlement_key=%s,response_hash=%s,settled_credits=8
         WHERE model_step_id=%s
        """,
        (
            attempt["attempt_id"],
            f"{facts['step_id']}:{attempt['attempt_id']}",
            "c" * 64,
            facts["step_id"],
        ),
    )
    before = model_state_snapshot(facts, attempt["attempt_id"])
    conflict = decoded(
        worker(
            """
            SELECT resolve_model_attempt(
             %s,%s,%s,%s,%s,'completed',%s,'{}',%s,'final',NULL,'{}',8
            );
            """,
            (
                attempt["attempt_id"], facts["run_token"],
                claimed["execution_token"], claimed["state_version"],
                facts["step_version"], "a" * 64, "b" * 64,
            ),
        )[-1][0]
    )
    assert conflict["outcome"] == "terminal_conflict"
    assert model_state_snapshot(facts, attempt["attempt_id"]) == before


def test_late_adjustment_conflict_rolls_back_receipt() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts)
    decoded(
        worker(
            "SELECT cancel_agent_run(%s,%s,'late-conflict');",
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    execute(
        """
        UPDATE agent_model_credit_settlements
           SET status='settled',effective_attempt_id=%s,
               settlement_key=%s,response_hash=%s,settled_credits=8
         WHERE model_step_id=%s
        """,
        (
            attempt["attempt_id"],
            f"{facts['step_id']}:{attempt['attempt_id']}",
            "c" * 64,
            facts["step_id"],
        ),
    )
    before = model_state_snapshot(facts, attempt["attempt_id"])
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="AGENT_MODEL_ADJUSTMENT_CONFLICT",
    ):
        worker(
            """
            SELECT record_late_model_receipt(
             %s,'provider-late','{}',%s,'{}','completed','{}',7
            );
            """,
            (attempt["attempt_id"], "b" * 64),
        )
    assert model_state_snapshot(facts, attempt["attempt_id"]) == before


def test_real_rollback_reverse_order_and_reapply() -> None:
    ensure_database()
    facts = create_running_step()
    prepare_attempt(facts)
    with pytest.raises(Exception, match="ROLLBACK_FACTS_PRESENT"):
        execute_file(ROLLBACKS[2])
    execute(
        """
        SET ROLE everydayai_owner;
        TRUNCATE agent_model_credit_settlements,agent_model_attempts CASCADE;
        RESET ROLE;
        """
    )
    for rollback in ROLLBACKS:
        execute_file(rollback)
    assert execute(
        "SELECT to_regclass('public.agent_model_attempts'),"
        "to_regclass('public.agent_model_credit_settlements')"
    )[0] == (None, None)
    for migration in MIGRATIONS:
        execute_file(migration)
    assert execute(
        "SELECT to_regclass('public.agent_model_attempts'),"
        "to_regclass('public.agent_model_credit_settlements')"
    )[0] == ("agent_model_attempts", "agent_model_credit_settlements")
