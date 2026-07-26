"""Migration 210 real PostgreSQL role, concurrency, and rollback contract."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/210_worker_orphan_task_recovery_capability.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback"
    / "210_worker_orphan_task_recovery_capability_rollback.sql"
)
TABLES = (
    "messages",
    "tasks",
    "conversations",
    "credit_transactions",
    "credits_history",
    "users",
)


def _configured_urls() -> tuple[str, str, str]:
    if os.getenv("RUN_ORPHAN_RECOVERY_DB_TEST") != "1":
        pytest.skip("RUN_ORPHAN_RECOVERY_DB_TEST=1_REQUIRED")
    admin_url = os.getenv("ORPHAN_RECOVERY_TEST_DATABASE_URL")
    expected_name = os.getenv("ORPHAN_RECOVERY_TEST_DATABASE_NAME")
    if not admin_url or not expected_name:
        pytest.skip("ORPHAN_RECOVERY_TEST_DATABASE_CONFIG_REQUIRED")
    config = conninfo_to_dict(admin_url)
    if config.get("dbname") != expected_name or "ar02" not in expected_name:
        pytest.skip("DEDICATED_AR02_DATABASE_NAME_REQUIRED")
    password = f"ar02-{uuid4().hex}-{uuid4().hex}"
    worker_url = make_conninfo(
        **{
            **config,
            "user": "everydayai_worker",
            "password": password,
        }
    )
    return admin_url, worker_url, password


def _bootstrap(admin_url: str, worker_password: str) -> None:
    schema = """
    DROP SCHEMA public CASCADE;
    CREATE SCHEMA public;
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
    DO $$ BEGIN
      CREATE ROLE everydayai_owner NOLOGIN;
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    DO $$ BEGIN
      CREATE ROLE everydayai_worker LOGIN;
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    DO $$ BEGIN
      CREATE ROLE everydayai_runtime NOLOGIN;
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    DO $$ BEGIN
      CREATE ROLE everydayai_wecom_runtime NOLOGIN;
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    DO $$ BEGIN
      CREATE ROLE everydayai NOLOGIN;
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    GRANT ALL ON SCHEMA public TO everydayai_owner;
    GRANT USAGE ON SCHEMA public TO everydayai_worker;
    SET ROLE everydayai_owner;
    CREATE TABLE users(
      id UUID PRIMARY KEY, credits INTEGER NOT NULL, updated_at TIMESTAMPTZ
    );
    CREATE TABLE conversations(
      id UUID PRIMARY KEY, user_id UUID NOT NULL, org_id UUID
    );
    CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');
    CREATE TABLE messages(
      id UUID PRIMARY KEY, conversation_id UUID NOT NULL, org_id UUID,
      role message_role NOT NULL, content TEXT NOT NULL, status TEXT,
      credits_cost INTEGER, is_error BOOLEAN, generation_params JSONB
    );
    CREATE TABLE credit_transactions(
      id UUID PRIMARY KEY, user_id UUID NOT NULL, org_id UUID,
      amount INTEGER NOT NULL, status TEXT NOT NULL,
      confirmed_at TIMESTAMPTZ
    );
    CREATE TABLE credits_history(
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NOT NULL,
      change_amount INTEGER NOT NULL, balance_after INTEGER NOT NULL,
      change_type TEXT NOT NULL, description TEXT, org_id UUID
    );
    CREATE TABLE tasks(
      id UUID PRIMARY KEY, user_id UUID NOT NULL, org_id UUID,
      conversation_id UUID, type TEXT NOT NULL, status TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), started_at TIMESTAMPTZ,
      completed_at TIMESTAMPTZ, external_task_id TEXT,
      placeholder_message_id TEXT, model_id TEXT, client_task_id TEXT,
      accumulated_content TEXT, accumulated_blocks JSONB,
      credit_transaction_id UUID, delivery_context JSONB NOT NULL DEFAULT '{}',
      execution_token UUID, lease_expires_at TIMESTAMPTZ,
      execution_attempt INTEGER NOT NULL DEFAULT 0,
      terminal_reason TEXT, error_message TEXT
    );
    CREATE FUNCTION atomic_refund_credits(p_transaction_id UUID)
    RETURNS JSONB LANGUAGE plpgsql AS $$
    DECLARE v_user UUID; v_amount INTEGER;
    BEGIN
      UPDATE credit_transactions SET status = 'refunded', confirmed_at = NOW()
       WHERE id = p_transaction_id AND status = 'pending'
       RETURNING user_id, amount INTO v_user, v_amount;
      IF v_user IS NULL THEN
        RETURN jsonb_build_object('refunded', false);
      END IF;
      UPDATE users SET credits = credits + v_amount WHERE id = v_user;
      INSERT INTO credits_history(
        user_id, change_amount, balance_after, change_type
      ) SELECT v_user, v_amount, credits, 'refund' FROM users WHERE id = v_user;
      RETURN jsonb_build_object('refunded', true);
    END; $$;
    RESET ROLE;
    """
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(schema)
        admin.execute(
            sql.SQL(
                "ALTER ROLE everydayai_worker LOGIN PASSWORD {} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            ).format(sql.Literal(worker_password))
        )
        with admin.transaction():
            admin.execute(MIGRATION.read_text(encoding="utf-8"))


def _scope(connection: psycopg.Connection) -> None:
    connection.execute(
        "SELECT set_config('app.actor_user_id', '', true), "
        "set_config('app.org_id', '', true), "
        "set_config('app.access_kind', 'worker', true), "
        "set_config('app.request_id', 'ar02-external', true)"
    )


def _claim(worker_url: str) -> list[dict]:
    with psycopg.connect(worker_url) as worker:
        with worker.transaction():
            _scope(worker)
            return worker.execute(
                "SELECT worker_claim_orphan_tasks(10, 60)"
            ).fetchone()[0]


def _seed_initial_contract(admin_url: str) -> tuple:
    user_id, conversation_id = uuid4(), uuid4()
    task_id, message_id, transaction_id = uuid4(), uuid4(), uuid4()
    actor_task_id, terminal_task_id = uuid4(), uuid4()
    with psycopg.connect(admin_url) as admin:
        with admin.transaction():
            admin.execute("SET LOCAL ROLE everydayai_owner")
            admin.execute(
                "INSERT INTO users VALUES (%s, 100, NOW())",
                (user_id,),
            )
            admin.execute(
                "INSERT INTO conversations VALUES (%s, %s, NULL)",
                (conversation_id, user_id),
            )
            admin.execute(
                "INSERT INTO credit_transactions VALUES "
                "(%s, %s, NULL, 9, 'pending', NULL)",
                (transaction_id, user_id),
            )
            for current_id, status, delivery in (
                (task_id, "running", {}),
                (actor_task_id, "running", {"actor": True}),
                (terminal_task_id, "completed", {}),
            ):
                admin.execute(
                    "INSERT INTO tasks("
                    "id,user_id,conversation_id,type,status,"
                    "placeholder_message_id,accumulated_content,"
                    "credit_transaction_id,delivery_context"
                    ") VALUES (%s,%s,%s,'chat',%s,%s,'partial',%s,%s)",
                    (
                        current_id,
                        user_id,
                        conversation_id,
                        status,
                        message_id if current_id == task_id else uuid4(),
                        transaction_id if current_id == task_id else None,
                        Jsonb(delivery),
                    ),
                )
    return user_id, conversation_id, task_id, message_id


def _verify_concurrency_fencing_and_refund(
    admin_url: str,
    worker_url: str,
    facts: tuple,
) -> None:
    user_id, _, task_id, message_id = facts
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: _claim(worker_url), range(2)))
    claimed = [task for batch in claims for task in batch]
    assert [task["id"] for task in claimed] == [str(task_id)]
    first_token = claimed[0]["execution_token"]

    with psycopg.connect(admin_url) as admin:
        with admin.transaction():
            admin.execute("SET LOCAL ROLE everydayai_owner")
            admin.execute(
                "UPDATE tasks SET lease_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = %s",
                (task_id,),
            )
    second_claim = _claim(worker_url)
    second_token = second_claim[0]["execution_token"]
    assert second_token != first_token

    with psycopg.connect(worker_url) as worker:
        with worker.transaction():
            _scope(worker)
            stale = worker.execute(
                "SELECT worker_complete_orphan_task(%s,%s,%s)",
                (task_id, first_token, Jsonb([{"type": "text", "text": "x"}])),
            ).fetchone()[0]
            assert stale["outcome"] == "ownership_lost"
            failed = worker.execute(
                "SELECT worker_fail_orphan_task(%s,%s,%s)",
                (task_id, second_token, "restart"),
            ).fetchone()[0]
            assert failed["outcome"] == "failed"
            repeated = worker.execute(
                "SELECT worker_fail_orphan_task(%s,%s,%s)",
                (task_id, second_token, "restart"),
            ).fetchone()[0]
            assert repeated["outcome"] == "already_failed"
        for table in TABLES:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute(f"SELECT * FROM {table} LIMIT 1")
            worker.rollback()

    with psycopg.connect(admin_url) as admin:
        task, credits, refund_count = admin.execute(
            "SELECT status, terminal_reason FROM tasks WHERE id=%s",
            (task_id,),
        ).fetchone(), admin.execute(
            "SELECT credits FROM users WHERE id=%s", (user_id,)
        ).fetchone()[0], admin.execute(
            "SELECT COUNT(*) FROM credits_history WHERE user_id=%s",
            (user_id,),
        ).fetchone()[0]
        assert task == ("failed", "startup_recovery_failed")
        assert (credits, refund_count) == (109, 1)
        assert admin.execute(
            "SELECT COUNT(*) FROM messages WHERE id=%s", (message_id,)
        ).fetchone()[0] == 0


def _verify_message_task_atomic_completion(
    admin_url: str,
    worker_url: str,
    facts: tuple,
) -> None:
    user_id, conversation_id, _, _ = facts
    with psycopg.connect(admin_url) as admin:
        complete_task_id, complete_message_id = uuid4(), uuid4()
        admin.execute(
            "INSERT INTO tasks("
            "id,user_id,conversation_id,type,status,"
            "placeholder_message_id,accumulated_content"
            ") VALUES (%s,%s,%s,'chat','running',%s,'visible')",
            (
                complete_task_id,
                user_id,
                conversation_id,
                complete_message_id,
            ),
        )
        admin.commit()
    complete_claim = _claim(worker_url)[0]
    with psycopg.connect(worker_url) as worker:
        with worker.transaction():
            _scope(worker)
            complete = worker.execute(
                "SELECT worker_complete_orphan_task(%s,%s,%s)",
                (
                    complete_task_id,
                    complete_claim["execution_token"],
                    Jsonb([{"type": "text", "text": "visible"}]),
                ),
            ).fetchone()[0]
            assert complete["outcome"] == "completed"
            repeated = worker.execute(
                "SELECT worker_complete_orphan_task(%s,%s,%s)",
                (
                    complete_task_id,
                    complete_claim["execution_token"],
                    Jsonb([{"type": "text", "text": "visible"}]),
                ),
            ).fetchone()[0]
            assert repeated["outcome"] == "already_completed"
    with psycopg.connect(admin_url) as admin:
        task_status = admin.execute(
            "SELECT status FROM tasks WHERE id=%s", (complete_task_id,)
        ).fetchone()[0]
        message = admin.execute(
            "SELECT status, content FROM messages WHERE id=%s",
            (complete_message_id,),
        ).fetchone()
        assert task_status == "completed"
        assert message[0] == "interrupted"
        assert json.loads(message[1]) == [
            {"type": "text", "text": "visible"}
        ]


def _verify_database_error_and_rollback(
    admin_url: str,
    worker_url: str,
    facts: tuple,
) -> None:
    user_id, conversation_id, _, _ = facts
    with psycopg.connect(admin_url) as admin:
        rollback_task_id, rollback_message_id = uuid4(), uuid4()
        admin.execute(
            "INSERT INTO tasks("
            "id,user_id,conversation_id,type,status,"
            "placeholder_message_id,accumulated_content"
            ") VALUES (%s,%s,%s,'chat','running',%s,'rollback')",
            (
                rollback_task_id,
                user_id,
                conversation_id,
                rollback_message_id,
            ),
        )
        admin.commit()
    rollback_claim = _claim(worker_url)[0]
    with psycopg.connect(admin_url) as admin:
        admin.execute(
            "CREATE FUNCTION reject_recovery_terminal() RETURNS TRIGGER "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'forced'; END $$"
        )
        admin.execute(
            "CREATE TRIGGER reject_recovery_terminal "
            "BEFORE UPDATE ON tasks FOR EACH ROW "
            "WHEN (NEW.status = 'completed') "
            "EXECUTE FUNCTION reject_recovery_terminal()"
        )
        admin.commit()
    with psycopg.connect(worker_url) as worker:
        with pytest.raises(psycopg.errors.RaiseException):
            with worker.transaction():
                _scope(worker)
                worker.execute(
                    "SELECT worker_complete_orphan_task(%s,%s,%s)",
                    (
                        rollback_task_id,
                        rollback_claim["execution_token"],
                        Jsonb([{"type": "text", "text": "rollback"}]),
                    ),
                )
    with psycopg.connect(admin_url) as admin:
        assert admin.execute(
            "SELECT COUNT(*) FROM messages WHERE id=%s",
            (rollback_message_id,),
        ).fetchone()[0] == 0
        assert admin.execute(
            "SELECT status FROM tasks WHERE id=%s", (rollback_task_id,)
        ).fetchone()[0] == "running"
        admin.execute("DROP TRIGGER reject_recovery_terminal ON tasks")
        admin.execute("DROP FUNCTION reject_recovery_terminal()")
        admin.execute(
            "UPDATE tasks SET lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE id=%s",
            (rollback_task_id,),
        )
        admin.commit()
    retry_claim = _claim(worker_url)[0]
    with psycopg.connect(worker_url) as worker:
        with worker.transaction():
            _scope(worker)
            retry = worker.execute(
                "SELECT worker_complete_orphan_task(%s,%s,%s)",
                (
                    rollback_task_id,
                    retry_claim["execution_token"],
                    Jsonb([{"type": "text", "text": "retry"}]),
                ),
            ).fetchone()[0]
            assert retry["outcome"] == "completed"
    with psycopg.connect(admin_url) as admin:
        signature = "worker_claim_orphan_tasks(integer,integer)"
        assert admin.execute(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE oid = %s::regprocedure",
            (signature,),
        ).fetchone()[0] == "everydayai_owner"
        for role, expected in (
            ("everydayai_worker", True),
            ("everydayai_runtime", False),
            ("public", False),
        ):
            assert admin.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                (role, signature),
            ).fetchone()[0] is expected
        admin.execute(ROLLBACK.read_text(encoding="utf-8"))
        for dropped_signature in (
            "worker_claim_orphan_tasks(integer,integer)",
            "worker_complete_orphan_task(uuid,uuid,jsonb)",
            "worker_fail_orphan_task(uuid,uuid,text)",
        ):
            assert admin.execute(
                "SELECT to_regprocedure(%s)", (dropped_signature,)
            ).fetchone()[0] is None
        admin.execute(MIGRATION.read_text(encoding="utf-8"))
        assert admin.execute(
            "SELECT to_regprocedure(%s)", (signature,)
        ).fetchone()[0] is not None
        admin.execute(ROLLBACK.read_text(encoding="utf-8"))
    with psycopg.connect(worker_url) as worker:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SELECT * FROM tasks LIMIT 1")


def test_real_role_concurrency_atomicity_fencing_and_rollback() -> None:
    admin_url, worker_url, worker_password = _configured_urls()
    _bootstrap(admin_url, worker_password)
    facts = _seed_initial_contract(admin_url)
    _verify_concurrency_fencing_and_refund(admin_url, worker_url, facts)
    _verify_message_task_atomic_completion(admin_url, worker_url, facts)
    _verify_database_error_and_rollback(admin_url, worker_url, facts)
