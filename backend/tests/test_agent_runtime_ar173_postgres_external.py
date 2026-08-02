"""AR-17.3 migration contract on the disposable local PostgreSQL fixture."""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Mapping
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from core.db_scope import AsyncScopedDatabaseClient, DatabaseAccessKind, DatabaseScope
from core.local_db import AsyncLocalDBClient
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.resolver import PostgresActionExecutorResolver
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.specialist_contracts import ProviderReceipt, ProviderState
from services.agent.runtime.ports.executor import ExecutionOutcome
from services.agent.runtime.executors.specialist_registry import specialist_descriptor
from services.agent.runtime.infrastructure.postgres.action_repository import PostgresActionRepository
from services.agent.runtime.infrastructure.postgres.authorization import PostgresActionAuthorizationRepository
from services.agent.runtime.infrastructure.postgres.coordinator_recovery import PostgresCoordinatorRecoveryRepository
from services.agent.runtime.infrastructure.postgres.specialist_repository import PostgresSpecialistRepository
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.resource_contracts import ErpSyncService
from services.agent.runtime.executors.resource_support import sync_idempotency_key
from services.agent.runtime.ports.coordinator_recovery import ActionDispatchSnapshot


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _rollback(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / name).read_text())


def test_ar173_226_apply_rollback_reapply_and_worker_acl(database: str) -> None:
    migrations = [f"226_{index:02d}_" for index in range(1, 20)]
    names = [next((ROOT / "migrations").glob(f"{prefix}*.sql")).name for prefix in migrations]
    rollbacks = [next((ROOT / "migrations/rollback").glob(f"{prefix}*_rollback.sql")).name for prefix in reversed(migrations)]
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for name in names:
        _apply(database, name)
    with psycopg.connect(database) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
        assert {"agent_action_callback_inbox", "agent_action_cost_settlements", "agent_action_artifact_links"} <= tables
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='agent_action_cost_settlements'::regclass").fetchone() == (True, True)
        assert conn.execute("SELECT has_table_privilege('everydayai_agent_runtime_worker','agent_action_cost_settlements','SELECT')").fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','reserve_agent_action_cost(UUID,UUID,BIGINT,TEXT)','EXECUTE')").fetchone()[0] is True
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','finalize_agent_action_provider(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT)','EXECUTE')").fetchone()[0] is False
    for name in rollbacks:
        _rollback(database, name)
        with psycopg.connect(database) as conn:
            for signature in (
                "finalize_agent_action_provider(uuid,uuid,uuid,text,text,jsonb,jsonb,text,bigint,bigint,text,text,text)",
                "complete_agent_child_run_strict(uuid,uuid,uuid,text,integer,jsonb)",
                "cancel_agent_child_run_strict(uuid,uuid,uuid,text,text)",
                "complete_agent_child_run(uuid,uuid,integer,jsonb)",
                "cancel_agent_child_run(uuid,uuid,text)",
            ):
                present = conn.execute("SELECT to_regprocedure(%s)", (signature,)).fetchone()[0]
                if present is not None:
                    assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'EXECUTE')", (signature,)).fetchone()[0] is False
    for name in names:
        _apply(database, name)
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT to_regclass('agent_action_cost_settlements')").fetchone()[0] == "agent_action_cost_settlements"


