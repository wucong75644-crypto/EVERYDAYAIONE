from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.agent.runtime.catalog.safe_read_release import (
    build_safe_read_catalog,
    build_safe_read_snapshot,
)
from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"
CONVERSATION = "55555555-5555-5555-5555-555555555555"
STACK = (
    "227_01_agent_runtime_production_closure.sql",
    "227_06_agent_runtime_tenant_kill_control.sql",
    "227_07_agent_runtime_kill_epoch_fence.sql",
    "227_09_agent_runtime_claim_fence_ambiguity_fix.sql",
    "230_02_agent_runtime_catalog_safe_read_v9.sql",
    "227_17_agent_runtime_safe_policy_activation.sql",
)


def _apply(url: str, name: str) -> None:
    path = ROOT / "migrations" / name
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _rollback(url: str, name: str) -> None:
    path = ROOT / "migrations/rollback" / name
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _worker_call(
    url: str, name: str, params: tuple[object, ...],
    *, role: str = "everydayai_agent_runtime_worker",
) -> dict[str, object]:
    worker_url = url.replace("postgres@", f"{role}@")
    kind = "authorization" if role == "everydayai_authorization_worker" else "agent_runtime"
    with psycopg.connect(worker_url) as connection:
        connection.execute("SELECT set_config('app.access_kind',%s,false)", (kind,))
        connection.execute("SELECT set_config('app.actor_user_id',%s,false)", (USER,))
        connection.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
        placeholders = ",".join(["%s"] * len(params))
        row = connection.execute(
            f"SELECT {name}({placeholders})", params,
        ).fetchone()[0]
        connection.commit()
        return row


def _seed_safe_action(url: str, *, tool_name: str = "search_knowledge") -> dict[str, object]:
    ids = {
        name: str(uuid4()) for name in (
            "session", "command", "run", "step", "action",
        )
    }
    release = build_safe_read_snapshot(
        scope="channel", channel="web", gate_state="disabled",
    )
    tool = next(
        item for item in build_safe_read_catalog().definitions()
        if item.canonical_name == tool_name
    )
    request_hash = "a" * 64
    policy_snapshot = {
        "source": "runtime_executor_registry",
        "safety_level": tool.safety_level,
        "side_effect": tool.side_effect,
        "authorization_requirement": tool.authorization_requirement,
        "capability_requirements": sorted(tool.capability_requirements),
        "capability_revision": tool.schema_hash,
        "catalog_revision": release.catalog_document["catalog_revision"],
        "effective_toolset_hash": release.toolset_hash,
        "schema_hash": tool.schema_hash,
        "executor_revision": tool.executor_revision,
    }
    capability_snapshot = {
        "channel": "web",
        "effective_toolset_revision": release.catalog_document["catalog_revision"],
        "effective_toolset_hash": release.toolset_hash,
    }
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_sessions("
            "id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,"
            "agent_definition_id,agent_definition_revision) "
            "VALUES(%s,%s,%s,NULL,'channel',%s,%s,'everydayai-default','v9')",
            (ids["session"], CONVERSATION, ORG, f"channel:{ids['session']}", USER),
        )
        connection.execute(
            "INSERT INTO agent_session_commands("
            "id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)",
            (ids["command"], ids["session"], ORG, USER, ids["command"], "b" * 32),
        )
        connection.execute(
            "INSERT INTO agent_runs("
            "id,session_id,command_id,org_id,user_id,run_kind,status,idempotency_key,"
            "request_hash,context_receipt,config_snapshot,capability_snapshot,blocking_action_count) "
            "VALUES(%s,%s,%s,%s,%s,'user','waiting_actions',%s,%s,'{}','{}',%s,1)",
            (ids["run"], ids["session"], ids["command"], ORG, USER,
             ids["run"], "c" * 32, Jsonb(capability_snapshot)),
        )
        connection.execute(
            "INSERT INTO agent_model_steps("
            "id,run_id,session_id,org_id,user_id,step_number,status,model_id,provider,"
            "model_revision,prompt_revision,tool_catalog_revision,stop_reason,completed_at) "
            "VALUES(%s,%s,%s,%s,%s,1,'completed','fixture','fixture','v1',"
            "'agent-runtime-safe-read-v1',%s,'tool_calls',clock_timestamp())",
            (ids["step"], ids["run"], ids["session"], ORG, USER,
             release.catalog_document["catalog_revision"]),
        )
        connection.execute(
            "INSERT INTO agent_actions("
            "id,session_id,run_id,model_step_id,org_id,user_id,action_index,"
            "stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,batch_hash,"
            "policy_decision,policy_snapshot,policy_revision,retry_disposition,status) "
            "VALUES(%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,'preauthorized',%s,"
            "'agent-runtime-policy-v1','retry_safe','queued')",
            (ids["action"], ids["session"], ids["run"], ids["step"], ORG, USER,
             ids["action"], tool_name, Jsonb({"query": "stock"}), "d" * 64,
             request_hash, "e" * 64, Jsonb(policy_snapshot)),
        )
        connection.commit()
    claim = _worker_call(
        url, "claim_ready_agent_action_snapshots_v2",
        ("safe-worker", f"claim-{ids['action']}", 1, 120),
    )
    ids["snapshot"] = claim["snapshots"][0]
    return ids


