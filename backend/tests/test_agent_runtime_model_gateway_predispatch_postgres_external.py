from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_model_gateway_postgres_external import (
    HASH,
    ORG,
    ORG_USER,
    REVISION,
    _assert_security,
    _call,
    _claim_params,
    _mutation_params,
    _prepare_schema,
    _seed,
    _submit_params,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_19_agent_runtime_model_gateway_predispatch_failure.sql"
ROLLBACK = ROOT / "migrations/rollback/227_19_agent_runtime_model_gateway_predispatch_failure_rollback.sql"
RPC = "fail_agent_runtime_model_gateway_claim"
SIGNATURE = (
    "fail_agent_runtime_model_gateway_claim("
    "uuid,uuid,bigint,uuid,uuid,text,text,bigint,bigint,bigint,text)"
)
ERROR = "GATEWAY_SECRET_DECRYPT_FAILED"
POSTGRES_LOG = Path("/private/tmp/c7-bg21-model-gateway-predispatch-postgres.log")


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _fail_params(
    ids: dict[str, object], claim: dict[str, object], *, version: int = 1,
    error_code: str = ERROR,
) -> list[object]:
    operation = claim["operation"]
    return [
        operation["operation_id"], claim["claim_token"], version, ids["org"],
        ids["token"], HASH, REVISION, 0, 0, 0, error_code,
    ]


def _submit_and_claim(url: str, ids: dict[str, object]) -> dict[str, object]:
    assert _call(
        url, "everydayai_agent_runtime_worker",
        "submit_agent_runtime_model_gateway_operation", _submit_params(ids),
    )["outcome"] == "submitted"
    claim = _call(
        url, "everydayai_agent_model_gateway",
        "claim_agent_runtime_model_gateway_operation", _claim_params(ids),
    )
    assert claim["outcome"] == "claimed"
    return claim


def _assert_predispatch_security(url: str) -> None:
    with psycopg.connect(url) as connection:
        routine = connection.execute(
            "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
            (SIGNATURE,),
        ).fetchone()
        assert routine == (True, ["search_path=pg_catalog, public"])
        for role, allowed in (
            ("everydayai_agent_model_gateway", True),
            ("everydayai_agent_runtime_worker", False),
            ("everydayai_worker", False),
            ("public", False),
        ):
            assert connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                (role, SIGNATURE),
            ).fetchone()[0] is allowed


def _make_terminal_states(
    url: str, submitted: dict[str, object], dispatching: dict[str, object],
    completed: dict[str, object], unknown: dict[str, object],
) -> list[tuple[dict[str, object], dict[str, object]]]:
    runtime = "everydayai_agent_runtime_worker"
    gateway = "everydayai_agent_model_gateway"
    assert _call(
        url, runtime, "submit_agent_runtime_model_gateway_operation",
        _submit_params(submitted),
    )["outcome"] == "submitted"
    claims = []
    for ids in (dispatching, completed, unknown):
        claims.append((ids, _submit_and_claim(url, ids)))
    for ids, claim in claims:
        assert _call(
            url, gateway, "mark_agent_runtime_model_gateway_dispatched",
            tuple(_mutation_params(ids, claim, version=1)),
        )["outcome"] == "dispatching"
    completed_ids, completed_claim = claims[1]
    assert _call(
        url, gateway, "finalize_agent_runtime_model_gateway_operation",
        (*_mutation_params(completed_ids, completed_claim, version=2),
         "completed", "provider-request", True, "9" * 64,
         json.dumps({"total_tokens": 1, "unit": "tokens"}), None, None),
    )["outcome"] == "completed"
    unknown_ids, unknown_claim = claims[2]
    assert _call(
        url, gateway, "finalize_agent_runtime_model_gateway_operation",
        (*_mutation_params(unknown_ids, unknown_claim, version=2),
         "unknown", None, False, None, json.dumps({}), None,
         "GATEWAY_LOST_AFTER_DISPATCH"),
    )["outcome"] == "unknown"
    return claims


