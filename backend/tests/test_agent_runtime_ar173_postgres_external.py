"""AR-17.3 migration contract on the disposable local PostgreSQL fixture."""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


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
    migrations = [f"226_{index:02d}_" for index in range(1, 12)]
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
    for name in rollbacks:
        _rollback(database, name)
    for name in names:
        _apply(database, name)
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT to_regclass('agent_action_cost_settlements')").fetchone()[0] == "agent_action_cost_settlements"


def _seed_specialist_action(database: str) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in ("session", "command", "run", "step", "action", "attempt", "token", "policy")}
    request_hash = "a" * 64
    run_hash = "b" * 32
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,agent_definition_id,agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')", (ids["session"], "55555555-5555-5555-5555-555555555555", "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", "44444444-4444-4444-4444-444444444444", "44444444-4444-4444-4444-444444444444"))
        conn.execute("INSERT INTO agent_session_commands(id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)", (ids["command"], ids["session"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["command"], run_hash))
        conn.execute("INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,idempotency_key,request_hash,status,execution_token,lease_expires_at,context_receipt,config_snapshot,capability_snapshot) VALUES(%s,%s,%s,%s,%s,'user',%s,%s,'running',%s,clock_timestamp()+interval '10 minutes','{}','{}','{}')", (ids["run"], ids["session"], ids["command"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["run"], run_hash, ids["token"]))
        conn.execute("INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,model_id,provider,model_revision,prompt_revision,tool_catalog_revision) VALUES(%s,%s,%s,%s,%s,1,'fixture','fixture','v1','v1','v1')", (ids["step"], ids["run"], ids["session"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444"))
        conn.execute("INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,batch_hash,policy_decision,policy_snapshot,policy_revision,retry_disposition,status) VALUES(%s,%s,%s,%s,%s,%s,0,%s,'generate_image','{}',%s,%s,%s,'preauthorized','{}','v1','retry_after_reconcile','running')", (ids["action"], ids["session"], ids["run"], ids["step"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["action"], "b" * 64, request_hash, "c" * 64))
        conn.execute("INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,user_id,attempt_number,status,dispatch_phase,worker_id,execution_token,lease_expires_at,idempotency_key,request_hash,retry_disposition) VALUES(%s,%s,%s,%s,%s,%s,1,'dispatching','request_started','fixture-worker',%s,clock_timestamp()+interval '10 minutes',%s,%s,'retry_after_reconcile')", (ids["attempt"], ids["action"], ids["session"], ids["run"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", ids["token"], ids["attempt"], request_hash))
        conn.execute("INSERT INTO agent_policy_receipts(id,action_id,session_id,run_id,org_id,user_id,decision,arguments_hash,executor_type,executor_revision,policy_revision,effective_scope,reason_codes,receipt_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,'allow',%s,'runtime_media_generation:generate_image',1,'v1','{}',ARRAY['fixture'],%s,clock_timestamp()+interval '10 minutes')", (ids["policy"], ids["action"], ids["session"], ids["run"], "22222222-2222-2222-2222-222222222222", "44444444-4444-4444-4444-444444444444", "b" * 64, "d" * 64))
        conn.execute("INSERT INTO agent_action_dispatch_intents(attempt_id,action_id,policy_receipt_id,execution_token,request_hash,executor_type,executor_revision,policy_revision,external_idempotency_key,recovery_mode) VALUES(%s,%s,%s,%s,%s,'runtime_media_generation:generate_image',1,'v1',%s,'idempotent_replay')", (ids["attempt"], ids["action"], ids["policy"], ids["token"], request_hash, ids["attempt"]))
        conn.execute("UPDATE agent_runs SET blocking_action_count=1 WHERE id=%s", (ids["run"],))
        ids["artifact"] = str(uuid4())
        conn.execute("INSERT INTO conversation_artifacts(id,conversation_id,org_id) VALUES(%s,%s,%s)", (ids["artifact"], "55555555-5555-5555-5555-555555555555", "22222222-2222-2222-2222-222222222222"))
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


def test_ar173_worker_rpc_behavior_matrix_and_50_concurrent_idempotency(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 12):
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
    completed_child = _worker_rpc(database, "complete_agent_child_run_strict", (child["child_run_id"], ids["run"], ids["action"], ids["request_hash"], 1, {"items": []}))
    assert completed_child["outcome"] == "completed"
    for phase in ("submitted", "progressing", "applying", "checkpointed"):
        phase_result = _worker_rpc(database, "record_agent_sync_phase", (ids["action"], ids["attempt"], ids["token"], ids["request_hash"], phase, {"phase": phase}, {"provider": "erp"}))
        assert phase_result["outcome"] == "recorded"
    reconciliation_token = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_action_attempts SET reconciliation_token=%s,reconciliation_lease_expires_at=clock_timestamp()+interval '10 minutes' WHERE id=%s", (reconciliation_token, ids["attempt"]))
        conn.commit()
    _worker_rpc(database, "record_agent_action_cost_strict", (ids["action"], ids["attempt"], "settle", 3, 9, "credits", "runtime", "f" * 64))
    with pytest.raises(Exception):
        _worker_rpc(database, "finalize_agent_action_provider", (ids["attempt"], None, reconciliation_token, ids["request_hash"], "completed", {"state": "completed", "provider_task_ref": "task-1"}, {"status": "success", "summary": "ok", "data": {}, "external_receipt": {"provider": "kie"}}, "settle", 3, 1, "credits", "runtime", "f" * 64))
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT status FROM agent_actions WHERE id=%s", (ids["action"],)).fetchone()[0] == "accepted"
    finalized = _worker_rpc(database, "finalize_agent_action_provider", (ids["attempt"], None, reconciliation_token, ids["request_hash"], "completed", {"state": "completed", "provider_task_ref": "task-1"}, {"status": "success", "summary": "ok", "data": {}, "external_receipt": {"provider": "kie"}}, "settle", 3, 9, "credits", "runtime", "f" * 64))
    assert finalized["outcome"] == "completed"
    duplicate_settle = _worker_rpc(database, "record_agent_action_cost_strict", (ids["action"], ids["attempt"], "settle", 3, 9, "credits", "runtime", "f" * 64))
    assert duplicate_settle["outcome"] == "idempotent_readback"
    fenced = _worker_rpc(database, "record_agent_action_provider_terminal", (ids["attempt"], str(uuid4()), ids["request_hash"], "completed", {}, {}))
    assert fenced["outcome"] == "fenced"
