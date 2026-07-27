"""Real PostgreSQL unique settlement and late-adjustment contracts for 217."""

from __future__ import annotations

import json

import pytest

from tests.test_agent_runtime_model_attempt_postgres_external import (
    create_running_step,
    decoded,
    ensure_database,
    execute,
    prepare_attempt,
    worker,
)

pytestmark = pytest.mark.external


def test_reserve_settle_and_replay_are_exactly_once() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts, reserve=20)
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
    completed = decoded(
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
                started["state_version"],
                facts["step_version"],
                "a" * 64,
                "b" * 64,
            ),
        )[-1][0]
    )
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
                started["state_version"] + 1,
                facts["step_version"] + 1,
                "a" * 64,
                "b" * 64,
            ),
        )[-1][0]
    )
    balance = execute("SELECT credits FROM users WHERE id=%s", (facts["user_id"],))[0][0]
    settlement = execute(
        "SELECT status,reserved_credits,settled_credits FROM "
        "agent_model_credit_settlements WHERE model_step_id=%s",
        (facts["step_id"],),
    )[0]
    assert completed.get("settlement_outcome") == "settled", completed
    assert replay["outcome"] == "already_completed"
    assert balance == 88
    assert settlement == ("settled", 20, 12)


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
    replay = decoded(
        worker("SELECT record_late_model_receipt(%s,%s,%s,%s,%s,%s,%s,%s);", values)[-1][0]
    )
    balance = execute("SELECT credits FROM users WHERE id=%s", (facts["user_id"],))[0][0]
    assert first["settlement_outcome"] == "adjusted"
    assert replay["settlement_outcome"] == "already_adjusted"
    assert balance == 93
