"""Real PostgreSQL lifecycle, fencing, cancellation, and fault contracts for 217."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("AR11_TEST_DATABASE_URL", "")
MIGRATIONS = tuple(sorted((ROOT / "migrations").glob("217_*.sql")))
ROLLBACKS = tuple(
    ROOT / "migrations/rollback" / f"{path.stem}_rollback.sql"
    for path in reversed(MIGRATIONS)
)
BASE_MIGRATIONS = tuple(
    ROOT / "migrations" / name
    for name in (
        "212_agent_runtime_core_foundation.sql",
        "213_agent_runtime_session_run_rpcs.sql",
        "214_agent_runtime_run_lifecycle_rpcs.sql",
        "215_agent_runtime_model_event_projection_rpcs.sql",
    )
)
BOOTSTRAP = (
    ROOT / "tests/fixtures/agent_runtime_core_postgres_bootstrap.sql"
).read_text(encoding="utf-8")
CREDITS_BOOTSTRAP = """
SET ROLE everydayai_owner;
ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE TYPE credits_change_type AS ENUM (
 'register_gift','admin_adjust','conversation_cost','image_generation_cost',
 'daily_checkin','purchase','partial_refund','refund'
);
CREATE TABLE credit_transactions(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), task_id UUID NOT NULL,
 user_id UUID NOT NULL REFERENCES users(id), amount INTEGER NOT NULL CHECK(amount > 0),
 type VARCHAR(20) NOT NULL CHECK(type IN ('lock','deduct','refund')),
 status VARCHAR(20) NOT NULL DEFAULT 'pending'
  CHECK(status IN ('pending','confirmed','refunded','expired')),
 reason VARCHAR(100), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 confirmed_at TIMESTAMPTZ, expires_at TIMESTAMPTZ DEFAULT now() + interval '10 min',
 org_id UUID REFERENCES organizations(id)
);
CREATE TABLE credits_history(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NOT NULL REFERENCES users(id),
 change_amount INTEGER NOT NULL, balance_after INTEGER NOT NULL,
 change_type credits_change_type NOT NULL, related_id UUID, description VARCHAR(500),
 operator_id UUID, created_at TIMESTAMPTZ DEFAULT now(),
 org_id UUID REFERENCES organizations(id)
);
CREATE FUNCTION atomic_refund_credits(
 p_transaction_id UUID, p_final_status TEXT DEFAULT 'refunded'
) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE v_user UUID; v_amount INTEGER; v_org UUID;
BEGIN
 UPDATE credit_transactions SET status=p_final_status, confirmed_at=now()
  WHERE id=p_transaction_id AND status='pending'
  RETURNING user_id,amount,org_id INTO v_user,v_amount,v_org;
 IF v_user IS NULL THEN RETURN jsonb_build_object('refunded',false); END IF;
 UPDATE users SET credits=credits+v_amount,updated_at=now() WHERE id=v_user;
 INSERT INTO credits_history(
  user_id,change_amount,balance_after,change_type,description,org_id
 ) SELECT v_user,v_amount,credits,'refund','AR-11 test refund',v_org
    FROM users WHERE id=v_user;
 RETURN jsonb_build_object('refunded',true);
