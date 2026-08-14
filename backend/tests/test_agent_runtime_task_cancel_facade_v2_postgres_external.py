"""Disposable PostgreSQL contract for the callable task cancel v2 facade."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    ORG,
    _connect,
    database as ar17_database,
)
from tests.test_agent_runtime_task_cancel_intent_postgres_external import (
    MIGRATIONS as B1_MIGRATIONS,
    USER,
    Case,
    _facade,
    _hash,
    _scope,
    _seed,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_23_agent_runtime_task_cancel_facade_callable.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/227_23_agent_runtime_task_cancel_facade_callable_rollback.sql"
)
V1 = "request_agent_runtime_task_cancel_v1(uuid,uuid,uuid,uuid,uuid,uuid,text,text)"
V2 = "request_agent_runtime_task_cancel_v2(uuid,uuid,uuid,uuid,uuid,uuid,text)"
HASH = "_agent_runtime_task_cancel_request_hash(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)"


def _execute_file(url: str, path: Path) -> None:
    with psycopg.connect(url) as conn:
        conn.execute(path.read_text(encoding="utf-8"))
        conn.commit()


@pytest.fixture
def database(ar17_database: str) -> str:
    with psycopg.connect(ar17_database) as conn:
        conn.execute("""
        DO $$ BEGIN
          IF to_regrole('everydayai_agent_model_gateway') IS NULL THEN
            CREATE ROLE everydayai_agent_model_gateway LOGIN;
          END IF;
        END $$;
        """)
        conn.commit()
    for path in B1_MIGRATIONS:
        _execute_file(ar17_database, path)
    _execute_file(ar17_database, MIGRATION)
    return ar17_database


def _v2(
    url: str, case: Case, *, role: str = "everydayai_runtime",
    overrides: dict | None = None,
) -> dict:
    values = {
        "task": case.task_id, "message": case.message_id, "org": ORG,
        "user": case.requested_by_user_id, "session": case.session_id,
        "command": case.command_id, "key": case.idempotency_key,
    }
    values.update(overrides or {})
    with _connect(url, role) as conn:
        _scope(conn, role, user=case.requested_by_user_id)
        return conn.execute(
            "SELECT request_agent_runtime_task_cancel_v2("
            "%s,%s,%s,%s,%s,%s,%s)", tuple(values.values()),
        ).fetchone()[0]


def _assert_v1_and_hash_private(url: str, case: Case, role: str) -> None:
    request_hash = _hash(url, case)
    with _connect(url, role) as conn:
        _scope(conn, role, user=case.requested_by_user_id)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "SELECT request_agent_runtime_task_cancel_v1("
                "%s,%s,%s,%s,%s,%s,%s,%s)",
                (case.task_id, case.message_id, ORG, case.requested_by_user_id,
                 case.session_id, case.command_id, case.idempotency_key,
                 request_hash),
            )
    with _connect(url, role) as conn:
        _scope(conn, role, user=case.requested_by_user_id)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "SELECT _agent_runtime_task_cancel_request_hash("
                "%s,%s,%s,%s,%s,%s,%s,%s)",
                (case.task_id, case.message_id, ORG, case.scope_user_id,
                 case.requested_by_user_id, case.session_id, case.command_id,
                 case.idempotency_key),
            )


def _assert_acl(url: str, *, v1: bool, v2: bool | None) -> None:
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_runtime',to_regprocedure(%s),'execute'),"
            "has_function_privilege('everydayai_wecom_runtime',to_regprocedure(%s),'execute'),"
            "has_function_privilege('everydayai_runtime',to_regprocedure(%s),'execute'),"
            "has_function_privilege('everydayai_wecom_runtime',to_regprocedure(%s),'execute'),"
            "has_function_privilege('everydayai_runtime',to_regprocedure(%s),'execute'),"
            "has_function_privilege('everydayai_wecom_runtime',to_regprocedure(%s),'execute')",
            (V1, V1, V2, V2, HASH, HASH),
        ).fetchone() == (v1, v1, v2, v2, False, False)


def test_callable_v2_full_contract(database: str) -> None:
    _assert_acl(database, v1=False, v2=True)
    user_case = _seed(database)
    _assert_v1_and_hash_private(database, user_case, "everydayai_runtime")
    first = _v2(database, user_case)
    assert set(first) == {"outcome", "intent_id", "run_id"}
    assert first["outcome"] == "cancelled_before_claim"
    assert _v2(database, user_case) == first
    assert _v2(
        database, user_case,
        overrides={"key": user_case.idempotency_key + ":conflict"},
    ) == {"outcome": "idempotency_conflict"}

    binding = _seed(database)
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="TASK_CANCEL_BINDING_MISMATCH",
    ):
        _v2(database, binding, overrides={"task": uuid4()})

    channel = _seed(database, scope_kind="channel")
    _assert_v1_and_hash_private(database, channel, "everydayai_wecom_runtime")
    assert _v2(
        database, channel, role="everydayai_wecom_runtime",
    )["outcome"] == "cancelled_before_claim"

    with psycopg.connect(database) as conn:
        facts_before = conn.execute(
            "SELECT count(*) FROM agent_runtime_task_cancel_intents"
        ).fetchone()[0]
    _execute_file(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regprocedure(%s)", (V2,),
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_task_cancel_intents"
        ).fetchone()[0] == facts_before
    _assert_acl(database, v1=True, v2=None)
    restored = _seed(database)
    assert _facade(database, restored)["outcome"] == "cancelled_before_claim"

    _execute_file(database, MIGRATION)
    _assert_acl(database, v1=False, v2=True)
    reapplied = _seed(database)
    assert _v2(database, reapplied)["outcome"] == "cancelled_before_claim"
