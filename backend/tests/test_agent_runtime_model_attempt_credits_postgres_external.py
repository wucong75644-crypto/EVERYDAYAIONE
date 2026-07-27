"""Real PostgreSQL unique settlement and late-adjustment contracts for 217."""

from __future__ import annotations

import json

import psycopg
import pytest

from tests.test_agent_runtime_model_attempt_postgres_external import (
    create_running_step,
    decoded,
    ensure_database,
    execute,
    prepare_attempt,
    system_state_snapshot,
    worker,
)

pytestmark = pytest.mark.external


def ledger(facts: dict[str, object]) -> tuple[object, ...]:
    return execute(
        """
        SELECT users.credits,
               COALESCE(SUM(history.change_amount),0),
               (ARRAY_AGG(history.balance_after ORDER BY history.created_at DESC,
                                                   history.id DESC))[1],
               COUNT(history.id),
               transaction.status,
               settlement.status,
               settlement.reserved_credits,
               settlement.settled_credits,
               settlement.adjusted_credits
          FROM users
          JOIN agent_model_credit_settlements settlement
            ON settlement.billing_user_id=users.id
          LEFT JOIN credit_transactions transaction
            ON transaction.id=settlement.credit_transaction_id
          LEFT JOIN credits_history history ON history.user_id=users.id
         WHERE users.id=%s
         GROUP BY users.credits,transaction.status,settlement.status,
                  settlement.reserved_credits,settlement.settled_credits,
                  settlement.adjusted_credits
        """,
        (facts["user_id"],),
    )[0]


def complete_attempt(
    facts: dict[str, object], attempt: dict[str, object], actual: int,
) -> dict[str, object]:
    started = decoded(
        worker(
            "SELECT start_model_attempt_dispatch(%s,%s,%s,%s);",
            (
                attempt["attempt_id"],
                facts["run_token"],
                attempt["state_version"],
                "a" * 64,
            ),
        )[-1][0]
    )
    return decoded(
        worker(
            """
            SELECT complete_model_attempt_without_actions(
             %s,%s,%s,%s,%s,'{}',%s,'final',NULL,
             '{"input_tokens":2,"output_tokens":3}',%s
            );
            """,
            (
                attempt["attempt_id"],
                facts["run_token"],
                started["state_version"],
                facts["step_version"],
                "a" * 64,
                "b" * 64,
                actual,
            ),
        )[-1][0]
    )


def test_reserve_and_replay_preserve_ledger_conservation() -> None:
    ensure_database()
    facts = create_running_step()
    key = "reserve-replay"
    attempt = prepare_attempt(facts, reserve=20, idempotency_key=key)
    before_replay = ledger(facts)
    replay = prepare_attempt(facts, reserve=20, idempotency_key=key)
    assert replay["outcome"] == "already_prepared"
    assert replay["attempt_id"] == attempt["attempt_id"]
    assert ledger(facts) == before_replay
    assert before_replay == (80, -20, 80, 1, "pending", "reserved", 20, 0, 0)


def test_reserve_failure_rolls_back_balance_history_and_attempt() -> None:
    ensure_database()
    facts = create_running_step()
    execute(
        """
        CREATE FUNCTION ar11_reject_credit_transaction()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'AR11_INJECTED_LEDGER_FAILURE'; END $$;
        CREATE TRIGGER ar11_reject_credit_transaction
        BEFORE INSERT ON credit_transactions
        FOR EACH ROW EXECUTE FUNCTION ar11_reject_credit_transaction();
        """
    )
    try:
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="AR11_INJECTED_LEDGER_FAILURE",
        ):
            prepare_attempt(facts, reserve=20)
    finally:
        execute(
            """
            DROP TRIGGER ar11_reject_credit_transaction ON credit_transactions;
            DROP FUNCTION ar11_reject_credit_transaction();
            """
        )
    assert execute(
        """
        SELECT users.credits,
               (SELECT count(*) FROM credits_history WHERE user_id=users.id),
               (SELECT count(*) FROM agent_model_attempts WHERE model_step_id=%s),
               (SELECT count(*) FROM agent_model_credit_settlements
                 WHERE model_step_id=%s)
          FROM users WHERE id=%s
        """,
        (facts["step_id"], facts["step_id"], facts["user_id"]),
    )[0] == (100, 0, 0, 0)


