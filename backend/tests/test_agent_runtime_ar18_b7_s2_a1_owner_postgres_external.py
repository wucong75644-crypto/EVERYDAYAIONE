from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.agent.runtime.catalog.safe_read_release import build_safe_read_snapshot
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import (
    ORG, USER, _create_payload, _mutate, _prepare, _rpc, _seed,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_29_agent_runtime_scheduled_execution_owner.sql"
ROLLBACK = "rollback/227_29_agent_runtime_scheduled_execution_owner_rollback.sql"
HASH_A, HASH_B = "a" * 64, "b" * 64


def _setup(url: str) -> None:
    _prepare(url)
    _apply(url, "227_16_agent_runtime_safe_read_release.sql")
    _apply(url, MIGRATION)


def _freeze_source(url: str, ids: dict[str, str], payload: dict[str, object]) -> None:
    release = build_safe_read_snapshot(scope="user", channel="web", gate_state="enabled")
    definition = release.definition
    model = {
        "model_id": "qwen3.5-plus", "provider": "dashscope",
        "revision": "qwen3.5-plus:test", "source": "default",
    }
    config = {
        "resolved_model": model, "model_id": model["model_id"],
        "provider": model["provider"], "revision": model["revision"],
    }
    capability = {
        "channel": "web", "agent_definition_id": definition.canonical_key,
        "agent_definition_revision": definition.revision,
        "agent_definition_hash": definition.definition_hash,
        "tool_catalog_revision": release.catalog_document["catalog_revision"],
        "tool_catalog_hash": release.catalog_document["catalog_hash"],
        "effective_toolset_revision": release.catalog_document["catalog_revision"],
        "effective_toolset_hash": release.toolset_hash, "gate_state": "enabled",
    }
    identity = {
        "org_id": ORG, "user_id": USER, "scope_kind": "user",
        "scope_id": USER,
    }
    envelope = {
        "schema_revision": 3, "run_kind": "user",
        "config_snapshot": config, "capability_snapshot": capability,
        "request_identity": identity,
    }
    arguments = {"operation": "create", "payload": payload}
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_sessions SET agent_definition_id=%s,"
            "agent_definition_revision=%s WHERE id=%s",
            (definition.canonical_key, definition.revision, ids["session"]),
        )
        conn.execute(
            "UPDATE agent_session_commands SET payload=%s WHERE id=%s",
            (Jsonb({"run_envelope": envelope}), ids["command"]),
        )
        conn.execute(
            "UPDATE agent_runs SET config_snapshot=%s,capability_snapshot=%s WHERE id=%s",
            (Jsonb(config), Jsonb(capability), ids["run"]),
        )
        conn.execute(
            "UPDATE agent_actions SET arguments=%s WHERE id=%s",
            (Jsonb(arguments), ids["action"]),
        )
        conn.commit()


def _create_runtime_task(url: str) -> tuple[str, dict[str, str]]:
    task_id, ids, payload = str(uuid4()), _seed(url), _create_payload()
    _freeze_source(url, ids, payload)
    result = _mutate(
        url, ids, task_id, "create", 0, f"profile-create:{task_id}", payload,
    )
    assert result["outcome"] == "committed"
    return task_id, ids


def _profile(url: str, task_id: str, ids: dict[str, str]):
    return _rpc(url, "create_agent_runtime_scheduled_execution_profile_v1", (
        task_id, ids["action"], ids["run"], 0,
    ))


def _legacy_task(url: str) -> str:
    task_id = str(uuid4())
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO scheduled_tasks(id,org_id,user_id,name,prompt,cron_expr,"
            "timezone,push_target,status,runtime_state_version) VALUES"
            "(%s,%s,%s,'Legacy','Read only','0 9 * * *','Asia/Shanghai',%s,'active',0)",
            (task_id, ORG, USER, Jsonb({"type": "web", "user_id": USER})),
        )
        conn.commit()
    return task_id


def _scheduled_run(url: str, task_id: str) -> str:
    run_id = str(uuid4())
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO scheduled_task_runs(id,task_id,org_id,status) "
            "VALUES(%s,%s,%s,'running')", (run_id, task_id, ORG),
        )
        conn.commit()
    return run_id


