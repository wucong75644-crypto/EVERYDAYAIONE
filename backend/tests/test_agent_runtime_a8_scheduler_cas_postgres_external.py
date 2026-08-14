from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _seed_specialist_action


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def _apply(database_url: str, filename: str) -> None:
    path = ROOT / "migrations" / filename
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(path.read_text())


def _worker_rpc(database_url: str, function: str, params: tuple[object, ...]) -> object:
    worker_url = database_url.replace("postgres@", "everydayai_agent_runtime_worker@")
    with psycopg.connect(worker_url) as connection:
        connection.execute("SELECT set_config('app.request_id',%s,false)", (str(uuid4()),))
        connection.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        connection.execute("SELECT set_config('app.actor_user_id',%s,false)", ("44444444-4444-4444-4444-444444444444",))
        connection.execute("SELECT set_config('app.org_id',%s,false)", ("22222222-2222-2222-2222-222222222222",))
        values = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
        return connection.execute(
            f"SELECT {function}({','.join(['%s'] * len(values))})", values,
        ).fetchone()[0]


def _mutate(database_url: str, ids: dict[str, str], operation: str, expected: int, key: str) -> object:
    return _worker_rpc(database_url, "mutate_agent_runtime_scheduler_cas", (
        ids["attempt"], ids["action"], ids["run"],
        "22222222-2222-2222-2222-222222222222",
        "44444444-4444-4444-4444-444444444444", "user",
        "44444444-4444-4444-4444-444444444444", "task-a", expected,
        operation, {}, ids["request_hash"], ids["token"], key,
    ))


def test_a8_postgres_apply_acl_concurrent_cas_and_rollback(database: str) -> None:
    _apply(database, "227_05_agent_runtime_scheduler_cas.sql")
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid='agent_runtime_scheduler_cas_facts'::regclass"
        ).fetchone() == (True, True)
        assert connection.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'agent_runtime_scheduler_cas_facts','SELECT')"
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'mutate_agent_runtime_scheduler_cas(uuid,uuid,uuid,uuid,uuid,text,text,text,bigint,text,jsonb,text,uuid,text)','EXECUTE')"
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'mutate_agent_runtime_scheduler_cas(uuid,uuid,uuid,uuid,uuid,text,text,text,bigint,text,jsonb,text,uuid,text)','EXECUTE')"
        ).fetchone()[0] is False
    ids = _seed_specialist_action(database)
    created = _mutate(database, ids, "create", 0, "scheduler-key-a")
    assert created["outcome"] == "created"
    assert _mutate(database, ids, "create", 0, "scheduler-key-a")["outcome"] == "already_applied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            lambda operation: _mutate(database, ids, operation, 1, f"scheduler-{operation}"),
            ("pause", "resume"),
        ))
    assert sorted(item["outcome"] for item in outcomes) == ["cas_conflict", "updated"]
    with pytest.raises(psycopg.Error, match="AR174_A8_ROLLBACK_GUARD_FACTS_EXIST"):
        _apply(database, "rollback/227_05_agent_runtime_scheduler_cas_rollback.sql")
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("DELETE FROM agent_runtime_scheduler_cas_facts")
        connection.commit()
    _apply(database, "rollback/227_05_agent_runtime_scheduler_cas_rollback.sql")
    _apply(database, "227_05_agent_runtime_scheduler_cas.sql")
    _apply(database, "rollback/227_05_agent_runtime_scheduler_cas_rollback.sql")
