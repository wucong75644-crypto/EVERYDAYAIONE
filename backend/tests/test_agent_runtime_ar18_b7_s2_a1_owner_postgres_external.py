from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import (
    ORG,
    USER,
    _create_payload,
    _mutate,
    _prepare,
    _rpc,
    _seed,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_29_agent_runtime_scheduled_execution_owner.sql"
ROLLBACK = "rollback/227_29_agent_runtime_scheduled_execution_owner_rollback.sql"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _setup(database: str) -> None:
    _prepare(database)
    _apply(database, MIGRATION)


def _create_runtime_task(database: str) -> tuple[str, dict[str, str]]:
    task_id = str(uuid4())
    ids = _seed(database)
    result = _mutate(
        database, ids, task_id, "create", 0,
        f"profile-create:{task_id}", _create_payload(),
    )
    assert result["outcome"] == "committed"
    return task_id, ids


def _seed_release(database: str) -> None:
    tools = [{
        "canonical_name": "memory_search",
        "executor_type": "runtime_read:memory_search",
        "safety_level": "safe",
        "side_effect": "none",
        "authorization_requirement": "none",
    }, {
        "canonical_name": "erp_trade_query",
        "executor_type": "runtime_remote_read:erp_trade_query",
        "safety_level": "safe",
        "side_effect": "none",
        "authorization_requirement": "none",
    }]
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_catalog_facts(catalog_revision,catalog_hash,"
            "catalog_document) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (HASH_A, HASH_A, Jsonb({"tools": tools})),
        )
        conn.execute(
            "INSERT INTO agent_runtime_definition_facts(agent_key,definition_revision,"
            "definition_hash,prompt_revision,catalog_revision,effective_toolset_hash,"
            "definition_document) VALUES('scheduled-safe','v1',%s,'p1',%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (HASH_B, HASH_A, HASH_C, Jsonb({"kind": "scheduled"})),
        )
        conn.execute(
            "INSERT INTO agent_runtime_effective_toolset_facts(agent_key,"
            "definition_revision,catalog_revision,scope_kind,channel,gate_state,"
            "effective_toolset_hash,toolset_document) VALUES"
            "('scheduled-safe','v1',%s,'user','web','enabled',%s,%s) "
            "ON CONFLICT DO NOTHING",
            (HASH_A, HASH_C, Jsonb({"tools": tools, "tool_names": ["memory_search", "erp_trade_query"]})),
        )
        conn.commit()


def _profile(database: str, task_id: str, ids: dict[str, str], *, model=None):
    return _rpc(database, "create_agent_runtime_scheduled_execution_profile_v1", (
        task_id, ORG, USER, ids["action"], ids["run"],
        "scheduled-safe", "v1", HASH_B, HASH_A, HASH_C,
        model or {"model_id": "qwen3.5-plus", "model_revision": "v1"},
        {"scope_kind": "user", "scope_id": USER}, "web",
        {"max_credits": 10, "max_model_steps": 8}, ids["request_hash"], 0,
    ))


def _scheduled_run(database: str, task_id: str) -> str:
    run_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO scheduled_task_runs(id,task_id,org_id,status) "
            "VALUES(%s,%s,%s,'running')", (run_id, task_id, ORG),
        )
        conn.commit()
    return run_id


def _select(database: str, task_id: str, scheduled_run_id: str, *, key="2030-01-01T00:00:00Z"):
    return _rpc(database, "select_agent_runtime_scheduled_run_owner_v1", (
        task_id, scheduled_run_id, ORG, USER, "scheduled", key,
        "2030-01-01T00:00:00Z", None, 1, HASH_A, HASH_B, 0,
    ))


def test_legacy_default_runtime_profile_owner_and_runtime_binding(database: str) -> None:
    _setup(database)
    _seed_release(database)
    runtime_task, ids = _create_runtime_task(database)
    legacy_task = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO scheduled_tasks(id,org_id,user_id,name,prompt,cron_expr,"
            "timezone,push_target,status,runtime_state_version) "
            "VALUES(%s,%s,%s,'Legacy','Read only','0 9 * * *','Asia/Shanghai',"
            "%s,'active',0)",
            (legacy_task, ORG, USER, Jsonb({"type": "web", "user_id": USER})),
        )
        conn.commit()
    legacy_run = _scheduled_run(database, legacy_task)
    defaulted = _rpc(database, "read_agent_runtime_scheduled_run_owner_v1", (
        legacy_task, legacy_run, ORG, USER,
    ))
    assert defaulted == {"outcome": "defaulted", "owner_kind": "legacy"}
    selected_legacy = _rpc(database, "select_agent_runtime_scheduled_run_owner_v1", (
        legacy_task, legacy_run, ORG, USER, "scheduled", "legacy-trigger",
        "2030-01-01T00:00:00Z", None, 0, HASH_A, HASH_B, 0,
    ))
    assert selected_legacy["binding"]["owner_kind"] == "legacy"

    assert _profile(database, runtime_task, ids)["outcome"] == "created"
    assert _profile(database, runtime_task, ids)["outcome"] == "already_exists"
    runtime_scheduled_run = _scheduled_run(database, runtime_task)
    selected_runtime = _select(database, runtime_task, runtime_scheduled_run)
    assert selected_runtime["binding"]["owner_kind"] == "runtime"
    source_command = ids["command"]
    bound = _rpc(database, "bind_agent_runtime_scheduled_run_runtime_v1", (
        runtime_scheduled_run, source_command, ids["run"], 0,
    ))
    assert bound["outcome"] == "bound"
    assert bound["binding"]["owner_status"] == "runtime_claimed"
    assert _rpc(database, "assert_agent_runtime_scheduled_run_owner_v1", (
        runtime_task, runtime_scheduled_run, "runtime",
    ))["outcome"] == "allowed"
    with pytest.raises(Exception, match="SCHEDULED_RUN_RUNTIME_OWNED"):
        _rpc(database, "assert_agent_runtime_scheduled_run_owner_v1", (
            runtime_task, runtime_scheduled_run, "legacy",
        ))


