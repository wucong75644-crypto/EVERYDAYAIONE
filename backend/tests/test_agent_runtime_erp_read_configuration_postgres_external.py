from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_model_configuration_facade_postgres_external import (
    _prepare as _prepare_model_configuration,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_54_agent_runtime_erp_read_configuration.sql"
ROLLBACK = ROOT / "migrations/rollback/227_54_agent_runtime_erp_read_configuration_rollback.sql"
RELEASE = ROOT / "migrations/227_55_agent_runtime_erp_read_release.sql"
RELEASE_ROLLBACK = ROOT / "migrations/rollback/227_55_agent_runtime_erp_read_release_rollback.sql"
ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"
CONVERSATION = "55555555-5555-5555-5555-555555555555"
REQUEST_HASH = "a" * 64


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection, connection.transaction():
        connection.execute(path.read_text(encoding="utf-8"))


def _prepare(url: str) -> None:
    _prepare_model_configuration(url)
    _apply(url, ROOT / "migrations/159_configuration_management_core.sql")
    _apply(url, MIGRATION)
    _apply(url, RELEASE)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for key, secret_name in (
            ("erp.app_credentials", "erp.app_credentials"),
            ("erp.token_pair", "erp.token_pair"),
        ):
            secret_id = connection.execute(
                "INSERT INTO secret_records(scope_kind,org_id,secret_name,"
                "payload_ciphertext,wrapped_dek,kek_version,payload_version,"
                "created_by,updated_by) VALUES('organization',%s,%s,%s,%s,'v1',1,%s,%s) "
                "RETURNING id",
                (ORG, secret_name, f"cipher-{key}", f"wrapped-{key}", USER, USER),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO configuration_entries(scope_kind,org_id,definition_version,"
                "config_key,secret_id,updated_by) VALUES('organization',%s,'v1',%s,%s,%s)",
                (ORG, key, secret_id, USER),
            )
        connection.commit()


def _seed(url: str) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in (
        "session", "command", "run", "step", "action", "attempt",
        "token", "policy", "intent",
    )}
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,"
            "scope_kind,scope_id,created_by_user_id,agent_definition_id,"
            "agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')",
            (ids["session"], CONVERSATION, ORG, USER, USER, USER),
        )
        connection.execute(
            "INSERT INTO agent_session_commands(id,session_id,org_id,user_id,"
            "command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)",
            (ids["command"], ids["session"], ORG, USER, ids["command"], "b" * 32),
        )
        connection.execute(
            "INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,"
            "status,idempotency_key,request_hash,execution_token,lease_expires_at) "
            "VALUES(%s,%s,%s,%s,%s,'user','running',%s,%s,%s,"
            "clock_timestamp()+interval '10 minutes')",
            (ids["run"], ids["session"], ids["command"], ORG, USER,
             ids["run"], "c" * 32, ids["token"]),
        )
        connection.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,"
            "step_number,model_id,provider,model_revision,prompt_revision,"
            "tool_catalog_revision) VALUES(%s,%s,%s,%s,%s,1,'fixture','dashscope',"
            "'v1','v1','v1')",
            (ids["step"], ids["run"], ids["session"], ORG, USER),
        )
        policy = (
            '{"provider":"erp","capability":"network.provider.read",'
            '"provider_revision":null,"capability_revision":null}'
        )
        connection.execute(
            "INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,"
            "user_id,action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,"
            "request_hash,batch_hash,policy_decision,policy_snapshot,policy_revision,"
            "retry_disposition,status) VALUES(%s,%s,%s,%s,%s,%s,0,%s,"
            "'erp_trade_query','{\"action\":\"order_list\",\"params\":{}}',%s,%s,%s,"
            "'preauthorized',%s::jsonb,'v1','retry_safe','running')",
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
            "VALUES(%s,%s,%s,%s,%s,%s,'allow',%s,'runtime_remote_read:erp_trade_query',"
            "1,'v1','{}',ARRAY['fixture'],%s,clock_timestamp()+interval '10 minutes')",
            (ids["policy"], ids["action"], ids["session"], ids["run"], ORG, USER,
             "d" * 64, "f" * 64),
        )
        connection.execute(
            "INSERT INTO agent_action_dispatch_intents(id,attempt_id,action_id,"
            "policy_receipt_id,execution_token,request_hash,executor_type,"
            "executor_revision,policy_revision,external_idempotency_key,recovery_mode) "
            "VALUES(%s,%s,%s,%s,%s,%s,'runtime_remote_read:erp_trade_query',1,'v1',%s,"
            "'idempotent_replay')",
            (ids["intent"], ids["attempt"], ids["action"], ids["policy"],
             ids["token"], REQUEST_HASH, ids["attempt"]),
        )
        epochs = {
            row[0]: row[1] for row in connection.execute(
                "SELECT gate_scope,kill_epoch FROM agent_runtime_tenant_gate_controls "
                "WHERE org_id=%s AND ((gate_scope='tenant' AND scope_key='tenant') "
                "OR (gate_scope='provider' AND scope_key='erp') OR "
                "(gate_scope='capability' AND scope_key='network.provider.read'))",
                (ORG,),
            ).fetchall()
        }
        connection.execute(
            "INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,"
            "execution_token,tenant_kill_epoch,provider_kill_epoch,"
            "capability_kill_epoch,state_version,lease_expires_at,status) "
            "VALUES('attempt',%s,%s,%s,%s,%s,%s,0,"
            "clock_timestamp()+interval '10 minutes','active')",
            (
                ids["attempt"], ORG, ids["token"],
                epochs.get("tenant", 0), epochs.get("provider", 0),
                epochs.get("capability", 0),
            ),
        )
        connection.commit()
    return ids


