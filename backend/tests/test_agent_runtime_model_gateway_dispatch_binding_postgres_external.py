from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    _call,
    _prepare_schema,
    _seed,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION_19 = ROOT / "migrations/227_19_agent_runtime_model_gateway_predispatch_failure.sql"
MIGRATION = ROOT / "migrations/227_20_agent_runtime_model_gateway_dispatch_binding.sql"
ROLLBACK = ROOT / "migrations/rollback/227_20_agent_runtime_model_gateway_dispatch_binding_rollback.sql"
START = "start_agent_runtime_model_gateway_dispatch"
CLAIM = "claim_agent_runtime_model_gateway_operation_v2"
POSTGRES_LOG = Path("/private/tmp/c7-bg35-dispatch-binding-postgres.log")


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _start_params(
    ids: dict[str, object], *, request_id: object | None = None,
    token: object | None = None, request_hash: str = HASH,
    attempt_version: int = 0, provider_revision: str = REVISION,
) -> tuple[object, ...]:
    return (
        request_id or ids["request"], ids["session"], ids["run"], ids["step"],
        ids["attempt"], token or ids["token"], request_hash, attempt_version,
        "qwen-plus", "dashscope", provider_revision, REVISION, "model.invoke",
    )


def _claim_params(
    ids: dict[str, object], operation: dict[str, object],
    *, attempt_version: int | None = None,
) -> tuple[object, ...]:
    return (
        ids["request"], "gateway-worker", "runtime-worker", ids["org"],
        ids["user"], ids["run"], ids["attempt"], ids["token"], HASH,
        operation["attempt_state_version"] if attempt_version is None else attempt_version,
        "qwen-plus", "dashscope", REVISION, REVISION, "model.invoke",
        operation["tenant_kill_epoch"], operation["provider_kill_epoch"],
        operation["capability_kill_epoch"], 120,
    )


def _seed_gates(url: str) -> None:
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO agent_runtime_tenant_gate_controls("
                "org_id,gate_scope,scope_key,kill_epoch,state_version,reason,updated_by) "
                "VALUES(%s,%s,%s,%s,1,'dispatch binding fixture',%s)",
                [
                    (ORG, "tenant", "tenant", 3, ORG_USER),
                    (ORG, "provider", "dashscope", 5, ORG_USER),
                    (ORG, "capability", "model.invoke", 7, ORG_USER),
                ],
            )
        connection.commit()


def _assert_acl(url: str) -> None:
    old_start = (
        "submit_agent_runtime_model_gateway_operation(uuid,uuid,uuid,uuid,uuid,uuid,"
        "uuid,uuid,text,bigint,text,text,text,text,text,bigint,bigint,bigint)"
    )
    old_claim = (
        "claim_agent_runtime_model_gateway_operation(uuid,text,text,uuid,uuid,uuid,uuid,"
        "uuid,text,bigint,text,text,text,text,text,bigint,bigint,bigint,integer)"
    )
    new_start = (
        "start_agent_runtime_model_gateway_dispatch(uuid,uuid,uuid,uuid,uuid,uuid,text,"
        "bigint,text,text,text,text,text)"
    )
    new_claim = (
        "claim_agent_runtime_model_gateway_operation_v2(uuid,text,text,uuid,uuid,uuid,"
        "uuid,uuid,text,bigint,text,text,text,text,text,bigint,bigint,bigint,integer)"
    )
    with psycopg.connect(url) as connection:
        for signature in (new_start, new_claim):
            assert connection.execute(
                "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
                (signature,),
            ).fetchone() == (True, ["search_path=pg_catalog, public"])
        matrix = (
            ("everydayai_agent_runtime_worker", new_start, True),
            ("everydayai_agent_model_gateway", new_start, False),
            ("everydayai_agent_model_gateway", new_claim, True),
            ("everydayai_agent_runtime_worker", new_claim, False),
            ("everydayai_agent_runtime_worker", old_start, False),
            ("everydayai_agent_model_gateway", old_claim, False),
            ("everydayai_worker", new_start, False),
            ("everydayai_worker", new_claim, False),
            ("public", new_start, False),
            ("public", new_claim, False),
        )
        for role, signature, allowed in matrix:
            assert connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, signature),
            ).fetchone()[0] is allowed
        for role in (
            "everydayai_agent_runtime_worker", "everydayai_agent_model_gateway",
            "everydayai_worker", "public",
        ):
            assert connection.execute(
                "SELECT has_table_privilege(%s,'agent_runtime_model_gateway_operations','SELECT')",
                (role,),
            ).fetchone()[0] is False