def _exercise_failure_guards(
    database: str, primary: dict[str, object], primary_claim: dict[str, object],
    expired: dict[str, object], expired_claim: dict[str, object],
) -> None:
    gateway = "everydayai_agent_model_gateway"
    base = _fail_params(primary, primary_claim)
    for index, bad_value in (
        (1, uuid4()), (2, 99), (3, uuid4()), (4, uuid4()),
        (5, "f" * 64), (6, "wrong-revision"), (7, 1), (8, 1), (9, 1),
    ):
        bad = list(base)
        bad[index] = bad_value
        assert _call(database, gateway, RPC, tuple(bad))["outcome"] == "fenced"
    with pytest.raises(
        psycopg.errors.InvalidParameterValue,
        match="AGENT_MODEL_GATEWAY_PREDISPATCH_FAILURE_INVALID",
    ):
        bad = list(base)
        bad[-1] = "raw /secret/path exception"
        _call(database, gateway, RPC, tuple(bad))

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls("
            "org_id,gate_scope,scope_key,claim_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'tenant','tenant',TRUE,0,0,'predispatch fence test',%s)",
            (ORG, ORG_USER),
        )
        connection.commit()
    assert _call(database, gateway, RPC, tuple(base))["outcome"] == "fenced"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "DELETE FROM agent_runtime_tenant_gate_controls WHERE org_id=%s",
            (ORG,),
        )
        connection.commit()
    for gate_scope, scope_key in (
        ("provider", "dashscope"), ("capability", "model.invoke"),
    ):
        with psycopg.connect(database) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute(
                "INSERT INTO agent_runtime_tenant_gate_controls("
                "org_id,gate_scope,scope_key,kill_epoch,state_version,reason,updated_by) "
                "VALUES(%s,%s,%s,1,1,'predispatch epoch test',%s)",
                (ORG, gate_scope, scope_key, ORG_USER),
            )
            connection.commit()
        assert _call(database, gateway, RPC, tuple(base))["outcome"] == "fenced"
        with psycopg.connect(database) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute(
                "DELETE FROM agent_runtime_tenant_gate_controls "
                "WHERE org_id=%s AND gate_scope=%s AND scope_key=%s",
                (ORG, gate_scope, scope_key),
            )
            connection.commit()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_model_gateway_operations SET lease_expires_at="
            "clock_timestamp()-interval '1 second' WHERE request_id=%s",
            (expired["request"],),
        )
        connection.commit()
    assert _call(
        database, gateway, RPC, tuple(_fail_params(expired, expired_claim)),
    )["outcome"] == "fenced"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: _call(database, gateway, RPC, tuple(base)), range(8),
        ))
    assert sum(item["outcome"] == "failed" for item in results) == 1
    assert sum(item["outcome"] == "already_failed" for item in results) == 7
    assert _call(database, gateway, RPC, tuple(base))["outcome"] == "already_failed"
    conflict = list(base)
    conflict[-1] = "GATEWAY_KEK_UNAVAILABLE"
    assert _call(database, gateway, RPC, tuple(conflict))["outcome"] == "idempotency_conflict"
    wrong_token = list(base)
    wrong_token[1] = uuid4()
    assert _call(database, gateway, RPC, tuple(wrong_token))["outcome"] == "fenced"
    wrong_binding = list(base)
    wrong_binding[5] = "e" * 64
    assert _call(database, gateway, RPC, tuple(wrong_binding))["outcome"] == "fenced"


def _assert_nonclaimed_states_are_immutable(
    database: str, submitted: dict[str, object],
    terminal_claims: list[tuple[dict[str, object], dict[str, object]]],
) -> None:
    gateway = "everydayai_agent_model_gateway"
    for ids, claim in terminal_claims:
        assert _call(
            database, gateway, RPC, tuple(_fail_params(ids, claim, version=2)),
        )["outcome"] == "fenced"
    submitted_fake_claim = {
        "claim_token": uuid4(),
        "operation": {"operation_id": _call(
            database, "everydayai_agent_runtime_worker",
            "read_agent_runtime_model_gateway_operation",
            (submitted["request"], submitted["org"], submitted["user"],
             submitted["run"], submitted["attempt"], submitted["token"], HASH),
        )["operation"]["operation_id"]},
    }
    assert _call(
        database, gateway, RPC,
        tuple(_fail_params(submitted, submitted_fake_claim, version=0)),
    )["outcome"] == "fenced"


