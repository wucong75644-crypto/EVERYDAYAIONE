from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b4_model_cancel_postgres_external import (
    _prepare as _prepare_gateway_history,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_53_agent_runtime_model_configuration_facade.sql"
ROLLBACK = ROOT / "migrations/rollback/227_53_agent_runtime_model_configuration_facade_rollback.sql"
ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"
CONVERSATION = "55555555-5555-5555-5555-555555555555"
CONFIG_SIGNATURE = (
    "get_agent_runtime_model_configuration_v1"
    "(uuid,uuid,text,uuid,bigint,text,text)"
)
DISPATCH_SIGNATURE = "start_model_attempt_dispatch_v2(uuid,uuid,bigint,text)"
HISTORICAL_GATEWAY_SIGNATURE = (
    "claim_agent_runtime_model_gateway_operation_v2"
    "(uuid,text,text,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,text,text,"
    "text,bigint,bigint,bigint,integer)"
)


def _apply(database_url: str, path: Path) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _prepare(database_url: str) -> None:
    _prepare_gateway_history(database_url)
    _apply(database_url, MIGRATION)


def _seed_model_attempt(database_url: str) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in (
        "session", "command", "run", "token", "step", "attempt",
    )}
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_sessions("
            "id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,"
            "agent_definition_id,agent_definition_revision) "
            "VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')",
            (ids["session"], CONVERSATION, ORG, USER, USER, USER),
        )
        connection.execute(
            "INSERT INTO agent_session_commands("
            "id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)",
            (ids["command"], ids["session"], ORG, USER, ids["command"], "a" * 32),
        )
        connection.execute(
            "INSERT INTO agent_runs("
            "id,session_id,command_id,org_id,user_id,run_kind,status,idempotency_key,"
            "request_hash,execution_token,lease_expires_at) "
            "VALUES(%s,%s,%s,%s,%s,'user','running',%s,%s,%s,"
            "clock_timestamp()+interval '10 minutes')",
            (ids["run"], ids["session"], ids["command"], ORG, USER,
             ids["run"], "b" * 32, ids["token"]),
        )
        connection.execute(
            "INSERT INTO agent_run_attempts("
            "run_id,org_id,user_id,attempt_number,execution_token,worker_id,lease_expires_at) "
            "VALUES(%s,%s,%s,1,%s,'runtime-worker',clock_timestamp()+interval '10 minutes')",
            (ids["run"], ORG, USER, ids["token"]),
        )
        connection.execute(
            "INSERT INTO agent_model_steps("
            "id,run_id,session_id,org_id,user_id,step_number,model_id,provider,"
            "model_revision,prompt_revision,tool_catalog_revision) "
            "VALUES(%s,%s,%s,%s,%s,1,'qwen3.5-plus','dashscope',"
            "'model-v1','prompt-v1','tools-v1')",
            (ids["step"], ids["run"], ids["session"], ORG, USER),
        )
        connection.execute(
            "INSERT INTO agent_model_attempts("
            "id,model_step_id,run_id,session_id,org_id,user_id,attempt_number,"
            "request_hash,idempotency_key,provider,request_receipt,worker_id,"
            "execution_token,lease_expires_at) VALUES("
            "%s,%s,%s,%s,%s,%s,1,%s,%s,'dashscope',%s::jsonb,'runtime-worker',"
            "%s,clock_timestamp()+interval '10 minutes')",
            (ids["attempt"], ids["step"], ids["run"], ids["session"], ORG, USER,
             "c" * 64, ids["attempt"],
             '{"credential_provider":"dashscope","credential_revision":"model-v1",'
             '"credential_purpose":"model.invoke"}', ids["token"]),
        )
        connection.commit()
    return ids


def _worker_url(database_url: str) -> str:
    return database_url.replace("postgres@", "everydayai_agent_runtime_worker@")