def test_reserve_settle_and_replay_are_exactly_once() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts, reserve=20)
    completed = complete_attempt(facts, attempt, 12)
    before_replay = ledger(facts)
    replay = decoded(
        worker(
            """
            SELECT complete_model_attempt_without_actions(
             %s,%s,%s,%s,%s,'{}',%s,'final',NULL,
             '{"input_tokens":2,"output_tokens":3}',12
            );
            """,
            (
                attempt["attempt_id"],
                facts["run_token"],
                attempt["state_version"] + 2,
                facts["step_version"] + 1,
                "a" * 64,
                "b" * 64,
            ),
        )[-1][0]
    )
    assert completed.get("settlement_outcome") == "settled", completed
    assert replay["outcome"] == "already_completed"
    assert ledger(facts) == before_replay
    assert before_replay == (88, -12, 88, 2, "confirmed", "settled", 20, 12, 0)


def test_settle_zero_and_replay_preserve_ledger_conservation() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts, reserve=20)
    completed = complete_attempt(facts, attempt, 0)
    before_replay = ledger(facts)
    replay = decoded(
        worker(
            """
            SELECT complete_model_attempt_without_actions(
             %s,%s,%s,%s,%s,'{}',%s,'final',NULL,
             '{"input_tokens":2,"output_tokens":3}',0
            );
            """,
            (
                attempt["attempt_id"],
                facts["run_token"],
                attempt["state_version"] + 2,
                facts["step_version"] + 1,
                "a" * 64,
                "b" * 64,
            ),
        )[-1][0]
    )
    assert completed["settlement_outcome"] == "settled"
    assert replay["outcome"] == "already_completed"
    assert ledger(facts) == before_replay
    assert before_replay == (100, 0, 100, 2, "refunded", "settled", 20, 0, 0)


@pytest.mark.parametrize(
    ("usage", "actual", "stop_reason", "provider_reason"),
    (
        ('{"input_tokens":9,"output_tokens":3}', 12, "final", None),
        ('{"input_tokens":2,"output_tokens":3}', 11, "final", None),
        ('{"input_tokens":2,"output_tokens":3}', 12, "structured_final", None),
        ('{"input_tokens":2,"output_tokens":3}', 12, "final", "different"),
    ),
)
def test_completed_conflicting_replay_has_zero_mutation(
    usage: str, actual: int, stop_reason: str,
    provider_reason: str | None,
) -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts, reserve=20)
    complete_attempt(facts, attempt, 12)
    before = system_state_snapshot(facts, attempt["attempt_id"])
    conflict = decoded(
        worker(
            """
            SELECT complete_model_attempt_without_actions(
             %s,%s,%s,%s,%s,'{}',%s,%s,%s,%s,%s
            );
            """,
            (
                attempt["attempt_id"], facts["run_token"],
                attempt["state_version"] + 2, facts["step_version"] + 1,
                "a" * 64, "b" * 64, stop_reason, provider_reason,
                usage, actual,
            ),
        )[-1][0]
    )
    assert conflict["outcome"] == "terminal_conflict"
    assert system_state_snapshot(facts, attempt["attempt_id"]) == before


