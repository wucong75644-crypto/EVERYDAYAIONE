from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = UUID("22222222-2222-2222-2222-222222222222")
ORG_USER = UUID("44444444-4444-4444-4444-444444444444")
PERSONAL_USER = UUID("11111111-1111-1111-1111-111111111111")
MIGRATION = ROOT / "migrations/227_18_agent_runtime_model_gateway.sql"
ROLLBACK = ROOT / "migrations/rollback/227_18_agent_runtime_model_gateway_rollback.sql"
HASH = "a" * 64
REVISION = "gateway-model-revision-v1"
POSTGRES_LOG = Path("/private/tmp/c7-bg2-model-gateway-postgres.log")


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _prepare_schema(url: str) -> None:
    with psycopg.connect(url) as connection:
        connection.execute("""
          CREATE ROLE everydayai_agent_model_gateway LOGIN NOINHERIT
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
          GRANT everydayai_agent_model_gateway TO CURRENT_USER;
        """)
        connection.commit()
    for name in (
        "158_configuration_control_plane_foundation.sql",
        "160_configuration_resolution_core.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
    ):
        _apply(url, ROOT / "migrations" / name)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
          WITH secret AS (
            INSERT INTO secret_records(
              scope_kind,secret_name,payload_ciphertext,wrapped_dek,kek_version,
              payload_version,created_by,updated_by
            ) VALUES(
              'platform','ai.dashscope_api_key','fixture-ciphertext',
              'fixture-wrapped-dek','v1',1,%s,%s
            ) RETURNING id
          )
          INSERT INTO configuration_entries(
            scope_kind,definition_version,config_key,secret_id,updated_by
          ) SELECT 'platform','v1','ai.dashscope.api_key',id,%s FROM secret;
        """, (ORG_USER, ORG_USER, ORG_USER))
        connection.commit()
    _apply(url, MIGRATION)


def _settings(connection: psycopg.Connection[object], role: str) -> None:
    kind = (
        "agent_runtime" if role == "everydayai_agent_runtime_worker"
        else "agent_model_gateway"
    )
    connection.execute("SELECT set_config('app.access_kind',%s,false)", (kind,))


def _call(
    url: str, role: str, name: str, params: tuple[object, ...],
) -> dict[str, object]:
    role_url = url.replace("postgres@", f"{role}@")
    with psycopg.connect(role_url) as connection:
        _settings(connection, role)
        placeholders = ",".join(["%s"] * len(params))
        value = connection.execute(
            f"SELECT {name}({placeholders})", params,
        ).fetchone()[0]
        connection.commit()
        return value


def _seed(url: str, *, org_id: UUID | None, user_id: UUID) -> dict[str, object]:
    ids = {name: uuid4() for name in (
        "conversation", "session", "command", "run", "step", "attempt",
        "token", "request",
    )}
    receipt = json.dumps({
        "receipt_hash": "b" * 64,
        "context_plan_hash": "c" * 64,
        "credential_provider": "dashscope",
        "credential_revision": REVISION,
        "credential_purpose": "model.invoke",
    })
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
            "VALUES(%s,%s,%s,'user',%s)",
            (ids["conversation"], user_id, org_id, str(user_id)),
        )
        connection.execute(
            "INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,"
            "scope_kind,scope_id,created_by_user_id,agent_definition_id,"
            "agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')",
            (ids["session"], ids["conversation"], org_id, user_id,
             str(user_id), user_id),
        )
        connection.execute(
            "INSERT INTO agent_session_commands(id,session_id,org_id,user_id,"
            "command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)",
            (ids["command"], ids["session"], org_id, user_id,
             str(ids["command"]), "d" * 32),
        )
        connection.execute(
            "INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,"
            "status,idempotency_key,request_hash,execution_token,lease_expires_at) "
            "VALUES(%s,%s,%s,%s,%s,'user','running',%s,%s,%s,"
            "clock_timestamp()+interval '10 minutes')",
            (ids["run"], ids["session"], ids["command"], org_id, user_id,
             str(ids["run"]), "e" * 32, ids["token"]),
        )
        connection.execute(
            "INSERT INTO agent_run_attempts(run_id,org_id,user_id,attempt_number,"
            "execution_token,worker_id,lease_expires_at) "
            "VALUES(%s,%s,%s,1,%s,'runtime-worker',clock_timestamp()+interval '10 minutes')",
            (ids["run"], org_id, user_id, ids["token"]),
        )
        connection.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,"
            "status,model_id,provider,model_revision,prompt_revision,tool_catalog_revision) "
            "VALUES(%s,%s,%s,%s,%s,1,'running','qwen-plus','dashscope',%s,'p1','t1')",
            (ids["step"], ids["run"], ids["session"], org_id, user_id, REVISION),
        )
        connection.execute(
            "INSERT INTO agent_model_attempts(id,model_step_id,run_id,session_id,org_id,"
            "user_id,attempt_number,request_hash,idempotency_key,provider,request_receipt,"
            "worker_id,execution_token,lease_expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,1,%s,%s,'dashscope',%s::jsonb,'runtime-worker',"
            "%s,clock_timestamp()+interval '10 minutes')",
            (ids["attempt"], ids["step"], ids["run"], ids["session"], org_id,
             user_id, HASH, str(ids["attempt"]), receipt, ids["token"]),
        )
        connection.commit()
    ids.update({"org": org_id, "user": user_id})
    return ids


def _submit_params(ids: dict[str, object], *, request_id: object | None = None) -> tuple[object, ...]:
    return (
        request_id or ids["request"], ids["org"], ids["user"], ids["session"],
        ids["run"], ids["step"], ids["attempt"], ids["token"], HASH, 0,
        "qwen-plus", "dashscope", REVISION, REVISION, "model.invoke", 0, 0, 0,
    )


def _claim_params(ids: dict[str, object]) -> tuple[object, ...]:
    return (
        ids["request"], "gateway-worker", "runtime-worker", ids["org"], ids["user"], ids["run"],
        ids["attempt"], ids["token"], HASH, 0, "qwen-plus", "dashscope",
        REVISION, REVISION, "model.invoke", 0, 0, 0, 120,
    )


def _mutation_params(
    ids: dict[str, object], claim: dict[str, object], *, version: int,
) -> list[object]:
    operation = claim["operation"]
    return [
        operation["operation_id"], claim["claim_token"], version, ids["token"],
        HASH, REVISION, 0, 0, 0,
    ]


def _assert_security(url: str) -> None:
    signatures = {
        "runtime": "submit_agent_runtime_model_gateway_operation(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,text,text,text,bigint,bigint,bigint)",
        "gateway": "claim_agent_runtime_model_gateway_operation(uuid,text,text,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,text,text,text,bigint,bigint,bigint,integer)",
    }
    with psycopg.connect(url) as connection:
        role = connection.execute(
            "SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s",
            ("everydayai_agent_model_gateway",),
        ).fetchone()
        assert role == (True, False, False, False, False, False, False)
        assert connection.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=%s",
            ("agent_runtime_model_gateway_operations",),
        ).fetchone() == (True, True)
        for role_name in (
            "everydayai_agent_runtime_worker", "everydayai_agent_model_gateway",
            "everydayai_worker", "everydayai",
        ):
            assert connection.execute(
                "SELECT has_table_privilege(%s,%s,'SELECT')",
                (role_name, "agent_runtime_model_gateway_operations"),
            ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_runtime_worker", signatures["runtime"]),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_model_gateway", signatures["gateway"]),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_runtime_worker", signatures["gateway"]),
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_model_gateway", signatures["runtime"]),
        ).fetchone()[0] is False
        for role_name in ("everydayai_worker", "everydayai", "public"):
            assert connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                (role_name, signatures["gateway"]),
            ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'get_agent_runtime_ai_bundle(uuid,text,uuid,text)','EXECUTE')",
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_agent_model_gateway',"
            "'get_agent_runtime_ai_bundle(uuid,text,uuid,text)','EXECUTE')",
        ).fetchone()[0] is False


def _exercise_submit_claim(
    database: str, org: dict[str, object], personal: dict[str, object],
    stale: dict[str, object],
) -> dict[str, object]:
    runtime = "everydayai_agent_runtime_worker"
    gateway = "everydayai_agent_model_gateway"
    for ids in (org, personal, stale):
        result = _call(
            database, runtime, "submit_agent_runtime_model_gateway_operation",
            _submit_params(ids),
        )
        assert result["outcome"] == "submitted"
    assert _call(
        database, runtime, "submit_agent_runtime_model_gateway_operation",
        _submit_params(org),
    )["outcome"] == "already_submitted"
    assert _call(
        database, runtime, "submit_agent_runtime_model_gateway_operation",
        _submit_params(org, request_id=uuid4()),
    )["outcome"] == "idempotency_conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(
            lambda _: _call(database, gateway,
                "claim_agent_runtime_model_gateway_operation", _claim_params(org)),
            range(8),
        ))
    assert sum(item["outcome"] == "claimed" for item in claims) == 1
    assert sum(item["outcome"] == "busy" for item in claims) == 7
    org_claim = next(item for item in claims if item["outcome"] == "claimed")
    assert org_claim["encrypted_configuration_bundle"]["items"][0]["secret_ref"][
        "payload_ciphertext"
    ] == "fixture-ciphertext"

    read = _call(database, runtime, "read_agent_runtime_model_gateway_operation", (
        org["request"], org["org"], org["user"], org["run"], org["attempt"],
        org["token"], HASH,
    ))
    assert read["outcome"] == "found"
    runtime_projection = json.dumps(read, sort_keys=True)
    for forbidden in ("secret_ref", "payload_ciphertext", "wrapped_dek", "fixture-ciphertext"):
        assert forbidden not in runtime_projection
    return org_claim


def _exercise_dispatch_finalize_recovery(
    database: str, org: dict[str, object], personal: dict[str, object],
    stale: dict[str, object], org_claim: dict[str, object],
) -> None:
    gateway = "everydayai_agent_model_gateway"
    base = _mutation_params(org, org_claim, version=1)
    for index, bad_value in (
        (1, uuid4()), (2, 99), (3, uuid4()), (4, "f" * 64),
        (5, "wrong-revision"), (6, 1), (7, 1), (8, 1),
    ):
        bad = list(base)
        bad[index] = bad_value
        assert _call(
            database, gateway, "mark_agent_runtime_model_gateway_dispatched",
            tuple(bad),
        )["outcome"] == "fenced"
    renewed = _call(
        database, gateway, "renew_agent_runtime_model_gateway_operation",
        (*base, 120),
    )
    assert renewed["outcome"] == "renewed"
    dispatched = _call(
        database, gateway, "mark_agent_runtime_model_gateway_dispatched",
        tuple(_mutation_params(org, org_claim, version=2)),
    )
    assert dispatched["outcome"] == "dispatching"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls(org_id,gate_scope,"
            "scope_key,dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'provider','dashscope',FALSE,1,1,'gateway fence test',%s)",
            (ORG, ORG_USER),
        )
        connection.commit()
    assert _call(
        database, gateway, "renew_agent_runtime_model_gateway_operation",
        (*_mutation_params(org, org_claim, version=3), 120),
    )["outcome"] == "fenced"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "DELETE FROM agent_runtime_tenant_gate_controls WHERE org_id=%s "
            "AND gate_scope='provider' AND scope_key='dashscope'", (ORG,),
        )
        connection.commit()

    personal_claim = _call(
        database, gateway, "claim_agent_runtime_model_gateway_operation",
        _claim_params(personal),
    )
    personal_dispatched = _call(
        database, gateway, "mark_agent_runtime_model_gateway_dispatched",
        tuple(_mutation_params(personal, personal_claim, version=1)),
    )
    assert personal_dispatched["outcome"] == "dispatching"
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _call(
            database, gateway, "finalize_agent_runtime_model_gateway_operation",
            (*_mutation_params(personal, personal_claim, version=2), "completed",
             "provider-request-1", True, "9" * 64,
             json.dumps({"input_tokens": -1}), None, None),
        )
    finalized = _call(
        database, gateway, "finalize_agent_runtime_model_gateway_operation",
        (*_mutation_params(personal, personal_claim, version=2), "completed",
         "provider-request-1", True, "9" * 64,
         json.dumps({"input_tokens": 3, "output_tokens": 2, "unit": "tokens"}),
         None, None),
    )
    assert finalized["outcome"] == "completed"
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT status,state_version FROM agent_model_attempts WHERE id=%s",
            (personal["attempt"],),
        ).fetchone() == ("prepared", 0)

    stale_claim = _call(
        database, gateway, "claim_agent_runtime_model_gateway_operation",
        _claim_params(stale),
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_model_gateway_operations SET lease_expires_at="
            "clock_timestamp()-interval '1 second' WHERE request_id IN(%s,%s)",
            (org["request"], stale["request"]),
        )
        connection.commit()
    recovered = _call(
        database, gateway, "recover_agent_runtime_model_gateway_operations",
        ("recovery-worker", 120, 50),
    )
    states = {item["request_id"]: item["status"] for item in recovered["operations"]}
    assert states[str(org["request"])] == "unknown"
    assert states[str(stale["request"])] == "submitted"
    assert _call(
        database, gateway, "claim_agent_runtime_model_gateway_operation",
        _claim_params(org),
    )["outcome"] == "readback"
    reclaimed = _call(
        database, gateway, "claim_agent_runtime_model_gateway_operation",
        _claim_params(stale),
    )
    assert reclaimed["outcome"] == "claimed"
    assert reclaimed["claim_token"] != stale_claim["claim_token"]


def _exercise_rollback(database: str) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="AGENT_MODEL_GATEWAY_OPERATION_FACTS_EXIST"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("TRUNCATE agent_runtime_model_gateway_operations")
        connection.commit()
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'get_agent_runtime_ai_bundle(uuid,text,uuid,text)','EXECUTE')",
        ).fetchone()[0] is False
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        data_directory = Path(connection.execute("SHOW data_directory").fetchone()[0])
    shutil.copyfile(data_directory / "postgres.log", POSTGRES_LOG)
    assert POSTGRES_LOG.stat().st_size > 0


def test_gateway_database_contract_and_recovery(database: str) -> None:
    _prepare_schema(database)
    _assert_security(database)
    org = _seed(database, org_id=ORG, user_id=ORG_USER)
    personal = _seed(database, org_id=None, user_id=PERSONAL_USER)
    stale = _seed(database, org_id=ORG, user_id=ORG_USER)
    org_claim = _exercise_submit_claim(database, org, personal, stale)
    _exercise_dispatch_finalize_recovery(
        database, org, personal, stale, org_claim,
    )
    _exercise_rollback(database)