def _select(
    url: str, task_id: str, run_id: str, *, key: str = "trigger-1",
    revision: int = 1, epoch: int = 0,
):
    return _rpc(url, "select_agent_runtime_scheduled_run_owner_v1", (
        task_id, run_id, ORG, USER, "scheduled", key,
        "2030-01-01T00:00:00Z", None, revision, HASH_A, HASH_B, epoch,
    ))


def _set_tenant_gate(url: str, *, blocked: bool, epoch: int) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls(org_id,gate_scope,scope_key,"
            "ingress_blocked,claim_blocked,dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'tenant','tenant',%s,%s,%s,%s,1,'test',%s) "
            "ON CONFLICT(org_id,gate_scope,scope_key) DO UPDATE SET "
            "ingress_blocked=EXCLUDED.ingress_blocked,claim_blocked=EXCLUDED.claim_blocked,"
            "dispatch_blocked=EXCLUDED.dispatch_blocked,kill_epoch=EXCLUDED.kill_epoch",
            (ORG, blocked, blocked, blocked, epoch, USER),
        )
        conn.commit()


def test_profile_unbound_never_defaults_legacy_and_old_gate_is_stable(database: str) -> None:
    _setup(database)
    runtime_task, ids = _create_runtime_task(database)
    assert _profile(database, runtime_task, ids)["outcome"] == "created"
    runtime_run = _scheduled_run(database, runtime_task)
    read = _rpc(database, "read_agent_runtime_scheduled_run_owner_v1", (
        runtime_task, runtime_run, ORG, USER,
    ))
    assert read == {"outcome": "runtime_profile_unbound", "owner_kind": "runtime"}
    with pytest.raises(Exception, match="SCHEDULED_RUN_RUNTIME_PROFILE_UNBOUND"):
        _rpc(database, "assert_agent_runtime_scheduled_run_owner_v1", (
            runtime_task, runtime_run, "legacy",
        ))

    legacy_task = _legacy_task(database)
    legacy_run = _scheduled_run(database, legacy_task)
    allowed = _rpc(database, "assert_agent_runtime_scheduled_run_owner_v1", (
        legacy_task, legacy_run, "legacy",
    ))
    assert allowed == {"outcome": "allowed", "owner_kind": "legacy"}
    selected = _select(database, runtime_task, runtime_run)
    assert selected["binding"]["owner_kind"] == "runtime"
    with pytest.raises(Exception, match="SCHEDULED_RUN_RUNTIME_OWNED"):
        _rpc(database, "assert_agent_runtime_scheduled_run_owner_v1", (
            runtime_task, runtime_run, "legacy",
        ))


def test_same_tenant_command_and_missing_scheduled_context_are_rejected(database: str) -> None:
    _setup(database)
    task_id, ids = _create_runtime_task(database)
    _profile(database, task_id, ids)
    scheduled_run = _scheduled_run(database, task_id)
    _select(database, task_id, scheduled_run)
    with pytest.raises(Exception, match="SCHEDULED_CONTEXT_ENVELOPE_REQUIRED"):
        _rpc(database, "bind_agent_runtime_scheduled_run_runtime_v1", (
            scheduled_run, ids["command"], ids["run"], 0,
        ))
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT runtime_command_id,runtime_run_id,state_version FROM "
            "agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=%s",
            (scheduled_run,),
        ).fetchone() == (None, None, 0)


def test_profile_derivation_rejects_model_toolset_budget_and_scope_tampering(database: str) -> None:
    _setup(database)
    mutations = (
        ("model", "UPDATE agent_runs SET config_snapshot=jsonb_set(config_snapshot,"
         "'{resolved_model,model_id}','\"tampered\"') WHERE id=%s", "run"),
        ("toolset", "UPDATE agent_runs SET capability_snapshot=jsonb_set("
         "capability_snapshot,'{effective_toolset_hash}',to_jsonb(%s::text)) WHERE id=%s", "run"),
        ("budget", "UPDATE scheduled_tasks SET max_credits=max_credits+1 WHERE id=%s", "task"),
        ("scope", "UPDATE agent_session_commands SET payload=jsonb_set(payload,"
         "'{run_envelope,request_identity,scope_id}',to_jsonb(%s::text)) WHERE id=%s", "command"),
    )
    for kind, sql, target in mutations:
        task_id, ids = _create_runtime_task(database)
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            if kind == "toolset":
                conn.execute(sql, ("f" * 64, ids[target]))
            elif kind == "scope":
                conn.execute(sql, ("tampered", ids[target]))
            else:
                conn.execute(sql, (task_id if target == "task" else ids[target],))
            conn.commit()
        with pytest.raises(Exception, match="PROFILE_(SOURCE_INVALID|TOOLSET_NOT_APPROVED)"):
            _profile(database, task_id, ids)