def _activation_params(snapshot: dict[str, object]) -> tuple[object, ...]:
    return (
        snapshot["id"], snapshot["execution_token"], snapshot["state_version"],
        snapshot["request_hash"], "runtime_read:search_knowledge", 1,
        "agent-runtime-policy-v1",
    )


def _assert_activation_acl(connection: psycopg.Connection[object]) -> None:
    assert connection.execute(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
        "WHERE relname='agent_safe_action_activations'",
    ).fetchone() == (True, True)
    signature = "activate_agent_safe_action(uuid,uuid,bigint,text,text,integer,text)"
    for role in ("everydayai_agent_runtime_worker", "everydayai_authorization_worker"):
        assert connection.execute(
            "SELECT has_table_privilege(%s,'agent_safe_action_activations','SELECT')",
            (role,),
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, signature),
        ).fetchone()[0] is True
    assert connection.execute(
        "SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE')", (signature,),
    ).fetchone()[0] is False
    assert connection.execute(
        "SELECT COALESCE(bool_or(acl.grantee=0 AND acl.privilege_type='EXECUTE'),FALSE) "
        "FROM pg_proc proc LEFT JOIN LATERAL aclexplode(proc.proacl) acl ON TRUE "
        "WHERE proc.oid=to_regprocedure(%s)", (signature,),
    ).fetchone()[0] is False


def test_safe_policy_activation_dispatch_acl_and_rollback(database: str) -> None:
    for name in STACK:
        _apply(database, name)
    ids = _seed_safe_action(database)
    snapshot = ids["snapshot"]
    params = _activation_params(snapshot)
    unsafe = list(params)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET policy_snapshot="
            "jsonb_set(policy_snapshot,'{safety_level}','\"dangerous\"') "
            "WHERE id=%s", (ids["action"],),
        )
        connection.commit()
    assert _worker_call(
        database, "activate_agent_safe_action", tuple(unsafe),
    )["outcome"] == "safe_policy_required"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET policy_snapshot="
            "jsonb_set(policy_snapshot,'{safety_level}','\"safe\"') "
            "WHERE id=%s", (ids["action"],),
        )
        connection.commit()
    wrong_token = list(params)
    wrong_token[1] = str(uuid4())
    assert _worker_call(
        database, "activate_agent_safe_action", tuple(wrong_token),
    )["outcome"] == "ownership_lost"
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(
            lambda _: _worker_call(database, "activate_agent_safe_action", params),
            range(12),
        ))
    assert sum(item["outcome"] == "activated" for item in results) == 1
    assert sum(item["outcome"] == "already_activated" for item in results) == 11
    receipt_id = results[0]["policy_receipt_id"]
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT action_dispatch_enabled,safe_actions_enabled "
            "FROM agent_runtime_control WHERE singleton",
        ).fetchone() == (False, False)
        connection.execute(
            "UPDATE agent_runtime_control SET "
            "action_dispatch_enabled=TRUE,safe_actions_enabled=TRUE",
        )
        connection.execute(
            "INSERT INTO agent_runtime_org_rollout("
            "org_id,enabled,updated_by,update_reason) VALUES(%s,TRUE,%s,%s) "
            "ON CONFLICT(org_id) DO UPDATE SET enabled=EXCLUDED.enabled",
            (ORG, USER, "c7-b32a disposable gate"),
        )
        connection.commit()
    gate = _worker_call(database, "gate_agent_action_dispatch_v2", (
        snapshot["id"], snapshot["execution_token"], snapshot["state_version"],
        snapshot["request_hash"], receipt_id, "runtime_read:search_knowledge", 1,
        "agent-runtime-policy-v1", "idempotent_replay",
    ))
    assert gate["outcome"] == "dispatch_authorized"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls("
            "org_id,gate_scope,scope_key,dispatch_blocked,kill_epoch,state_version,"
            "reason,updated_by) VALUES(%s,'capability','knowledge.search',TRUE,1,1,%s,%s)",
            (ORG, "disposable capability kill", USER),
        )
        connection.commit()
    fenced = _worker_call(database, "gate_agent_action_dispatch_v2", (
        snapshot["id"], snapshot["execution_token"], gate["state_version"],
        snapshot["request_hash"], receipt_id, "runtime_read:search_knowledge", 1,
        "agent-runtime-policy-v1", "idempotent_replay",
    ))
    assert fenced["error_code"] == "RUNTIME_CAPABILITY_KILL_FENCED"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_control SET "
            "action_dispatch_enabled=FALSE,safe_actions_enabled=FALSE",
        )
        _assert_activation_acl(connection)
        connection.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        _rollback(database, "227_17_agent_runtime_safe_policy_activation_rollback.sql")
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "TRUNCATE agent_safe_action_activations,agent_action_dispatch_intents,"
            "agent_policy_receipts,agent_action_attempts,agent_actions,agent_model_steps,"
            "agent_runs,agent_session_commands,agent_runtime_sessions CASCADE",
        )
        connection.commit()
    _rollback(database, "227_17_agent_runtime_safe_policy_activation_rollback.sql")
    _rollback(database, "230_02_agent_runtime_catalog_safe_read_v9_rollback.sql")
    _apply(database, "230_02_agent_runtime_catalog_safe_read_v9.sql")
    _apply(database, "227_17_agent_runtime_safe_policy_activation.sql")
    _rollback(database, "227_17_agent_runtime_safe_policy_activation_rollback.sql")
    _rollback(database, "230_02_agent_runtime_catalog_safe_read_v9_rollback.sql")