END $$;
RESET ROLE;
"""
_READY = False


def decoded(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else json.loads(str(value))


def execute(sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return cursor.fetchall() if cursor.description else []


def execute_file(path: Path) -> None:
    execute(path.read_text(encoding="utf-8"))


def ensure_database() -> None:
    global _READY
    if _READY:
        return
    if os.getenv("RUN_AR11_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR11_DB_TEST=1 and AR11_TEST_DATABASE_URL are required")
    if "ar11" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR11 database name required")
    execute(BOOTSTRAP)
    execute(CREDITS_BOOTSTRAP)
    for migration in (*BASE_MIGRATIONS, *MIGRATIONS):
        execute_file(migration)
    _READY = True


def worker(sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION everydayai_worker")
            cursor.execute("SELECT set_config('app.actor_user_id', '', false)")
            cursor.execute("SELECT set_config('app.org_id', '', false)")
            cursor.execute("SELECT set_config('app.access_kind', 'worker', false)")
            cursor.execute("SELECT set_config('app.request_id', 'ar11-worker', false)")
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.description else []


def runtime(
    sql: str, user_id: object, params: tuple[object, ...] = ()
) -> list[tuple[object, ...]]:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION everydayai_runtime")
            cursor.execute(
                "SELECT set_config('app.actor_user_id', %s, false)",
                (str(user_id),),
            )
            cursor.execute("SELECT set_config('app.org_id', '', false)")
            cursor.execute("SELECT set_config('app.access_kind', 'runtime', false)")
            cursor.execute("SELECT set_config('app.request_id', 'ar11-runtime', false)")
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.description else []


def create_running_step(*, credits: int = 100) -> dict[str, object]:
    suffix = uuid4().hex
    user_id, conversation_id = uuid4(), uuid4()
    execute("INSERT INTO users(id, credits) VALUES (%s, %s)", (user_id, credits))
    execute(
        """
        INSERT INTO conversations(id,user_id,scope_type,scope_id)
        VALUES (%s,%s,'user',%s)
        """,
        (conversation_id, user_id, str(user_id)),
    )
    session = decoded(
        runtime(
            """
            SELECT ensure_agent_runtime_session(
                %s,NULL,%s,'user',%s,%s,'default','v1'
            );
            """,
            user_id,
            (conversation_id, user_id, str(user_id), user_id),
        )[-1][0]
    )
    command = decoded(
        runtime(
            "SELECT submit_session_command(%s,'submit_input',%s,'{}');",
            user_id,
            (session["entity_id"], f"command-{suffix}"),
        )[-1][0]
    )
    run = decoded(
        worker(
            "SELECT create_agent_run(%s,%s,%s,'user','{}','{}','{}');",
            (session["entity_id"], command["entity_id"], f"run-{suffix}"),
        )[-1][0]
    )
    claim = decoded(
        worker(
            "SELECT claim_agent_run(%s,'worker-a',120,3);",
            (run["entity_id"],),
        )[-1][0]
    )
    step = decoded(
        worker(
            """
            SELECT create_model_step(
                %s,%s,'model','provider','revision','prompt','tools','{}'
            );
            """,
            (run["entity_id"], claim["execution_token"]),
        )[-1][0]
    )
    return {
        "user_id": str(user_id),
        "run_id": run["entity_id"],
        "run_token": claim["execution_token"],
        "run_version": claim["state_version"],
        "step_id": step["entity_id"],
        "step_version": step["state_version"],
    }


def prepare_attempt(
    facts: dict[str, object], *, reserve: int = 20,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    result = worker(
        """
        SELECT prepare_model_attempt(
            %s,%s,%s,'worker-a',%s,%s,'provider','{}',%s,120
        );
        """,
        (
            facts["step_id"],
            facts["run_token"],
            facts["step_version"],
            "a" * 64,
            idempotency_key or f"attempt-{uuid4().hex}",
            reserve,
        ),
    )
    return decoded(result[-1][0])


def claim_unknown_attempt(
    facts: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    attempt = prepare_attempt(facts)
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
    unknown = decoded(
        worker(
            "SELECT record_model_attempt_unknown(%s,%s,%s,%s,%s,%s,%s);",
            (
                attempt["attempt_id"],
                facts["run_token"],
                started["state_version"],
                "a" * 64,
                "request_started",
                "reconcile_only",
                json.dumps({"reason": "worker_crash"}),
            ),
        )[-1][0]
    )
    claimed = decoded(
        worker(
            "SELECT claim_model_attempt_reconciliation(%s,%s,%s,'worker-b',120);",
            (
                attempt["attempt_id"],
                facts["run_token"],
                unknown["state_version"],
            ),
        )[-1][0]
    )
    return attempt, claimed


def model_state_snapshot(
    facts: dict[str, object], attempt_id: object,
) -> tuple[object, ...]:
    return execute(
        """
        SELECT to_jsonb(attempt),to_jsonb(step),to_jsonb(settlement),
               (SELECT count(*) FROM agent_runtime_events
                 WHERE agent_runtime_events.model_step_id=step.id),
               (SELECT count(*) FROM agent_projection_outbox outbox
                 WHERE outbox.session_id=step.session_id)
          FROM agent_model_attempts attempt
          JOIN agent_model_steps step ON step.id=attempt.model_step_id
          JOIN agent_model_credit_settlements settlement
            ON settlement.model_step_id=step.id
         WHERE attempt.id=%s AND step.id=%s
        """,
        (attempt_id, facts["step_id"]),
    )[0]


def system_state_snapshot(
    facts: dict[str, object], attempt_id: object,
) -> tuple[object, ...]:
    ledger = execute(
        """
        SELECT users.credits,
               COUNT(history.id),
               COALESCE(SUM(history.change_amount),0),
               COALESCE(
                 jsonb_agg(to_jsonb(transaction) ORDER BY transaction.id)
                   FILTER (WHERE transaction.id IS NOT NULL),
                 '[]'::jsonb
               )
          FROM users
          LEFT JOIN credits_history history ON history.user_id=users.id
          LEFT JOIN credit_transactions transaction
            ON transaction.user_id=users.id
         WHERE users.id=%s
         GROUP BY users.credits
        """,
        (facts["user_id"],),
    )[0]
    return (*model_state_snapshot(facts, attempt_id), *ledger)


def test_unknown_is_non_terminal_and_fencing_fails_closed() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts)
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
    unknown = decoded(
        worker(
            "SELECT record_model_attempt_unknown(%s,%s,%s,%s,%s,%s,%s);",
            (
                attempt["attempt_id"],
                facts["run_token"],
                started["state_version"],
                "a" * 64,
                "request_started",
                "reconcile_only",
                json.dumps({"reason": "timeout"}),
            ),
        )[-1][0]
    )
    assert unknown["outcome"] == "unknown"
    status = execute(
        "SELECT status FROM agent_model_steps WHERE id=%s", (facts["step_id"],)
    )[0][0]
    assert status == "running"
    lost = decoded(
        worker(
            "SELECT start_model_attempt_dispatch(%s,%s,%s,%s);",
            (attempt["attempt_id"], uuid4(), unknown["state_version"], "a" * 64),
        )[-1][0]
    )
    assert lost["outcome"] == "ownership_lost"


def test_tool_handoff_has_zero_terminal_mutation() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts)
    before = execute(
        "SELECT status,state_version FROM agent_model_attempts WHERE id=%s",
        (attempt["attempt_id"],),
    )[0]
    handoff = decoded(
        worker(
            """
            SELECT complete_model_attempt_without_actions(
             %s,%s,%s,%s,%s,'{}',%s,'tool_calls',NULL,'{}',10
            );
            """,
            (
                attempt["attempt_id"],
                facts["run_token"],
                attempt["state_version"],
                facts["step_version"],
                "a" * 64,
                "b" * 64,
            ),
        )[-1][0]
    )
    after = execute(
        "SELECT status,state_version FROM agent_model_attempts WHERE id=%s",
        (attempt["attempt_id"],),
    )[0]
    assert handoff["outcome"] == "handoff_tool_calls"
    assert after == before


def test_cancel_wins_against_stale_terminal_commit() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts)
    cancelled = decoded(
        worker(
            "SELECT cancel_agent_run(%s,%s,'user_cancelled');",
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    late = decoded(
        worker(
            """
            SELECT complete_model_attempt_without_actions(
             %s,%s,%s,%s,%s,'{}',%s,'final',NULL,'{}',10
            );
            """,
            (
                attempt["attempt_id"],
                facts["run_token"],
                attempt["state_version"],
                facts["step_version"],
                "a" * 64,
                "b" * 64,
            ),
        )[-1][0]
    )
    assert cancelled["outcome"] == "cancelled"
    assert late["outcome"] == "run_cancelled_use_late_receipt"


def test_concurrent_cancel_and_terminal_commit_serialize_without_torn_attempt() -> None:
    ensure_database()
    facts = create_running_step()
    attempt = prepare_attempt(facts)
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

    def complete() -> dict[str, object]:
        return decoded(
            worker(
                """
                SELECT complete_model_attempt_without_actions(
                 %s,%s,%s,%s,%s,'{}',%s,'final',NULL,'{}',10
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

    def cancel() -> dict[str, object]:
        return decoded(
            worker(
                "SELECT cancel_agent_run(%s,%s,'concurrent_cancel');",
                (facts["run_id"], facts["run_version"]),
            )[-1][0]
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed, cancelled = (
            future.result()
            for future in (executor.submit(complete), executor.submit(cancel))
        )
    run_status, attempt_status = execute(
        """
        SELECT run.status,attempt.status
          FROM agent_runs run
          JOIN agent_model_attempts attempt ON attempt.run_id=run.id
         WHERE run.id=%s
        """,
        (facts["run_id"],),
    )[0]
    assert (run_status, attempt_status) in {
        ("running", "completed"),
        ("cancelled", "cancelled"),
        ("cancelled", "completed"),
    }
    assert {completed["outcome"], cancelled["outcome"]} & {
        "completed",
        "cancelled",
    }