def _exercise_atomic_and_claim(url: str) -> tuple[dict[str, object], dict[str, object]]:
    ids = _seed(url, org_id=ORG, user_id=ORG_USER)
    runtime = "everydayai_agent_runtime_worker"
    gateway = "everydayai_agent_model_gateway"
    with ThreadPoolExecutor(max_workers=8) as pool:
        starts = list(pool.map(
            lambda _: _call(url, runtime, START, _start_params(ids)), range(8),
        ))
    assert sum(item["outcome"] == "dispatching" for item in starts) == 1
    assert sum(item["outcome"] == "already_dispatching" for item in starts) == 7
    started = starts[0]
    operation = started["operation"]
    assert operation["tenant_kill_epoch"] == 3
    assert operation["provider_kill_epoch"] == 5
    assert operation["capability_kill_epoch"] == 7
    assert operation["provider_revision"] == REVISION
    assert operation["purpose"] == "model.invoke"
    assert started["state_version"] == operation["attempt_state_version"] == 1
    for params in (
        _start_params(ids, request_id=uuid4()),
        _start_params(ids, token=uuid4()),
        _start_params(ids, request_hash="f" * 64),
        _start_params(ids, attempt_version=1),
        _start_params(ids, provider_revision="wrong-revision"),
    ):
        assert _call(url, runtime, START, params)["outcome"] == "idempotency_conflict"
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT status,dispatch_phase,state_version FROM agent_model_attempts WHERE id=%s",
            (ids["attempt"],),
        ).fetchone() == ("dispatching", "request_started", 1)
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_model_gateway_operations WHERE model_attempt_id=%s",
            (ids["attempt"],),
        ).fetchone()[0] == 1
    assert _call(
        url, gateway, CLAIM, _claim_params(ids, operation, attempt_version=0),
    )["outcome"] == "fenced"
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(
            lambda _: _call(url, gateway, CLAIM, _claim_params(ids, operation)),
            range(8),
        ))
    assert sum(item["outcome"] == "claimed" for item in claims) == 1
    assert sum(item["outcome"] == "busy" for item in claims) == 7
    return ids, operation


def _exercise_blocked_and_epoch_fences(
    url: str, old_ids: dict[str, object], old_operation: dict[str, object],
) -> list[dict[str, object]]:
    runtime = "everydayai_agent_runtime_worker"
    gateway = "everydayai_agent_model_gateway"
    blocked_attempts = []
    for scope, key in (
        ("tenant", "tenant"), ("provider", "dashscope"),
        ("capability", "model.invoke"),
    ):
        ids = _seed(url, org_id=ORG, user_id=ORG_USER)
        blocked_attempts.append(ids)
        with psycopg.connect(url) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute(
                "UPDATE agent_runtime_tenant_gate_controls SET dispatch_blocked=TRUE "
                "WHERE org_id=%s AND gate_scope=%s AND scope_key=%s", (ORG, scope, key),
            )
            connection.commit()
        assert _call(url, runtime, START, _start_params(ids))["outcome"] == "fenced"
        with psycopg.connect(url) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute(
                "UPDATE agent_runtime_tenant_gate_controls SET dispatch_blocked=FALSE "
                "WHERE org_id=%s AND gate_scope=%s AND scope_key=%s", (ORG, scope, key),
            )
            connection.commit()
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_tenant_gate_controls SET kill_epoch=kill_epoch+1 "
            "WHERE org_id=%s AND gate_scope='provider' AND scope_key='dashscope'", (ORG,),
        )
        connection.commit()
    assert _call(
        url, gateway, CLAIM, _claim_params(old_ids, old_operation),
    )["outcome"] == "fenced"
    with psycopg.connect(url) as connection:
        for ids in blocked_attempts:
            assert connection.execute(
                "SELECT status,state_version FROM agent_model_attempts WHERE id=%s",
                (ids["attempt"],),
            ).fetchone() == ("prepared", 0)
        assert connection.execute(
            "SELECT count(*) FROM agent_model_attempts a LEFT JOIN "
            "agent_runtime_model_gateway_operations o ON o.model_attempt_id=a.id "
            "WHERE a.status='dispatching' AND o.id IS NULL",
        ).fetchone()[0] == 0
    return blocked_attempts


def _exercise_rollback(
    url: str, atomic_ids: dict[str, object], blocked_attempts: list[dict[str, object]],
) -> None:
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="AGENT_MODEL_GATEWAY_DISPATCH_BINDING_FACTS_EXIST",
    ):
        _apply(url, ROLLBACK)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("DELETE FROM agent_runtime_model_gateway_operations")
        connection.execute(
            "UPDATE agent_model_attempts SET status='prepared',dispatch_phase='prepared',"
            "state_version=0,dispatched_at=NULL WHERE id=%s", (atomic_ids["attempt"],),
        )
        connection.commit()
    _apply(url, ROLLBACK)
    _apply(url, MIGRATION)
    _apply(url, ROLLBACK)
    with psycopg.connect(url) as connection:
        data_directory = Path(connection.execute("SHOW data_directory").fetchone()[0])
    shutil.copyfile(data_directory / "postgres.log", POSTGRES_LOG)
    assert POSTGRES_LOG.stat().st_size > 0


def test_atomic_dispatch_binding_database_contract(database: str) -> None:
    _prepare_schema(database)
    _apply(database, MIGRATION_19)
    _apply(database, MIGRATION)
    _assert_acl(database)
    _seed_gates(database)
    ids, operation = _exercise_atomic_and_claim(database)
    blocked = _exercise_blocked_and_epoch_fences(database, ids, operation)
    _exercise_rollback(database, ids, blocked)
