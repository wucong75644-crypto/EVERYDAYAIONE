from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from core.workspace import build_wecom_channel_workspace_owner
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b3_provider_cancel_postgres_external import (
    _prepare as _prepare_runtime,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_56_agent_runtime_resource_manifest_facade.sql"
ROLLBACK = ROOT / "migrations/rollback/227_56_agent_runtime_resource_manifest_facade_rollback.sql"
ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"
OTHER_USER = "11111111-1111-1111-1111-111111111111"
CONVERSATION = "55555555-5555-5555-5555-555555555555"
REQUEST_HASH = "a" * 64


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection, connection.transaction():
        connection.execute(path.read_text(encoding="utf-8"))


def _prepare(url: str) -> None:
    _prepare_runtime(url)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
        ALTER TABLE conversations ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'web';
        CREATE TABLE IF NOT EXISTS conversation_attachment_refs(
          id UUID PRIMARY KEY,org_id UUID NOT NULL,conversation_id UUID NOT NULL,
          attachment_set_id UUID NOT NULL,canonical_name TEXT NOT NULL,
          workspace_path TEXT NOT NULL,detected_mime_type TEXT NOT NULL,
          size BIGINT,status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_attachment_refs(
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),org_id UUID NOT NULL,
          task_id UUID NOT NULL,turn_id UUID NOT NULL,input_message_id UUID NOT NULL,
          attachment_id UUID NOT NULL,attachment_set_id UUID NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS user_assets(
          id UUID PRIMARY KEY,org_id UUID,storage_scope TEXT NOT NULL,
          storage_owner_key TEXT NOT NULL,storage_provider TEXT NOT NULL,
          media_type TEXT NOT NULL,status TEXT NOT NULL,workspace_path TEXT,
          name TEXT NOT NULL,mime_type TEXT,size BIGINT
        );
        """)
        connection.commit()
    _apply(url, MIGRATION)


def _run_context(ids: dict[str, str]) -> tuple[dict, str]:
    context_receipt = {
        "base_context_revision": f"message:{ids['message']}",
        "through_message_id": ids["message"],
        "session_id": ids["session"], "conversation_id": CONVERSATION,
    }
    payload = json.dumps({
        "task_id": ids["task"], "input_message_id": ids["message"],
        "turn_id": ids["turn"],
        "run_envelope": {
            "context_receipt": context_receipt,
            "request_identity": {
                "session_id": ids["session"], "conversation_id": CONVERSATION,
                "user_id": USER, "org_id": ORG, "scope_kind": "user",
                "scope_id": USER, "through_message_id": ids["message"],
            },
        },
    })
    return context_receipt, payload


def _seed(url: str) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in (
        "session", "command", "run", "step", "action", "attempt",
        "token", "policy", "intent", "message", "task", "turn", "web_asset",
    )}
    context_receipt, payload = _run_context(ids)
    content = json.dumps([{
        "type": "file", "asset_id": ids["web_asset"], "name": "销售.csv",
        "workspace_path": "上传/2026-08/销售.csv", "mime_type": "text/csv",
        "size": 12,
    }], ensure_ascii=False)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content,turn_id) "
            "VALUES(%s,%s,%s,'user',%s,%s)",
            (ids["message"], CONVERSATION, ORG, content, ids["turn"]),
        )
        connection.execute(
            "INSERT INTO user_assets(id,org_id,storage_scope,storage_owner_key,"
            "storage_provider,media_type,status,workspace_path,name,mime_type,size) "
            "VALUES(%s,%s,'user',%s,'workspace','file','ready',%s,%s,'text/csv',12)",
            (ids["web_asset"], ORG, USER, "上传/2026-08/销售.csv", "销售.csv"),
        )
        connection.execute(
            "INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,"
            "input_message_id,turn_id,model_id,delivery_context) "
            "VALUES(%s,%s,%s,%s,'chat','pending',%s,%s,'qwen',"
            "'{\"actor\":false,\"runtime\":true,\"channel\":\"web\"}')",
            (ids["task"], USER, ORG, CONVERSATION, ids["message"], ids["turn"]),
        )
        connection.execute(
            "INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,"
            "scope_kind,scope_id,created_by_user_id,agent_definition_id,"
            "agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')",
            (ids["session"], CONVERSATION, ORG, USER, USER, USER),
        )
        connection.execute(
            "INSERT INTO agent_session_commands(id,session_id,org_id,user_id,"
            "command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,%s::jsonb,%s)",
            (ids["command"], ids["session"], ORG, USER, ids["command"], payload,
             "b" * 32),
        )
        connection.execute(
            "INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,"
            "status,idempotency_key,request_hash,execution_token,lease_expires_at,"
            "context_receipt) "
            "VALUES(%s,%s,%s,%s,%s,'user','running',%s,%s,%s,"
            "clock_timestamp()+interval '10 minutes',%s::jsonb)",
            (ids["run"], ids["session"], ids["command"], ORG, USER,
             ids["run"], "c" * 32, ids["token"], json.dumps(context_receipt)),
        )
        connection.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,"
            "step_number,model_id,provider,model_revision,prompt_revision,"
            "tool_catalog_revision) VALUES(%s,%s,%s,%s,%s,1,'fixture','dashscope',"
            "'v1','v1','v1')",
            (ids["step"], ids["run"], ids["session"], ORG, USER),
        )
        policy = '{"capability":"artifact.materialize"}'
        connection.execute(
            "INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,"
            "user_id,action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,"
            "request_hash,batch_hash,policy_decision,policy_snapshot,policy_revision,"
            "retry_disposition,status) VALUES(%s,%s,%s,%s,%s,%s,0,%s,"
            "'file_analyze','{\"file_id\":\"asset-web-1\"}',%s,%s,%s,"
            "'requires_authorization',%s::jsonb,'v1','retry_safe','running')",
            (ids["action"], ids["session"], ids["run"], ids["step"], ORG, USER,
             ids["action"], "d" * 64, REQUEST_HASH, "e" * 64, policy),
        )
        connection.execute(
            "INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,"
            "user_id,attempt_number,status,dispatch_phase,worker_id,execution_token,"
            "lease_expires_at,idempotency_key,request_hash,retry_disposition) "
            "VALUES(%s,%s,%s,%s,%s,%s,1,'dispatching','request_started','runtime-worker',"
            "%s,clock_timestamp()+interval '10 minutes',%s,%s,'retry_safe')",
            (ids["attempt"], ids["action"], ids["session"], ids["run"], ORG, USER,
             ids["token"], ids["attempt"], REQUEST_HASH),
        )
        connection.execute(
            "INSERT INTO agent_policy_receipts(id,action_id,session_id,run_id,org_id,"
            "user_id,decision,arguments_hash,executor_type,executor_revision,"
            "policy_revision,effective_scope,reason_codes,receipt_hash,expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,'allow',%s,'runtime_artifact_job:file_analyze',"
            "1,'v1','{}',ARRAY['fixture'],%s,clock_timestamp()+interval '10 minutes')",
            (ids["policy"], ids["action"], ids["session"], ids["run"], ORG, USER,
             "d" * 64, "f" * 64),
        )
        connection.execute(
            "INSERT INTO agent_action_dispatch_intents(id,attempt_id,action_id,"
            "policy_receipt_id,execution_token,request_hash,executor_type,"
            "executor_revision,policy_revision,external_idempotency_key,recovery_mode) "
            "VALUES(%s,%s,%s,%s,%s,%s,'runtime_artifact_job:file_analyze',1,'v1',%s,"
            "'idempotent_replay')",
            (ids["intent"], ids["attempt"], ids["action"], ids["policy"],
             ids["token"], REQUEST_HASH, ids["attempt"]),
        )
        connection.execute(
            "INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,"
            "execution_token,tenant_kill_epoch,provider_kill_epoch,"
            "capability_kill_epoch,state_version,lease_expires_at,status) "
            "VALUES('attempt',%s,%s,%s,0,0,0,0,"
            "clock_timestamp()+interval '10 minutes','active')",
            (ids["attempt"], ORG, ids["token"]),
        )
        connection.commit()
    return ids


def _worker_call(
    url: str, ids: dict[str, str], *, token: str | None = None,
    version: int = 0, request_hash: str = REQUEST_HASH,
):
    worker_url = url.replace("postgres@", "everydayai_agent_runtime_worker@")
    with psycopg.connect(worker_url) as connection:
        connection.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        return connection.execute(
            "SELECT get_agent_runtime_resource_manifest_v1(%s,%s,%s,%s,%s)",
            (ids["attempt"], "runtime-worker", token or ids["token"], version,
             request_hash),
        ).fetchone()[0]


def _assert_fences_and_anchors(url: str, ids: dict[str, str]) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids, token=str(uuid4()))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids, version=1)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids, request_hash="9" * 64)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE user_assets SET org_id=%s WHERE id=%s",
            (str(uuid4()), ids["web_asset"]),
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE",
    ):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE user_assets SET org_id=%s WHERE id=%s",
            (ORG, ids["web_asset"]),
        )
        connection.execute(
            "UPDATE agent_action_dispatch_intents SET policy_revision='wrong' "
            "WHERE id=%s", (ids["intent"],),
        )
        connection.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_action_dispatch_intents SET policy_revision='v1' "
            "WHERE id=%s", (ids["intent"],),
        )
        connection.commit()
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls(org_id,gate_scope,"
            "scope_key,dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'capability','artifact.materialize',TRUE,1,1,'test',%s)",
            (ORG, USER),
        )
        connection.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "DELETE FROM agent_runtime_tenant_gate_controls WHERE org_id=%s "
            "AND gate_scope='capability' AND scope_key='artifact.materialize'",
            (ORG,),
        )
        connection.execute(
            "UPDATE agent_policy_receipts SET evaluated_at=clock_timestamp()-"
            "interval '2 seconds',expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s", (ids["policy"],),
        )
        connection.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_policy_receipts SET evaluated_at=clock_timestamp(),"
            "expires_at=clock_timestamp()+interval '10 minutes' WHERE id=%s",
            (ids["policy"],),
        )
        connection.execute(
            "UPDATE agent_runs SET context_receipt=jsonb_set(context_receipt,"
            "'{through_message_id}',to_jsonb(%s::text)) WHERE id=%s",
            (str(uuid4()), ids["run"]),
        )
        connection.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runs SET context_receipt=jsonb_set(context_receipt,"
            "'{through_message_id}',to_jsonb(%s::text)) WHERE id=%s",
            (ids["message"], ids["run"]),
        )
        connection.execute(
            "UPDATE messages SET content=%s WHERE id=%s",
            (json.dumps([{
                "type": "file", "asset_id": ids["web_asset"],
                "workspace_path": "../越权.csv",
            }]), ids["message"]),
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE",
    ):
        _worker_call(url, ids)


def _convert_to_wecom_channel(url: str, ids: dict[str, str]) -> tuple[str, str]:
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        attachment_id, set_id = str(uuid4()), str(uuid4())
        connection.execute(
            "UPDATE conversations SET user_id=NULL,scope_type='channel',"
            "scope_id='group-1',source='wecom' WHERE id=%s", (CONVERSATION,),
        )
        connection.execute(
            "UPDATE agent_runtime_sessions SET user_id=NULL,scope_kind='channel',"
            "scope_id='group-1' "
            "WHERE id=%s", (ids["session"],),
        )
        connection.execute(
            "UPDATE agent_session_commands SET user_id=NULL,payload=jsonb_set("
            "jsonb_set(jsonb_set(payload,"
            "'{run_envelope,request_identity,scope_kind}','\"channel\"'::jsonb),"
            "'{run_envelope,request_identity,scope_id}','\"group-1\"'::jsonb),"
            "'{run_envelope,request_identity,user_id}','null'::jsonb) "
            "WHERE id=%s", (ids["command"],),
        )
        for table, row_id in (
            ("agent_runs", ids["run"]),
            ("agent_model_steps", ids["step"]),
            ("agent_actions", ids["action"]),
            ("agent_action_attempts", ids["attempt"]),
            ("agent_policy_receipts", ids["policy"]),
        ):
            connection.execute(
                f"UPDATE {table} SET user_id=NULL WHERE id=%s", (row_id,),
            )
        connection.execute(
            "UPDATE tasks SET delivery_context=%s::jsonb WHERE id=%s",
            (json.dumps({
                "actor": False, "runtime": True, "channel": "wecom",
                "chattype": "group", "corp_id": "corp-1", "chatid": "group-1",
            }), ids["task"]),
        )
        connection.execute(
            "INSERT INTO conversation_attachment_refs(id,org_id,conversation_id,"
            "attachment_set_id,canonical_name,workspace_path,detected_mime_type,size,"
            "status) VALUES(%s,%s,%s,%s,'企微销售.csv','上传/企微/销售.csv',"
            "'text/csv',12,'ready')",
            (attachment_id, ORG, CONVERSATION, set_id),
        )
        connection.execute(
            "INSERT INTO task_attachment_refs(org_id,task_id,turn_id,input_message_id,"
            "attachment_id,attachment_set_id) VALUES(%s,%s,%s,%s,%s,%s)",
            (ORG, ids["task"], ids["turn"], ids["message"], attachment_id, set_id),
        )
        connection.commit()
    return attachment_id, set_id


def _assert_channel_and_acl(
    url: str, ids: dict[str, str], attachment_id: str, set_id: str,
) -> None:
    channel_manifest = _worker_call(url, ids)
    assert channel_manifest["manifest_source"] == "task_attachment_refs"
    assert channel_manifest["workspace_owner_id"] == (
        build_wecom_channel_workspace_owner("corp-1", "group-1")
    )
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_sessions SET created_by_user_id=%s WHERE id=%s",
            (OTHER_USER, ids["session"]),
        )
        connection.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_sessions SET created_by_user_id=%s WHERE id=%s",
            (USER, ids["session"]),
        )
        connection.execute(
            "DELETE FROM task_attachment_refs WHERE task_id=%s",
            (ids["task"],),
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE",
    ):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO task_attachment_refs(org_id,task_id,turn_id,input_message_id,"
            "attachment_id,attachment_set_id) VALUES(%s,%s,%s,%s,%s,%s)",
            (ORG, ids["task"], ids["turn"], ids["message"], attachment_id, set_id),
        )
        connection.commit()
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE conversation_attachment_refs SET status='failed' WHERE id=%s",
            (attachment_id,),
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE",
    ):
        _worker_call(url, ids)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE conversation_attachment_refs SET status='ready' WHERE id=%s",
            (attachment_id,),
        )
        connection.commit()
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'task_attachment_refs','SELECT')",
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'get_agent_runtime_resource_manifest_v1(uuid,text,uuid,bigint,text)',"
            "'EXECUTE')",
        ).fetchone()[0] is False
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker_url = url.replace("postgres@", "everydayai_agent_runtime_worker@")
        with psycopg.connect(worker_url) as connection:
            connection.execute("SELECT * FROM task_attachment_refs").fetchall()


def test_resource_manifest_apply_readback_acl_rollback_reapply(database: str) -> None:
    _prepare(database)
    ids = _seed(database)
    manifest = _worker_call(database, ids)
    assert manifest["workspace_scope"] == "user"
    assert manifest["workspace_owner_id"] == USER
    assert manifest["manifest_source"] == "input_message"
    assert manifest["assets"][0]["workspace_path"] == "上传/2026-08/销售.csv"
    _assert_fences_and_anchors(database, ids)
    attachment_id, set_id = _convert_to_wecom_channel(database, ids)
    _assert_channel_and_acl(database, ids, attachment_id, set_id)
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure('get_agent_runtime_resource_manifest_v1"
            "(uuid,text,uuid,bigint,text)')",
        ).fetchone()[0] is None
    _apply(database, MIGRATION)
    assert _worker_call(database, ids)["assets"][0]["asset_id"] == attachment_id
    _apply(database, ROLLBACK)