def _assert_failure_fact_and_rollback(
    database: str, primary: dict[str, object], primary_claim: dict[str, object],
) -> None:
    with psycopg.connect(database) as connection:
        row = connection.execute(
            "SELECT status,state_version,dispatching_at,provider_request_id,response_started,"
            "response_hash,usage_summary,terminal_error_code,ambiguity_code,finalize_token,"
            "lease_owner,lease_token,lease_expires_at,finalized_at IS NOT NULL "
            "FROM agent_runtime_model_gateway_operations WHERE request_id=%s",
            (primary["request"],),
        ).fetchone()
        assert row[:9] == (
            "failed", 2, None, None, False, None, {}, ERROR, None,
        )
        assert str(row[9]) == primary_claim["claim_token"]
        assert row[10:] == (None, None, None, True)
        assert connection.execute(
            "SELECT status,state_version FROM agent_model_attempts WHERE id=%s",
            (primary["attempt"],),
        ).fetchone() == ("prepared", 0)
        assert connection.execute(
            "SELECT status FROM agent_model_steps WHERE id=%s", (primary["step"],),
        ).fetchone() == ("running",)

    with pytest.raises(
        psycopg.errors.RaiseException,
        match="AGENT_MODEL_GATEWAY_OPERATION_FACTS_EXIST",
    ):
        _apply(database, ROLLBACK)
    readback = _call(
        database, "everydayai_agent_runtime_worker",
        "read_agent_runtime_model_gateway_operation",
        (primary["request"], primary["org"], primary["user"], primary["run"],
         primary["attempt"], primary["token"], HASH),
    )
    assert readback["operation"]["status"] == "failed"
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (SIGNATURE,),
        ).fetchone()[0] is not None
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_agent_model_gateway',"
            "%s,'EXECUTE')", (SIGNATURE,),
        ).fetchone()[0] is True
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("DELETE FROM agent_runtime_model_gateway_operations")
        connection.commit()

    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (SIGNATURE,),
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'get_agent_runtime_ai_bundle(uuid,text,uuid,text)','EXECUTE')",
        ).fetchone()[0] is False
    _apply(database, MIGRATION)
    _assert_predispatch_security(database)

    with psycopg.connect(database) as connection:
        data_directory = Path(connection.execute("SHOW data_directory").fetchone()[0])
    shutil.copyfile(data_directory / "postgres.log", POSTGRES_LOG)
    assert POSTGRES_LOG.stat().st_size > 0


def test_predispatch_failure_database_contract(database: str) -> None:
    _prepare_schema(database)
    _apply(database, MIGRATION)
    _assert_security(database)
    _assert_predispatch_security(database)

    primary = _seed(database, org_id=ORG, user_id=ORG_USER)
    expired = _seed(database, org_id=ORG, user_id=ORG_USER)
    submitted = _seed(database, org_id=ORG, user_id=ORG_USER)
    dispatching = _seed(database, org_id=ORG, user_id=ORG_USER)
    completed = _seed(database, org_id=ORG, user_id=ORG_USER)
    unknown = _seed(database, org_id=ORG, user_id=ORG_USER)
    primary_claim = _submit_and_claim(database, primary)
    expired_claim = _submit_and_claim(database, expired)
    terminal_claims = _make_terminal_states(
        database, submitted, dispatching, completed, unknown,
    )
    _exercise_failure_guards(
        database, primary, primary_claim, expired, expired_claim,
    )
    _assert_nonclaimed_states_are_immutable(
        database, submitted, terminal_claims,
    )
    _assert_failure_fact_and_rollback(database, primary, primary_claim)