def test_cancel_and_replay_preserve_ledger_conservation() -> None:
    ensure_database()
    facts = create_running_step()
    prepare_attempt(facts, reserve=20)
    first = decoded(
        worker(
            "SELECT cancel_agent_run(%s,%s,'credit_cancel');",
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    before_replay = ledger(facts)
    replay = decoded(
        worker(
            "SELECT cancel_agent_run(%s,%s,'credit_cancel');",
            (facts["run_id"], facts["run_version"] + 1),
        )[-1][0]
    )
    assert first["outcome"] == "cancelled"
    assert replay["outcome"] == "already_cancelled"
    assert ledger(facts) == before_replay
    assert before_replay == (100, 0, 100, 2, "refunded", "released", 20, 0, 0)


def test_unknown_does_not_settle_and_late_replay_does_not_double_charge() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts, reserve=20)
    decoded(
        worker(
            "SELECT cancel_agent_run(%s,%s,'cancel-before-receipt');",
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    values = (
        attempt["attempt_id"],
        "provider-request",
        json.dumps({"receipt": "late"}),
        "c" * 64,
        json.dumps({"input_tokens": 3}),
        "completed",
        json.dumps({"readback": "completed"}),
        7,
    )
    first = decoded(
        worker("SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);", values)[-1][0]
    )
    before_replay = ledger(facts)
    replay = decoded(
        worker("SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);", values)[-1][0]
    )
    state = ledger(facts)
    assert first["settlement_outcome"] == "adjusted"
    assert replay["settlement_outcome"] == "already_adjusted"
    assert state == before_replay
    assert state == (93, -7, 93, 3, "refunded", "adjusted", 20, 0, 7)


def test_pending_late_adjustment_can_be_replayed_safely() -> None:
    ensure_database()
    facts = create_running_step(credits=99)
    attempt = prepare_attempt(facts, reserve=20)
    decoded(
        worker(
            "SELECT cancel_agent_run(%s,%s,'pending-adjustment');",
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    values = (
        attempt["attempt_id"],
        "provider-pending",
        json.dumps({"receipt": "late"}),
        "e" * 64,
        json.dumps({"input_tokens": 30}),
        "completed",
        json.dumps({"readback": "completed"}),
        100,
    )
    pending = decoded(
        worker("SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);", values)[-1][0]
    )
    assert pending["outcome"] == "adjustment_pending"
    assert ledger(facts) == (
        99, 0, 99, 2, "refunded", "adjustment_pending", 20, 0, 100,
    )
    before_conflicts = system_state_snapshot(facts, attempt["attempt_id"])
    different_amount = values[:-1] + (7,)
    amount_conflict = decoded(
        worker(
            "SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);",
            different_amount,
        )[-1][0]
    )
    different_hash = values[:3] + ("f" * 64,) + values[4:]
    hash_conflict = decoded(
        worker(
            "SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);",
            different_hash,
        )[-1][0]
    )
    assert amount_conflict["outcome"] == "receipt_conflict"
    assert hash_conflict["outcome"] == "receipt_conflict"
    assert system_state_snapshot(facts, attempt["attempt_id"]) == before_conflicts
    execute(
        """
        WITH changed AS (
            UPDATE users SET credits=credits+1,updated_at=now()
             WHERE id=%s RETURNING credits
        )
        INSERT INTO credits_history(
            user_id,change_type,change_amount,balance_after,description,org_id
        )
        SELECT %s,'admin_adjust',1,credits,'AR-11 test top-up',NULL FROM changed
        """,
        (facts["user_id"], facts["user_id"]),
    )
    replay = decoded(
        worker("SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);", values)[-1][0]
    )
    after_adjustment = ledger(facts)
    repeated = decoded(
        worker("SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);", values)[-1][0]
    )
    assert replay["settlement_outcome"] == "adjusted"
    assert repeated["settlement_outcome"] == "already_adjusted"
    assert ledger(facts) == after_adjustment
    assert after_adjustment == (
        0, -99, 0, 4, "refunded", "adjusted", 20, 0, 100,
    )


@pytest.mark.parametrize(
    (
        "provider_id", "response_hash", "receipt", "usage",
        "outcome", "evidence", "actual",
    ),
    (
        ("provider-changed", "c" * 64, '{"receipt":"late"}',
         '{"input_tokens":3}', "completed", '{"readback":"completed"}', 7),
        ("provider-replay", "d" * 64, '{"receipt":"late"}',
         '{"input_tokens":3}', "completed", '{"readback":"completed"}', 7),
        ("provider-replay", "c" * 64, '{"receipt":"changed"}', '{"input_tokens":3}',
         "completed", '{"readback":"completed"}', 7),
        ("provider-replay", "c" * 64, '{"receipt":"late"}', '{"input_tokens":4}',
         "completed", '{"readback":"completed"}', 7),
        ("provider-replay", "c" * 64, '{"receipt":"late"}', '{"input_tokens":3}',
         "failed", '{"readback":"completed"}', 7),
        ("provider-replay", "c" * 64, '{"receipt":"late"}', '{"input_tokens":3}',
         "completed", '{"readback":"different"}', 7),
        ("provider-replay", "c" * 64, '{"receipt":"late"}', '{"input_tokens":3}',
         "completed", '{"readback":"completed"}', 8),
    ),
)
def test_late_receipt_conflicting_replay_has_zero_mutation(
    provider_id: str, response_hash: str, receipt: str, usage: str,
    outcome: str, evidence: str, actual: int,
) -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts, reserve=20)
    decoded(
        worker(
            "SELECT cancel_agent_run(%s,%s,'late-identity');",
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    original = (
        attempt["attempt_id"], "provider-replay", '{"receipt":"late"}',
        "c" * 64, '{"input_tokens":3}', "completed",
        '{"readback":"completed"}', 7,
    )
    decoded(
        worker(
            "SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);",
            original,
        )[-1][0]
    )
    before = system_state_snapshot(facts, attempt["attempt_id"])
    replay = (
        attempt["attempt_id"], provider_id, receipt, response_hash, usage,
        outcome, evidence, actual,
    )
    conflict = decoded(
        worker(
            "SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);",
            replay,
        )[-1][0]
    )
    assert conflict["outcome"] == "receipt_conflict"
    assert system_state_snapshot(facts, attempt["attempt_id"]) == before