def _dispatch(database_url: str, ids: dict[str, str]) -> dict[str, object]:
    with psycopg.connect(_worker_url(database_url)) as connection:
        connection.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        return connection.execute(
            "SELECT start_model_attempt_dispatch_v2(%s,%s,%s,%s)",
            (ids["attempt"], ids["token"], 0, "c" * 64),
        ).fetchone()[0]


def _configuration(
    database_url: str, ids: dict[str, str], *, worker: str = "runtime-worker",
) -> dict[str, object]:
    with psycopg.connect(_worker_url(database_url)) as connection:
        connection.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        return connection.execute(
            "SELECT get_agent_runtime_model_configuration_v1(%s,%s,%s,%s,%s,%s,%s)",
            (ids["run"], ids["attempt"], worker, ids["token"], 1,
             "c" * 64, "ai.provider.dashscope"),
        ).fetchone()[0]


def _assert_acl(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        for signature in (CONFIG_SIGNATURE, DISPATCH_SIGNATURE):
            assert connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                ("everydayai_agent_runtime_worker", signature),
            ).fetchone()[0] is True
            for role in (
                "everydayai_worker", "everydayai",
                "everydayai_agent_model_gateway", "public",
            ):
                assert connection.execute(
                    "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                    (role, signature),
                ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_schema_privilege(%s,'public','USAGE')",
            ("everydayai_agent_model_gateway",),
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_model_gateway", HISTORICAL_GATEWAY_SIGNATURE),
        ).fetchone()[0] is False


def test_single_runtime_dispatch_configuration_fence_acl_and_rollback(
    database: str,
) -> None:
    _prepare(database)
    ids = _seed_model_attempt(database)
    with psycopg.connect(_worker_url(database)) as connection:
        connection.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        with pytest.raises(
            psycopg.errors.InvalidParameterValue,
            match="AGENT_RUNTIME_MODEL_DISPATCH_INVALID",
        ):
            connection.execute(
                "SELECT start_model_attempt_dispatch_v2(%s,%s,NULL,%s)",
                (ids["attempt"], ids["token"], "c" * 64),
            )
    dispatch = _dispatch(database, ids)
    assert dispatch["outcome"] == "dispatching"
    assert dispatch["state_version"] == 1
    bundle = _configuration(database, ids)
    assert bundle["bundle"] == "ai.provider.dashscope"
    assert bundle["items"][0]["secret_ref"]["payload_ciphertext"] == "fixture-ciphertext"
    assert "plaintext-api-key" not in str(bundle).lower()
    _assert_acl(database)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _configuration(database, ids, worker="wrong-worker")
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls("
            "org_id,gate_scope,scope_key,dispatch_blocked,kill_epoch,state_version,"
            "reason,updated_by) VALUES(%s,'provider','dashscope',TRUE,1,1,'test',%s) "
            "ON CONFLICT (org_id,gate_scope,scope_key) DO UPDATE SET "
            "dispatch_blocked=TRUE,kill_epoch="
            "agent_runtime_tenant_gate_controls.kill_epoch+1,state_version="
            "agent_runtime_tenant_gate_controls.state_version+1,reason='test',"
            "updated_by=EXCLUDED.updated_by,updated_at=clock_timestamp()",
            (ORG, USER),
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="AGENT_RUNTIME_MODEL_CONFIGURATION_FENCED",
    ):
        _configuration(database, ids)
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="AGENT_RUNTIME_MODEL_DISPATCH_ROLLBACK_FACTS_EXIST",
    ):
        _apply(database, ROLLBACK)

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("TRUNCATE agent_model_attempts CASCADE")
        connection.commit()
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (CONFIG_SIGNATURE,),
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT has_schema_privilege(%s,'public','USAGE')",
            ("everydayai_agent_model_gateway",),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_model_gateway", HISTORICAL_GATEWAY_SIGNATURE),
        ).fetchone()[0] is True
    _apply(database, MIGRATION)
    _assert_acl(database)
    _apply(database, ROLLBACK)