def test_kill_epoch_and_provider_capability_gates_fence_owner_selection(database: str) -> None:
    _setup(database)
    task_id, ids = _create_runtime_task(database)
    _profile(database, task_id, ids)
    next_task, next_ids = _create_runtime_task(database)
    _profile(database, next_task, next_ids)
    blocked_run = _scheduled_run(database, task_id)
    _set_tenant_gate(database, blocked=True, epoch=1)
    with pytest.raises(Exception, match="TENANT_FENCED"):
        _select(database, task_id, blocked_run, epoch=1)
    _set_tenant_gate(database, blocked=False, epoch=1)
    with pytest.raises(Exception, match="TENANT_FENCED"):
        _select(database, task_id, blocked_run, epoch=0)
    assert _select(database, task_id, blocked_run, epoch=1)["outcome"] == "selected"

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls(org_id,gate_scope,scope_key,"
            "dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'provider','scheduler',true,1,1,'test',%s)", (ORG, USER),
        )
        conn.commit()
    with pytest.raises(Exception, match="PROVIDER_FENCED"):
        _select(database, next_task, _scheduled_run(database, next_task), epoch=1)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_tenant_gate_controls SET dispatch_blocked=false "
            "WHERE org_id=%s AND gate_scope='provider' AND scope_key='scheduler'", (ORG,),
        )
        conn.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls(org_id,gate_scope,scope_key,"
            "dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'capability','runtime.scheduler.control',true,1,1,'test',%s)",
            (ORG, USER),
        )
        conn.commit()
    with pytest.raises(Exception, match="CAPABILITY_FENCED"):
        _select(database, next_task, _scheduled_run(database, next_task), epoch=1)


def test_owner_lock_order_concurrency_acl_and_rollback(database: str) -> None:
    _setup(database)
    task_id, ids = _create_runtime_task(database)
    _profile(database, task_id, ids)
    run_id = _scheduled_run(database, task_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_select, database, task_id, run_id, key=key)
                   for key in ("trigger-a", "trigger-b")]
        results, errors = [], []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as error:
                errors.append(error)
    assert len(results) == 1 and results[0]["outcome"] == "selected"
    assert len(errors) == 1 and "OWNER_IDEMPOTENCY_CONFLICT" in str(errors[0])
    duplicate_run = _scheduled_run(database, task_id)
    winner_key = results[0]["binding"]["trigger_key"]
    assert _select(database, task_id, duplicate_run, key=winner_key)["outcome"] == "already_selected"

    with psycopg.connect(database) as conn:
        for table in ("agent_runtime_scheduled_execution_profiles", "agent_runtime_scheduled_run_bindings"):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (table,),
            ).fetchone() == (True, True)
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_agent_runtime_worker',%s,'SELECT')", (table,),
            ).fetchone()[0] is False
        signature = "select_agent_runtime_scheduled_run_owner_v1(uuid,uuid,uuid,uuid,text,text,timestamp with time zone,text,bigint,text,text,bigint)"
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'EXECUTE')", (signature,)).fetchone()[0] is True
        assert conn.execute("SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE')", (signature,)).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'_agent_runtime_scheduled_owner_gate(uuid,uuid,text)','EXECUTE')"
        ).fetchone()[0] is False
    with pytest.raises(Exception, match="ROLLBACK_OWNER_FACTS_EXIST"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_scheduled_run_bindings,agent_runtime_scheduled_execution_profiles")
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)