def _seed_specialist_action(database: str, conversation_id: str = "55555555-5555-5555-5555-555555555555") -> dict[str, str]:
    ids = {name: str(uuid4()) for name in ("session", "command", "run", "step", "action", "attempt", "token", "policy")}
    request_hash = "a" * 64
    run_hash = "b" * 32
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,agent_definition_id,agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')", (ids["session"], conversation_id, "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", "44444444-4444-4444-4444-444444444444", "44444444-4444-4444-4444-444444444444"))
        conn.execute("INSERT INTO agent_session_commands(id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)", (ids["command"], ids["session"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["command"], run_hash))
        conn.execute("INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,idempotency_key,request_hash,status,execution_token,lease_expires_at,context_receipt,config_snapshot,capability_snapshot) VALUES(%s,%s,%s,%s,%s,'user',%s,%s,'running',%s,clock_timestamp()+interval '10 minutes','{}','{}','{}')", (ids["run"], ids["session"], ids["command"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["run"], run_hash, ids["token"]))
        conn.execute("INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,model_id,provider,model_revision,prompt_revision,tool_catalog_revision) VALUES(%s,%s,%s,%s,%s,1,'fixture','fixture','v1','v1','v1')", (ids["step"], ids["run"], ids["session"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444"))
        conn.execute("INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,batch_hash,policy_decision,policy_snapshot,policy_revision,retry_disposition,status) VALUES(%s,%s,%s,%s,%s,%s,0,%s,'generate_image','{}',%s,%s,%s,'preauthorized','{}','v1','retry_after_reconcile','running')", (ids["action"], ids["session"], ids["run"], ids["step"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["action"], "b" * 64, request_hash, "c" * 64))
        conn.execute("INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,user_id,attempt_number,status,dispatch_phase,worker_id,execution_token,lease_expires_at,idempotency_key,request_hash,retry_disposition) VALUES(%s,%s,%s,%s,%s,%s,1,'dispatching','request_started','fixture-worker',%s,clock_timestamp()+interval '10 minutes',%s,%s,'retry_after_reconcile')", (ids["attempt"], ids["action"], ids["session"], ids["run"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["token"], ids["attempt"], request_hash))
        conn.execute("INSERT INTO agent_policy_receipts(id,action_id,session_id,run_id,org_id,user_id,decision,arguments_hash,executor_type,executor_revision,policy_revision,effective_scope,reason_codes,receipt_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,'allow',%s,'runtime_media_generation:generate_image',1,'v1','{}',ARRAY['fixture'],%s,clock_timestamp()+interval '10 minutes')", (ids["policy"], ids["action"], ids["session"], ids["run"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", "b" * 64, "d" * 64))
        conn.execute("INSERT INTO agent_action_dispatch_intents(attempt_id,action_id,policy_receipt_id,execution_token,request_hash,executor_type,executor_revision,policy_revision,external_idempotency_key,recovery_mode) VALUES(%s,%s,%s,%s,%s,'runtime_media_generation:generate_image',1,'v1',%s,'idempotent_replay')", (ids["attempt"], ids["action"], ids["policy"], ids["token"], request_hash, ids["attempt"]))
        conn.execute("UPDATE agent_runs SET blocking_action_count=1 WHERE id=%s", (ids["run"],))
        ids["artifact"] = str(uuid4())
        conn.execute("INSERT INTO conversation_artifacts(id,conversation_id,org_id) VALUES(%s,%s,%s)", (ids["artifact"], conversation_id, "22222222-2222-2222-2222-222222222222"))
        conn.commit()
    ids["request_hash"] = request_hash
    return ids


def _worker_rpc(database: str, function: str, params: tuple[object, ...]) -> object:
    worker_url = database.replace("postgres@", "everydayai_agent_runtime_worker@")
    with psycopg.connect(worker_url) as conn:
        adapted = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
        value = conn.execute(f"SELECT {function}({','.join(['%s'] * len(params))})", adapted).fetchone()[0]
        conn.commit()
        return value


def _worker_rpc_outcome(database: str, function: str, params: tuple[object, ...]) -> tuple[str, object]:
    try:
        return ("ok", _worker_rpc(database, function, params))
    except Exception as error:
        detail = getattr(getattr(error, "diag", None), "message_primary", None) or str(error).splitlines()[0]
        context = getattr(getattr(error, "diag", None), "context", None) or ""
        return ("error", f"{type(error).__name__}:{detail}:{context}")


def test_ar173_worker_rpc_behavior_matrix_and_50_concurrent_idempotency(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)
    reserve_params = (ids["action"], ids["attempt"], "reserve", 3, 0, "credits", "runtime", None)
    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(lambda _: _worker_rpc(database, "record_agent_action_cost_strict", reserve_params), range(50)))
    assert sum(item["outcome"] == "applied" for item in outcomes) == 1
    assert sum(item["outcome"] == "idempotent_readback" for item in outcomes) == 49
    with pytest.raises(Exception):
        _worker_rpc(database, "record_agent_action_cost_strict", (ids["action"], ids["attempt"], "reserve", 4, 0, "credits", "runtime", None))
    submission = _worker_rpc(database, "record_agent_action_provider_submission", (ids["attempt"], ids["token"], ids["request_hash"], "kie", "task-1", "/status", "corr-1", ids["attempt"], ids["request_hash"], None, {"state": "accepted"}))
    assert submission["outcome"] == "accepted"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        deleted_id = conn.execute("INSERT INTO deleted_files(org_id,user_id,relative_path,oss_object_key) VALUES(%s,%s,'report.csv','workspace/report.csv') RETURNING id", ("22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444")).fetchone()[0]
        task_id = str(uuid4())
        conn.execute("INSERT INTO scheduled_tasks(id,org_id,user_id) VALUES(%s,%s,%s)", (task_id, "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444"))
        conn.commit()
    bound_delete = _worker_rpc(database, "runtime_delete_workspace_resource", (deleted_id, ids["action"], ids["attempt"], ids["request_hash"], ids["attempt"], ids["token"]))
    assert bound_delete["outcome"] == "bound"
    bound_task = _worker_rpc(database, "runtime_mutate_scheduled_task", (task_id, ids["action"], ids["attempt"], 0, ids["request_hash"], ids["attempt"], {"operation": "pause"}, ids["token"]))
    assert bound_task["outcome"] == "updated"
    linked = _worker_rpc(database, "link_agent_action_artifact", (ids["action"], ids["attempt"], ids["artifact"], "output", None, "e" * 64, 1, "materialized", "normal"))
    assert linked["outcome"] == "linked"
    callback = ("kie", "event-1", "corr-1", "e" * 64, {"state": "accepted"}, ids["action"], ids["attempt"])
    with ThreadPoolExecutor(max_workers=50) as pool:
        callbacks = list(pool.map(lambda _: _worker_rpc(database, "record_agent_action_callback_strict", callback), range(50)))
    assert sum(item["outcome"] == "accepted" for item in callbacks) == 1
    assert sum(item["outcome"] == "idempotent_readback" for item in callbacks) == 49
    child = _worker_rpc(database, "create_agent_child_run_strict", (ids["run"], ids["action"], ids["request_hash"], ids["token"], 0, "runtime.child", {"policy_receipt_id": ids["policy"], "capability": "runtime.child", "budget_remaining": 1, "scope": {"org_id": "22222222-2222-2222-2222-222222222222", "user_id": "44444444-4444-4444-4444-444444444444"}}))
    assert child["outcome"] == "created"
    readback = _worker_rpc(database, "read_agent_child_run_strict", (child["child_run_id"], ids["run"], ids["action"], ids["request_hash"]))
    assert readback["outcome"] == "readback" and readback["status"] == "queued"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_rpc(database, "complete_agent_child_run_strict", (child["child_run_id"], ids["run"], ids["action"], ids["request_hash"], 1, {"items": []}))
    reconciliation_token = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_action_attempts SET reconciliation_token=%s,reconciliation_lease_expires_at=clock_timestamp()+interval '10 minutes' WHERE id=%s", (reconciliation_token, ids["attempt"]))
        parent_version = conn.execute("SELECT state_version FROM agent_action_attempts WHERE id=%s", (ids["attempt"],)).fetchone()[0]
        conn.commit()
    for phase in ("submitted", "progressing", "applying", "checkpointed"):
        phase_result = _worker_rpc(database, "record_agent_sync_phase_v3", (ids["action"], ids["attempt"], reconciliation_token, parent_version, datetime.now(timezone.utc) + timedelta(minutes=10), ids["request_hash"], phase, {"phase": phase}, {"provider": "erp"}))
        assert phase_result["outcome"] == "recorded"
    _worker_rpc(database, "record_agent_action_cost_strict", (ids["action"], ids["attempt"], "settle", 3, 9, "credits", "runtime", "f" * 64))
    with pytest.raises(Exception):
        _worker_rpc(database, "finalize_agent_action_provider_v2", (ids["attempt"], None, reconciliation_token, 1, ids["request_hash"], "completed", {"state": "completed", "provider_task_ref": "task-1"}, {"status": "success", "summary": "ok", "data": {}, "external_receipt": {"provider": "kie"}}, "settle", 3, 1, "credits", "runtime", "f" * 64))
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT status FROM agent_actions WHERE id=%s", (ids["action"],)).fetchone()[0] == "accepted"
    finalized = _worker_rpc(database, "finalize_agent_action_provider_v2", (ids["attempt"], None, reconciliation_token, 1, ids["request_hash"], "completed", {"state": "completed", "provider_task_ref": "task-1"}, {"status": "success", "summary": "ok", "data": {}, "external_receipt": {"provider": "kie"}}, "settle", 3, 9, "credits", "runtime", "f" * 64))
    assert finalized["outcome"] == "completed"
    duplicate_settle = _worker_rpc(database, "record_agent_action_cost_strict", (ids["action"], ids["attempt"], "settle", 3, 9, "credits", "runtime", "f" * 64))
    assert duplicate_settle["outcome"] == "idempotent_readback"
    fenced = _worker_rpc(database, "record_agent_action_provider_terminal", (ids["attempt"], str(uuid4()), ids["request_hash"], "completed", {}, {}))
    assert fenced["outcome"] == "fenced"


def test_ar173_real_action_loop_postgres_specialist_e2e(database: str) -> None:
    """The acceptance path uses both formal Postgres repositories end to end."""
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        ids = {name: str(uuid4()) for name in ("session", "command", "run", "step", "action", "attempt", "token", "policy")}
        request_hash = canonical_request_hash({})
        arguments_hash = canonical_request_hash({})
        org = "22222222-2222-2222-2222-222222222222"
        user = "44444444-4444-4444-4444-444444444444"
        conversation = "55555555-5555-5555-5555-555555555555"
        conn.execute("INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,agent_definition_id,agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')", (ids["session"], conversation, org, user, user, user))
        conn.execute("INSERT INTO agent_session_commands(id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)", (ids["command"], ids["session"], org, user, ids["command"], "b" * 32))
        conn.execute("INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,idempotency_key,request_hash,status,execution_token,lease_expires_at,context_receipt,config_snapshot,capability_snapshot,blocking_action_count) VALUES(%s,%s,%s,%s,%s,'user',%s,%s,'running',%s,clock_timestamp()+interval '10 minutes','{}','{}','{}',1)", (ids["run"], ids["session"], ids["command"], org, user, ids["run"], "b" * 32, ids["token"]))
        conn.execute("INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,model_id,provider,model_revision,prompt_revision,tool_catalog_revision) VALUES(%s,%s,%s,%s,%s,1,'fixture','fixture','v1','v1','v1')", (ids["step"], ids["run"], ids["session"], org, user))
        conn.execute("INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,batch_hash,policy_decision,policy_snapshot,policy_revision,retry_disposition,status) VALUES(%s,%s,%s,%s,%s,%s,0,%s,'generate_image','{}',%s,%s,%s,'preauthorized',%s,'v1','retry_after_reconcile','queued')", (ids["action"], ids["session"], ids["run"], ids["step"], org, user, ids["action"], arguments_hash, request_hash, "c" * 64, Jsonb({"dispatch_policy_receipt_id": ids["policy"], "safety_level": "safe"})))
        conn.execute("INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,user_id,attempt_number,status,dispatch_phase,worker_id,execution_token,lease_expires_at,idempotency_key,request_hash,retry_disposition) VALUES(%s,%s,%s,%s,%s,%s,1,'claimed','claimed','fixture',%s,clock_timestamp()+interval '10 minutes',%s,%s,'retry_after_reconcile')", (ids["attempt"], ids["action"], ids["session"], ids["run"], org, user, ids["token"], ids["attempt"], request_hash))
        conn.execute("INSERT INTO agent_policy_receipts(id,action_id,session_id,run_id,org_id,user_id,decision,arguments_hash,executor_type,executor_revision,policy_revision,effective_scope,reason_codes,receipt_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,'allow',%s,'runtime_media_generation:generate_image',1,'v1','{}',ARRAY['fixture'],%s,clock_timestamp()+interval '10 minutes')", (ids["policy"], ids["action"], ids["session"], ids["run"], org, user, arguments_hash, "d" * 64))
        conn.execute("INSERT INTO agent_runtime_org_rollout(org_id,enabled,updated_by,update_reason) VALUES(%s,TRUE,%s,'isolated ar173 e2e')", (org, user))
        conn.execute("UPDATE agent_runtime_control SET action_dispatch_enabled=TRUE, safe_actions_enabled=TRUE, release_revision='isolated-ar173', config_revision='isolated-ar173'")
        conn.commit()

    class _Provider:
        calls = 0
        async def submit(self, attempt, request, *, idempotency_key):
            self.calls += 1
            return ProviderReceipt(state=ProviderState.COMPLETED, provider="local-mock", request_hash=attempt.request_hash, result={"summary": "ok", "count": 1}, cost={"credits": 2})
        async def reconcile(self, attempt, receipt):
            return ProviderReceipt(state=ProviderState.COMPLETED, provider="local-mock", request_hash=attempt.request_hash, result={"summary": "ok"}, cost={"credits": 2})
        async def cancel(self, attempt, receipt):
            return ProviderReceipt(state=ProviderState.UNKNOWN, provider="local-mock", request_hash=attempt.request_hash, evidence={"error_code": "cancel_unproven"})

    async def run() -> None:
        client = AsyncLocalDBClient(database.replace("postgres@", "everydayai_agent_runtime_worker@"), min_size=1, max_size=4)
        await client.open()
        scoped = AsyncScopedDatabaseClient(client, DatabaseScope(actor_user_id="44444444-4444-4444-4444-444444444444", org_id="22222222-2222-2222-2222-222222222222", access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="ar173-e2e"))
        try:
            facts = PostgresSpecialistRepository(scoped)
            registry = ExecutorRegistry()
            provider = _Provider()
            descriptor = specialist_descriptor("generate_image")
            registry.register(descriptor, SpecialistExecutor(executor_type=descriptor.executor_type, revision=descriptor.revision, provider=provider), safety_level="dangerous")
            registry.specialist_facts = facts
            driver = ActionLoopDriver(recovery_repository=PostgresCoordinatorRecoveryRepository(scoped), action_repository=PostgresActionRepository(scoped), authorization_repository=PostgresActionAuthorizationRepository(scoped), resolver=PostgresActionExecutorResolver(registry), worker_id="e2e-worker", renew_interval=60)
            assert await driver.dispatch_once() is True
            assert provider.calls == 1
        finally:
            await client.close()
    import asyncio
    asyncio.run(run())
    with psycopg.connect(database) as conn:
        state = conn.execute("SELECT a.status,t.status,r.status,run.blocking_action_count FROM agent_actions a JOIN agent_action_attempts t ON t.action_id=a.id LEFT JOIN agent_action_results r ON r.action_id=a.id JOIN agent_runs run ON run.id=a.run_id WHERE a.id=%s", (ids["action"],)).fetchone()
        assert state == ("completed", "completed", "success", 0)
        assert conn.execute("SELECT count(*) FROM agent_action_results WHERE action_id=%s", (ids["action"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM agent_action_cost_settlements WHERE action_id=%s AND kind='settle'", (ids["action"],)).fetchone()[0] == 1


def test_ar173_fifty_concurrent_finalize_has_one_terminal_winner(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)
    params = (ids["attempt"], ids["token"], None, 0, ids["request_hash"], "completed",
              {"state": "completed", "provider_task_ref": "task-50"},
              {"status": "success", "summary": "ok", "data": {}, "external_receipt": {"provider": "local-mock"}},
              "settle", 0, 2, "credits", "runtime", "a" * 64)
    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(lambda _: _worker_rpc_outcome(database, "finalize_agent_action_provider_v2", params), range(50)))
    successful = [value for kind, value in outcomes if kind == "ok"]
    assert len(successful) == 1
    assert sum(kind == "error" for kind, _ in outcomes) == 49
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT status FROM agent_actions WHERE id=%s", (ids["action"],)).fetchone()[0] == "completed"
        assert conn.execute("SELECT count(*) FROM agent_action_results WHERE action_id=%s", (ids["action"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM agent_action_cost_settlements WHERE action_id=%s AND kind='settle'", (ids["action"],)).fetchone()[0] == 1


def test_ar173_fifty_concurrent_child_create_and_sync_phase_idempotency(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)
    context = {"policy_receipt_id": ids["policy"], "capability": "runtime.child", "budget_remaining": 1, "scope": {"org_id": "22222222-2222-2222-2222-222222222222", "user_id": "44444444-4444-4444-4444-444444444444"}}
    child_params = (ids["run"], ids["action"], ids["request_hash"], ids["token"], 0, "runtime.child", context)
    with ThreadPoolExecutor(max_workers=50) as pool:
        children = list(pool.map(lambda _: _worker_rpc_outcome(database, "create_agent_child_run_strict", child_params), range(50)))
    created = [value for kind, value in children if kind == "ok" and value.get("outcome") == "created"]
    readbacks = [value for kind, value in children if kind == "ok" and value.get("outcome") == "already_exists"]
    assert len(created) == 1 and len(readbacks) == 49
    checkpoint = {"provider_task_ref": "sync-1", "cursor": 1}
    phase_params = (ids["action"], ids["attempt"], ids["token"], 0, datetime.now(timezone.utc) + timedelta(minutes=10), ids["request_hash"], "submitted", checkpoint, {"provider": "erp", "receipt": "r1"})
    with ThreadPoolExecutor(max_workers=50) as pool:
        phases = list(pool.map(lambda _: _worker_rpc_outcome(database, "record_agent_sync_phase_v3", phase_params), range(50)))
    assert sum(kind == "ok" for kind, _ in phases) == 50
    conflict_params = (*phase_params[:6], "submitted", {"provider_task_ref": "sync-2"}, {"provider": "erp", "receipt": "r2"})
    assert _worker_rpc_outcome(database, "record_agent_sync_phase_v3", conflict_params)[0] == "error"


def test_ar173_fifty_concurrent_sync_submission_mapping_is_stable(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)
    key = "sync-key-50"
    params = (ids["action"], ids["attempt"], ids["request_hash"], "org-scope", "orders", key, "erp")
    with ThreadPoolExecutor(max_workers=50) as pool:
        mappings = list(pool.map(lambda _: _worker_rpc_outcome(database, "create_or_get_agent_sync_submission", params), range(50)))
    values = [value for kind, value in mappings if kind == "ok"]
    assert len(values) == 50
    assert sum(value["outcome"] == "created" for value in values) == 1
    assert sum(value["outcome"] == "readback" for value in values) == 49
    submission_id = next(value["submission_id"] for value in values)
    recorded = _worker_rpc(database, "record_agent_sync_submission_result", (submission_id, key, ids["request_hash"], "sync-task-1", "found", {"enqueued": True}))
    assert recorded["provider_task_ref"] == "sync-task-1"
    recovered = _worker_rpc(database, "recover_agent_sync_submission", (key, ids["request_hash"]))
    assert recovered["outcome"] == "found" and recovered["provider_task_ref"] == "sync-task-1"
    with pytest.raises(Exception):
        _worker_rpc(database, "create_or_get_agent_sync_submission", (ids["action"], ids["attempt"], ids["request_hash"], "different-scope", "orders", key, "erp"))


def test_ar173_formal_action_loop_accepted_cancel_postgres_e2e(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)
    reconciliation = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_actions SET status='accepted' WHERE id=%s", (ids["action"],))
        conn.execute("UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',last_provider_status='accepted',accepted_at=clock_timestamp(),state_version=1,reconciliation_token=%s,reconciliation_lease_expires_at=clock_timestamp()+interval '10 minutes',external_receipt=%s WHERE id=%s", (reconciliation, Jsonb({"provider": "local", "provider_task_ref": "task-cancel"}), ids["attempt"]))
        conn.execute("UPDATE agent_runs SET blocking_action_count=1,status='running' WHERE id=%s", (ids["run"],))
        conn.commit()

    class _CancelProvider:
        async def submit(self, attempt, request, *, idempotency_key):
            raise AssertionError("cancel E2E must not submit")
        async def reconcile(self, attempt, receipt):
            raise AssertionError("cancel E2E must not reconcile")
        async def cancel(self, attempt, receipt):
            return ProviderReceipt(state=ProviderState.CANCELLED, provider="local", request_hash=attempt.request_hash, provider_task_ref="task-cancel", evidence={"cancel_confirmed": True})

    async def run() -> None:
        client = AsyncLocalDBClient(database.replace("postgres@", "everydayai_agent_runtime_worker@"), min_size=1, max_size=4)
        await client.open()
        scoped = AsyncScopedDatabaseClient(client, DatabaseScope(actor_user_id="44444444-4444-4444-4444-444444444444", org_id="22222222-2222-2222-2222-222222222222", access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="ar173-cancel"))
        try:
            facts = PostgresSpecialistRepository(scoped)
            descriptor = specialist_descriptor("generate_image")
            registry = ExecutorRegistry()
            registry.register(descriptor, SpecialistExecutor(executor_type=descriptor.executor_type, revision=1, provider=_CancelProvider()), safety_level="dangerous")
            registry.specialist_facts = facts
            driver = ActionLoopDriver(recovery_repository=PostgresCoordinatorRecoveryRepository(scoped), action_repository=PostgresActionRepository(scoped), authorization_repository=PostgresActionAuthorizationRepository(scoped), resolver=PostgresActionExecutorResolver(registry), worker_id="cancel-e2e", renew_interval=60)
            snapshot = ActionDispatchSnapshot(
                attempt={"id": ids["attempt"], "action_id": ids["action"], "status": "accepted", "execution_token": ids["token"], "reconciliation_token": reconciliation, "reconciliation_lease_expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(minutes=5), "lease_expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(minutes=5), "accepted_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc), "state_version": 1, "request_hash": ids["request_hash"], "attempt_number": 1, "worker_id": "cancel-e2e", "idempotency_key": ids["attempt"], "claimed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc), "external_receipt": {"provider": "local", "provider_task_ref": "task-cancel"}},
                action={"id": ids["action"], "action_id": ids["action"], "run_id": ids["run"], "session_id": ids["session"], "tool_name": "generate_image", "arguments": {}, "request_hash": ids["request_hash"], "policy_decision": "preauthorized", "policy_revision": "v1", "retry_disposition": "retry_after_reconcile", "state_version": 0, "policy_snapshot": {}, "scope_kind": "user", "scope_id": "44444444-4444-4444-4444-444444444444", "user_id": "44444444-4444-4444-4444-444444444444", "org_id": "22222222-2222-2222-2222-222222222222"},
            )
            assert await driver.cancel_action(snapshot) is ExecutionOutcome.CANCELLED
        finally:
            await client.close()
    import asyncio
    asyncio.run(run())
    with psycopg.connect(database) as conn:
        state = conn.execute("SELECT a.status,t.status,r.status,run.blocking_action_count FROM agent_actions a JOIN agent_action_attempts t ON t.id=%s LEFT JOIN agent_action_results r ON r.action_id=a.id JOIN agent_runs run ON run.id=a.run_id WHERE a.id=%s", (ids["attempt"], ids["action"])).fetchone()
        assert state == ("cancelled", "cancelled", "empty", 0)
        assert conn.execute("SELECT count(*) FROM agent_action_results WHERE action_id=%s", (ids["action"],)).fetchone()[0] == 1


def test_ar173_resource_and_scheduler_cas_50_concurrency(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        deleted_id = conn.execute("INSERT INTO deleted_files(org_id,user_id,relative_path,oss_object_key) VALUES(%s,%s,'report.csv','workspace/report.csv') RETURNING id", ("22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444")).fetchone()[0]
        task_id = str(uuid4())
        conn.execute("INSERT INTO scheduled_tasks(id,org_id,user_id) VALUES(%s,%s,%s)", (task_id, "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444"))
        conn.commit()
    delete_params = (deleted_id, ids["action"], ids["attempt"], ids["request_hash"], ids["attempt"], ids["token"])
    with ThreadPoolExecutor(max_workers=50) as pool:
        deletes = list(pool.map(lambda _: _worker_rpc_outcome(database, "runtime_delete_workspace_resource", delete_params), range(50)))
    assert sum(kind == "ok" and value["outcome"] == "bound" for kind, value in deletes) == 1
    assert sum(kind == "ok" and value["outcome"] == "idempotent_readback" for kind, value in deletes) == 49
    task_params = (task_id, ids["action"], ids["attempt"], 0, ids["request_hash"], ids["attempt"], {"operation": "pause"}, ids["token"])
    with ThreadPoolExecutor(max_workers=50) as pool:
        tasks = list(pool.map(lambda _: _worker_rpc_outcome(database, "runtime_mutate_scheduled_task", task_params), range(50)))
    assert sum(kind == "ok" and value["outcome"] == "updated" for kind, value in tasks) == 1
    assert sum(kind == "ok" and value["outcome"] == "cas_conflict" for kind, value in tasks) == 49
    stale_kind, stale_value = _worker_rpc_outcome(database, "runtime_mutate_scheduled_task", (*task_params[:3], 0, "bad" * 16, *task_params[5:]))
    assert stale_kind == "error" or stale_value.get("outcome") == "cas_conflict"


def test_ar173_sync_response_loss_and_checkpoint_crash_recovery(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)

    class _SyncProvider:
        def __init__(self) -> None:
            self.submit_calls = 0
            self.recover_calls = 0
            self.progress_calls = 0
            self.raise_submit_response = True

        async def submit_or_get(self, request, *, idempotency_key):
            self.submit_calls += 1
            if self.raise_submit_response:
                self.raise_submit_response = False
                raise TimeoutError("response lost after provider accepted submission")
            return {"state": "accepted", "provider_task_ref": "sync-task-stable"}

        async def recover_submission(self, *, idempotency_key):
            self.recover_calls += 1
            if self.recover_calls == 1:
                return {"outcome": "PROVEN_NOT_SUBMITTED"}
            return {"outcome": "FOUND", "provider_task_ref": "sync-task-stable"}

        async def progress(self, submission):
            self.progress_calls += 1
            return {"state": "ready", "provider_task_ref": submission["provider_task_ref"], "cursor": 7}

    class _Checkpoint:
        def __init__(self) -> None:
            self.apply_calls = 0
            self.checkpoint_calls = 0
            self.fail_checkpoint_once = True

        async def apply(self, value):
            self.apply_calls += 1
            return {"rows": 3, "cursor": value["cursor"]}

        async def checkpoint(self, value):
            self.checkpoint_calls += 1
            if self.fail_checkpoint_once:
                self.fail_checkpoint_once = False
                raise ConnectionError("checkpoint crash")
            return {"cursor": value["cursor"], "materialized": True}

    async def run() -> tuple[_SyncProvider, _Checkpoint, Mapping[str, object], Mapping[str, object]]:
        client = AsyncLocalDBClient(database.replace("postgres@", "everydayai_agent_runtime_worker@"), min_size=1, max_size=4)
        await client.open()
        scoped = AsyncScopedDatabaseClient(client, DatabaseScope(actor_user_id="44444444-4444-4444-4444-444444444444", org_id="22222222-2222-2222-2222-222222222222", access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="ar173-sync-crash"))
        provider = _SyncProvider()
        effects = _Checkpoint()
        attempt = SimpleNamespace(
            action_id=ids["action"], attempt_id=ids["attempt"], request_hash=ids["request_hash"], state_version=0,
            lease=SimpleNamespace(fencing_token=ids["token"], expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)),
        )
        try:
            facts = PostgresSpecialistRepository(scoped)
            service = ErpSyncService(provider=provider, local_apply=effects.apply, checkpoint_store=effects.checkpoint, facts=facts)
            first = await service.run({"domain": "orders", "scope_id": "org-scope", "payload": {"page": 1}}, attempt)
            second = await service.run({"domain": "orders", "scope_id": "org-scope", "payload": {"page": 1}}, attempt)
            return provider, effects, first, second
        finally:
            await client.close()

    import asyncio
    provider, effects, first, second = asyncio.run(run())
    assert provider.submit_calls == 1
    assert provider.recover_calls >= 1
    assert first["state"] == "unknown"
    assert second["state"] == "completed"
    assert effects.apply_calls == 1
    assert effects.checkpoint_calls == 2
    with psycopg.connect(database) as conn:
        phases = {row[0] for row in conn.execute("SELECT phase FROM agent_action_sync_phase_facts WHERE action_id=%s AND attempt_id=%s", (ids["action"], ids["attempt"]))}
        assert {"submitted", "progressing", "applying", "checkpointed", "completed"} <= phases


def test_ar173_completion_cancel_fifty_concurrent_single_terminal_winner(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)
    reconciliation = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_actions SET status='accepted' WHERE id=%s", (ids["action"],))
        conn.execute("UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',last_provider_status='accepted',accepted_at=clock_timestamp(),state_version=1,reconciliation_token=%s,reconciliation_lease_expires_at=clock_timestamp()+interval '10 minutes',external_receipt=%s WHERE id=%s", (reconciliation, Jsonb({"provider": "local", "provider_task_ref": "task-race"}), ids["attempt"]))
        conn.execute("UPDATE agent_runs SET blocking_action_count=1,status='running' WHERE id=%s", (ids["run"],))
        conn.commit()
    complete = (ids["attempt"], None, reconciliation, 1, ids["request_hash"], "completed", {"state": "completed", "provider_task_ref": "task-race"}, {"status": "success", "summary": "ok", "data": {}, "external_receipt": {"provider": "local"}}, "settle", 1, 1, "credits", "runtime", "c" * 64)
    cancel = (ids["attempt"], None, reconciliation, 1, ids["request_hash"], "cancelled", {"state": "cancelled", "provider_task_ref": "task-race", "cancel_confirmed": True}, {"status": "empty", "summary": "cancelled", "data": {}, "external_receipt": {"provider": "local"}}, "refund", 1, 0, "credits", "runtime", "d" * 64)
    requests = [complete] * 25 + [cancel] * 25
    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(lambda params: _worker_rpc_outcome(database, "finalize_agent_action_provider_v2", params), requests))
    winners = [value for kind, value in outcomes if kind == "ok" and value.get("outcome") in {"completed", "cancelled"}]
    assert len(winners) == 1
    with psycopg.connect(database) as conn:
        terminal = conn.execute("SELECT status FROM agent_actions WHERE id=%s", (ids["action"],)).fetchone()[0]
        assert terminal in {"completed", "cancelled"}
        assert conn.execute("SELECT count(*) FROM agent_action_results WHERE action_id=%s", (ids["action"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM agent_action_cost_settlements WHERE action_id=%s AND kind IN ('settle','refund')", (ids["action"],)).fetchone()[0] == 1
        assert conn.execute("SELECT blocking_action_count FROM agent_runs WHERE id=%s", (ids["run"],)).fetchone()[0] == 0


def test_ar173_sync_unknown_never_resubmits_after_mapping_readback(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    ids = _seed_specialist_action(database)

    class _UnknownProvider:
        def __init__(self) -> None:
            self.submit_calls = 0
            self.recover_calls = 0

        async def submit_or_get(self, request, *, idempotency_key):
            self.submit_calls += 1
            raise AssertionError("UNKNOWN submission must never resubmit")

        async def recover_submission(self, *, idempotency_key):
            self.recover_calls += 1
            return {"outcome": "UNKNOWN"}

        async def progress(self, submission):
            raise AssertionError("unknown submission must not query progress")

    async def run() -> tuple[_UnknownProvider, Mapping[str, object]]:
        client = AsyncLocalDBClient(database.replace("postgres@", "everydayai_agent_runtime_worker@"), min_size=1, max_size=2)
        await client.open()
        scoped = AsyncScopedDatabaseClient(client, DatabaseScope(actor_user_id="44444444-4444-4444-4444-444444444444", org_id="22222222-2222-2222-2222-222222222222", access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="ar173-sync-unknown"))
        provider = _UnknownProvider()
        attempt = SimpleNamespace(action_id=ids["action"], attempt_id=ids["attempt"], request_hash=ids["request_hash"], state_version=0, lease=SimpleNamespace(fencing_token=ids["token"], expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        try:
            facts = PostgresSpecialistRepository(scoped)
            request = {"domain": "orders", "scope_id": "org-scope"}
            key = sync_idempotency_key(attempt, request)
            identity = await facts.create_or_get_sync_submission(p_action_id=ids["action"], p_attempt_id=ids["attempt"], p_request_hash=ids["request_hash"], p_scope_id="org-scope", p_sync_domain="orders", p_external_idempotency_key=key, p_provider="erp_sync")
            await facts.record_sync_submission_result(p_submission_id=str(identity["submission_id"]), p_external_idempotency_key=key, p_request_hash=ids["request_hash"], p_provider_task_ref="", p_submission_state="unknown", p_enqueue_checkpoint={"state": "worker_reconcile"})
            result = await ErpSyncService(provider=provider, local_apply=lambda _: {}, checkpoint_store=lambda _: {}, facts=facts).run(request, attempt)
            return provider, result
        finally:
            await client.close()

    import asyncio
    provider, result = asyncio.run(run())
    assert result["state"] == "unknown" and provider.submit_calls == 0 and provider.recover_calls == 1


def test_ar173_sync_progress_and_terminal_crash_recovery(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    progress_ids = _seed_specialist_action(database)

    class _Provider:
        def __init__(self, crash_progress: bool = False) -> None:
            self.submit_calls = 0
            self.recover_calls = 0
            self.progress_calls = 0
            self.crash_progress = crash_progress

        async def submit_or_get(self, request, *, idempotency_key):
            self.submit_calls += 1
            return {"state": "accepted", "provider_task_ref": f"task-{idempotency_key[:8]}"}

        async def recover_submission(self, *, idempotency_key):
            self.recover_calls += 1
            return {"outcome": "PROVEN_NOT_SUBMITTED"}

        async def progress(self, submission):
            self.progress_calls += 1
            if self.crash_progress and self.progress_calls == 1:
                raise ConnectionError("worker crashed before progress fact")
            return {"state": "ready", "cursor": 1, "provider_task_ref": submission["provider_task_ref"]}

    class _Effects:
        def __init__(self) -> None:
            self.apply_calls = 0
            self.checkpoint_calls = 0

        async def apply(self, progress):
            self.apply_calls += 1
            return {"cursor": progress["cursor"], "rows": 1}

        async def checkpoint(self, applied):
            self.checkpoint_calls += 1
            return {"cursor": applied["cursor"], "durable": True}

    async def run() -> tuple[_Provider, _Effects, Mapping[str, object], Mapping[str, object]]:
        client = AsyncLocalDBClient(database.replace("postgres@", "everydayai_agent_runtime_worker@"), min_size=1, max_size=4)
        await client.open()
        scoped = AsyncScopedDatabaseClient(client, DatabaseScope(actor_user_id="44444444-4444-4444-4444-444444444444", org_id="22222222-2222-2222-2222-222222222222", access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="ar173-sync-crash-matrix"))
        try:
            facts = PostgresSpecialistRepository(scoped)

            def attempt(ids):
                return SimpleNamespace(action_id=ids["action"], attempt_id=ids["attempt"], request_hash=ids["request_hash"], state_version=0, lease=SimpleNamespace(fencing_token=ids["token"], expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))

            class _TerminalFailFacts:
                def __init__(self, delegate):
                    self.delegate = delegate
                    self.fail_once = True

                def __getattr__(self, name):
                    return getattr(self.delegate, name)

                async def sync_phase(self, **params):
                    if params.get("p_phase") == "completed" and self.fail_once:
                        self.fail_once = False
                        raise ConnectionError("terminal finalize crash")
                    return await self.delegate.sync_phase(**params)

            progress_provider = _Provider(crash_progress=True)
            progress_effects = _Effects()
            wrapped_facts = _TerminalFailFacts(facts)
            progress_service = ErpSyncService(provider=progress_provider, local_apply=progress_effects.apply, checkpoint_store=progress_effects.checkpoint, facts=wrapped_facts)
            progress_attempt = attempt(progress_ids)
            with pytest.raises(ConnectionError, match="progress fact"):
                await progress_service.run({"domain": "orders", "scope_id": "org-scope"}, progress_attempt)
            with pytest.raises(ConnectionError, match="terminal finalize"):
                await progress_service.run({"domain": "orders", "scope_id": "org-scope"}, progress_attempt)

            terminal_service = ErpSyncService(provider=progress_provider, local_apply=progress_effects.apply, checkpoint_store=progress_effects.checkpoint, facts=wrapped_facts)
            terminal_result = await terminal_service.run({"domain": "orders", "scope_id": "org-scope"}, progress_attempt)
            return progress_provider, progress_effects, terminal_result, terminal_result
        finally:
            await client.close()

    import asyncio
    progress_provider, progress_effects, progress_result, terminal_result = asyncio.run(run())
    assert progress_result["state"] == "completed" and terminal_result["state"] == "completed"
    assert progress_provider.submit_calls == 1 and progress_effects.apply_calls == 1 and progress_effects.checkpoint_calls == 1