def test_owner_concurrency_fences_acl_and_rollback(database: str) -> None:
    _setup(database)
    _seed_release(database)
    task_id, ids = _create_runtime_task(database)
    _profile(database, task_id, ids)
    scheduled_run = _scheduled_run(database, task_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            lambda _: _select(database, task_id, scheduled_run), range(2),
        ))
    assert sorted(item["outcome"] for item in outcomes) == ["already_selected", "selected"]
    duplicate_run = _scheduled_run(database, task_id)
    duplicate = _select(database, task_id, duplicate_run)
    assert duplicate["outcome"] == "already_selected"
    assert duplicate["binding"]["scheduled_run_id"] == scheduled_run
    with pytest.raises(Exception, match="OWNER_BINDING_INVALID"):
        _rpc(database, "assert_agent_runtime_scheduled_run_owner_v1", (
            task_id, str(uuid4()), "legacy",
        ))
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(Exception, match="OWNER_IDENTITY_IMMUTABLE"):
            conn.execute(
                "UPDATE agent_runtime_scheduled_run_bindings SET owner_kind='legacy',"
                "state_version=state_version+1 WHERE scheduled_run_id=%s",
                (scheduled_run,),
            )
        conn.rollback()
        for table in (
            "agent_runtime_scheduled_execution_profiles",
            "agent_runtime_scheduled_run_bindings",
        ):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (table,),
            ).fetchone() == (True, True)
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_agent_runtime_worker',%s,'SELECT')",
                (table,),
            ).fetchone()[0] is False
        signature = (
            "select_agent_runtime_scheduled_run_owner_v1(uuid,uuid,uuid,uuid,text,text,"
            "timestamp with time zone,text,bigint,text,text,bigint)"
        )
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'EXECUTE')",
            (signature,),
        ).fetchone()[0] is True
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE')", (signature,),
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('public',%s,'EXECUTE')", (signature,),
        ).fetchone()[0] is False
    with pytest.raises(Exception, match="PROFILE_TENANT_MISMATCH"):
        _rpc(database, "create_agent_runtime_scheduled_execution_profile_v1", (
            task_id, str(uuid4()), USER, ids["action"], ids["run"],
            "scheduled-safe", "v1", HASH_B, HASH_A, HASH_C,
            {"model_id": "qwen3.5-plus"}, {"scope_kind": "user", "scope_id": USER},
            "web", {"max_credits": 10}, ids["request_hash"], 0,
        ))
    with pytest.raises(Exception, match="ROLLBACK_OWNER_FACTS_EXIST"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "TRUNCATE agent_runtime_scheduled_run_bindings,"
            "agent_runtime_scheduled_execution_profiles"
        )
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


def test_unattended_profile_rejects_confirm_or_secret_snapshot(database: str) -> None:
    _setup(database)
    _seed_release(database)
    task_id, ids = _create_runtime_task(database)
    with pytest.raises(Exception, match="PROFILE_ARGUMENT_INVALID"):
        _profile(database, task_id, ids, model={"model_id": "x", "api_key": "forbidden"})
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        unsafe_hash = "d" * 64
        conn.execute(
            "INSERT INTO agent_runtime_effective_toolset_facts(agent_key,definition_revision,"
            "catalog_revision,scope_kind,channel,gate_state,effective_toolset_hash,toolset_document) "
            "VALUES('scheduled-safe','v1',%s,'user','wecom','enabled',%s,%s)",
            (HASH_A, unsafe_hash, Jsonb({"tools": [{
                "canonical_name": "erp_execute", "safety_level": "dangerous",
                "side_effect": "external", "authorization_requirement": "explicit_intent",
            }]})),
        )
        conn.commit()
    with pytest.raises(Exception, match="TOOLSET_NOT_UNATTENDED_SAFE"):
        _rpc(database, "create_agent_runtime_scheduled_execution_profile_v1", (
            task_id, ORG, USER, ids["action"], ids["run"], "scheduled-safe", "v1",
            HASH_B, HASH_A, "d" * 64, {"model_id": "x"},
            {"scope_kind": "user", "scope_id": USER}, "wecom",
            {"max_credits": 10}, ids["request_hash"], 0,
        ))
