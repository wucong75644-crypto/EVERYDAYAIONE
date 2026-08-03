from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar173_postgres_external import _seed_specialist_action
from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_04_agent_runtime_provider_submission_facts.sql"
ROLLBACK = "227_04_agent_runtime_provider_submission_facts_rollback.sql"


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _worker_call(url: str, function: str, params: tuple[object, ...]) -> object:
    worker_url = url.replace("postgres@", "everydayai_agent_runtime_worker@")
    with psycopg.connect(worker_url) as conn:
        conn.execute("SELECT set_config('app.request_id',%s,false)", (str(uuid4()),))
        conn.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", ("44444444-4444-4444-4444-444444444444",))
        conn.execute("SELECT set_config('app.org_id',%s,false)", ("22222222-2222-2222-2222-222222222222",))
        values = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
        return conn.execute(
            f"SELECT {function}({','.join(['%s'] * len(values))})", values,
        ).fetchone()[0]


def _create(url: str, ids: dict[str, str], key: str = "provider-key") -> object:
    return _worker_call(url, "create_agent_runtime_provider_submission", (
        ids["attempt"], ids["action"], ids["run"],
        "22222222-2222-2222-2222-222222222222",
        "44444444-4444-4444-4444-444444444444", "user",
        "44444444-4444-4444-4444-444444444444", ids["token"], ids["request_hash"],
        "mock-provider", "mock-v1", key,
    ))


def test_a2_apply_rls_acl_and_rollback_guard(database: str) -> None:
    _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid='agent_runtime_provider_submission_facts'::regclass"
        ).fetchone() == (True, True)
        assert conn.execute(
            "SELECT has_table_privilege('everydayai_worker',"
            "'agent_runtime_provider_submission_facts','SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'create_agent_runtime_provider_submission(uuid,uuid,uuid,uuid,uuid,text,text,uuid,text,text,text,text)','EXECUTE')"
        ).fetchone()[0] is True
    ids = _seed_specialist_action(database)
    created = _create(database, ids)
    assert created["outcome"] == "created"
    with pytest.raises(psycopg.Error, match="AR174_A2_ROLLBACK_GUARD_FACTS_EXIST"):
        with psycopg.connect(database) as conn:
            conn.execute((ROOT / "migrations/rollback" / ROLLBACK).read_text())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_runtime_provider_submission_facts")
        conn.commit()
    with psycopg.connect(database) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / ROLLBACK).read_text())
    _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regclass('agent_runtime_provider_submission_facts')"
        ).fetchone()[0] == "agent_runtime_provider_submission_facts"
    with psycopg.connect(database) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / ROLLBACK).read_text())


def test_a2_idempotency_fencing_unknown_reconcile_and_concurrent_cas(database: str) -> None:
    _apply(database, MIGRATION)
    ids = _seed_specialist_action(database)
    created = _create(database, ids)
    duplicate = _create(database, ids)
    assert duplicate["outcome"] == "already_applied"
    submission_id = str(created["submission_id"])

    submitted = _worker_call(database, "record_agent_runtime_provider_submitted", (
        submission_id, ids["token"], ids["request_hash"], 0, "task-a", None, "a" * 64,
    ))
    assert submitted["state"] == "submitted"
    assert submitted["state_version"] == 1

    def mark_unknown() -> object:
        return _worker_call(database, "record_agent_runtime_provider_unknown", (
            submission_id, ids["token"], ids["request_hash"], 1,
            {"transport": "connection_lost"},
        ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: mark_unknown(), range(2)))
    outcomes = sorted(result["outcome"] for result in results)
    assert outcomes == ["fenced", "unknown"]
    unknown = next(result for result in results if result["outcome"] == "unknown")

    reconciled = _worker_call(database, "reconcile_agent_runtime_provider_submission", (
        submission_id, ids["token"], ids["request_hash"], unknown["state_version"],
        "readback_confirmed", "b" * 64, {},
    ))
    assert reconciled["state"] == "readback_confirmed"

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_runtime_provider_submission_facts")
        conn.commit()
    with psycopg.connect(database) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / ROLLBACK).read_text())