def _worker_call(url: str, function: str, params: tuple[object, ...]):
    worker_url = url.replace("postgres@", "everydayai_agent_runtime_worker@")
    with psycopg.connect(worker_url) as connection:
        connection.execute(
            "SELECT set_config('app.access_kind','agent_runtime',false)"
        )
        placeholders = ",".join(["%s"] * len(params))
        return connection.execute(
            f"SELECT {function}({placeholders})", params,
        ).fetchone()[0]


def _assert_dispatch_binding_rejections(
    url: str, ids: dict[str, str], params: tuple[object, ...],
) -> None:
    mutations = (
        ("agent_action_dispatch_intents", "executor_type='runtime_read:wrong'",
         "executor_type='runtime_remote_read:erp_trade_query'", ids["intent"]),
        ("agent_action_dispatch_intents", "executor_revision=2",
         "executor_revision=1", ids["intent"]),
        ("agent_action_dispatch_intents", "recovery_mode='reconcile_only'",
         "recovery_mode='idempotent_replay'", ids["intent"]),
        ("agent_policy_receipts", "decision='deny'", "decision='allow'", ids["policy"]),
        ("agent_policy_receipts", "arguments_hash='" + "0" * 64 + "'",
         "arguments_hash='" + "d" * 64 + "'", ids["policy"]),
        ("agent_policy_receipts", "policy_revision='wrong'",
         "policy_revision='v1'", ids["policy"]),
        ("agent_policy_receipts", "expires_at=evaluated_at+interval '1 microsecond'",
         "expires_at=clock_timestamp()+interval '10 minutes'", ids["policy"]),
    )
    for table, mutation, restore, row_id in mutations:
        with psycopg.connect(url) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute(
                f"UPDATE {table} SET {mutation} WHERE id=%s", (row_id,),
            )
            connection.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _worker_call(url, "get_agent_runtime_erp_configuration_v1", params)
        with psycopg.connect(url) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute(
                f"UPDATE {table} SET {restore} WHERE id=%s", (row_id,),
            )
            connection.commit()


def test_erp_read_configuration_acl_fencing_token_cas_and_rollback(
    database: str,
) -> None:
    _prepare(database)
    ids = _seed(database)
    params = (
        ids["attempt"], "runtime-worker", ids["token"], 0, REQUEST_HASH,
    )
    bundle = _worker_call(
        database, "get_agent_runtime_erp_configuration_v1", params,
    )
    assert bundle["bundle"] == "erp.runtime"
    assert {item["key"] for item in bundle["items"]} >= {
        "erp.app_credentials", "erp.token_pair",
    }
    _assert_dispatch_binding_rejections(database, ids, params)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _worker_call(
            database, "get_agent_runtime_erp_configuration_v1",
            (ids["attempt"], "wrong-worker", ids["token"], 0, REQUEST_HASH),
        )
    rotated = _worker_call(
        database, "rotate_agent_runtime_erp_token_pair_v1", (
            *params,
            '{"payload_ciphertext":"next-cipher","wrapped_dek":"next-wrapped",'
            '"kek_version":"v1"}', 1,
        ),
    )
    assert rotated["version"] == 2
    with pytest.raises(psycopg.errors.SerializationFailure):
        _worker_call(
            database, "rotate_agent_runtime_erp_token_pair_v1", (
                *params,
                '{"payload_ciphertext":"conflict","wrapped_dek":"conflict",'
                '"kek_version":"v1"}', 1,
            ),
        )
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'configuration_entries','SELECT')",
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'get_agent_runtime_erp_configuration_v1(uuid,text,uuid,bigint,text)',"
            "'EXECUTE')",
        ).fetchone()[0] is False
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls(org_id,gate_scope,"
            "scope_key,dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'provider','erp',TRUE,1,1,'test',%s) "
            "ON CONFLICT(org_id,gate_scope,scope_key) DO UPDATE SET "
            "dispatch_blocked=TRUE,kill_epoch=1,state_version=1",
            (ORG, USER),
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="AGENT_RUNTIME_ERP_CONFIGURATION_FENCED",
    ):
        _worker_call(
            database, "get_agent_runtime_erp_configuration_v1", params,
        )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_definition_facts SET used_by_ingress=TRUE "
            "WHERE agent_key='everydayai-default' AND definition_revision='v5'",
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="AGENT_RUNTIME_ERP_READ_RELEASE_FACTS_EXIST",
    ):
        _apply(database, RELEASE_ROLLBACK)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_definition_facts SET used_by_ingress=FALSE "
            "WHERE agent_key='everydayai-default' AND definition_revision='v5'",
        )
        connection.commit()
    _apply(database, RELEASE_ROLLBACK)
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure('get_agent_runtime_erp_configuration_v1"
            "(uuid,text,uuid,bigint,text)')",
        ).fetchone()[0] is None
    _apply(database, MIGRATION)
    _apply(database, RELEASE)
    _apply(database, RELEASE_ROLLBACK)
    _apply(database, ROLLBACK)
