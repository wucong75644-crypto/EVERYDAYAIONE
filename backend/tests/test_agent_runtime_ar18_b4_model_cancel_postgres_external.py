from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
from threading import Barrier

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_model_gateway_dispatch_binding_postgres_external import (
    CLAIM,
    START,
    _claim_params,
    _seed_gates,
    _start_params,
)
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
MIGRATIONS = tuple(ROOT / "migrations" / name for name in (
    "227_19_agent_runtime_model_gateway_predispatch_failure.sql",
    "227_20_agent_runtime_model_gateway_dispatch_binding.sql",
    "227_25_agent_runtime_model_gateway_cancel_fence.sql",
))
ROLLBACK = ROOT / "migrations/rollback/227_25_agent_runtime_model_gateway_cancel_fence_rollback.sql"
LOG = Path("/private/tmp/ar18-b4-model-cancel-postgres.log")
GATEWAY = "everydayai_agent_model_gateway"


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection, connection.transaction():
        connection.execute(path.read_text(encoding="utf-8"))


def _prepare(url: str) -> None:
    _prepare_schema(url)
    for migration in MIGRATIONS:
        _apply(url, migration)
    _seed_gates(url)
    _install_cancel_facade(url)


def _install_cancel_facade(url: str) -> None:
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
        CREATE FUNCTION test_b4_cancel_agent_run(UUID,BIGINT,TEXT) RETURNS JSONB
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public
        AS 'SELECT cancel_agent_run($1,$2,$3)';
        REVOKE ALL ON FUNCTION test_b4_cancel_agent_run(UUID,BIGINT,TEXT) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION test_b4_cancel_agent_run(UUID,BIGINT,TEXT)
          TO everydayai_runtime;
        """)
        connection.commit()


def _start(url: str) -> tuple[dict[str, object], dict[str, object]]:
    ids = _seed(url, org_id=ORG, user_id=ORG_USER)
    started = _call(
        url, "everydayai_agent_runtime_worker", START, _start_params(ids),
    )
    assert started["outcome"] == "dispatching"
    return ids, started["operation"]


def _claim(
    url: str, ids: dict[str, object], operation: dict[str, object],
) -> dict[str, object]:
    claim = _call(url, GATEWAY, CLAIM, _claim_params(ids, operation))
    assert claim["outcome"] == "claimed"
    return claim


def _mutation(
    ids: dict[str, object], claim: dict[str, object], version: int,
) -> tuple[object, ...]:
    operation = claim["operation"]
    return (
        operation["operation_id"], claim["claim_token"], version,
        ids["token"], HASH, REVISION,
        operation["tenant_kill_epoch"], operation["provider_kill_epoch"],
        operation["capability_kill_epoch"],
    )


def _cancel(url: str, ids: dict[str, object]) -> dict[str, object]:
    role_url = url.replace("postgres@", "everydayai_runtime@")
    with psycopg.connect(role_url) as connection:
        connection.execute("SELECT set_config('app.access_kind','runtime',false)")
        connection.execute("SELECT set_config('app.actor_user_id',%s,false)", (str(ORG_USER),))
        connection.execute("SELECT set_config('app.org_id',%s,false)", (str(ORG),))
        result = connection.execute(
            "SELECT test_b4_cancel_agent_run(%s,0,'task_cancel_requested')",
            (ids["run"],),
        ).fetchone()[0]
        connection.commit()
        return result


def _operation_row(url: str, ids: dict[str, object]) -> tuple[object, ...] | None:
    with psycopg.connect(url) as connection:
        return connection.execute(
            "SELECT status,state_version,lease_token,finalize_token,"
            "terminal_error_code,ambiguity_code FROM agent_runtime_model_gateway_operations "
            "WHERE model_attempt_id=%s", (ids["attempt"],),
        ).fetchone()


def _finalize(
    url: str, ids: dict[str, object], claim: dict[str, object], version: int,
    status: str = "completed",
) -> dict[str, object]:
    return _call(url, GATEWAY, "finalize_agent_runtime_model_gateway_operation", (
        *_mutation(ids, claim, version), status, "provider-request-1", True,
        "9" * 64 if status == "completed" else None, "{}",
        "GATEWAY_PROVIDER_FAILED" if status == "failed" else None,
        "GATEWAY_PROVIDER_OUTCOME_UNKNOWN" if status == "unknown" else None,
    ))


def _assert_security(url: str) -> None:
    signatures = (
        "mark_agent_runtime_model_gateway_dispatched(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint)",
        "renew_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,integer)",
        "finalize_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,text,text,boolean,text,jsonb,text,text)",
    )
    with psycopg.connect(url) as connection:
        for signature in signatures:
            assert connection.execute(
                "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
                (signature,),
            ).fetchone() == (True, ["search_path=pg_catalog, public"])
            assert connection.execute(
                "SELECT has_function_privilege('everydayai_agent_model_gateway',%s,'EXECUTE')",
                (signature,),
            ).fetchone()[0] is True
        for signature in (
            "_agent_model_gateway_parent_active_v1(uuid)",
            "_cancel_agent_run_action_work(uuid)",
        ):
            for role in (
                "public", "everydayai_worker", "everydayai_agent_runtime_worker",
                "everydayai_agent_model_gateway",
            ):
                assert connection.execute(
                    "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, signature),
                ).fetchone()[0] is False
        for role in (
            "public", "everydayai_worker", "everydayai_agent_runtime_worker",
            "everydayai_agent_model_gateway",
        ):
            assert connection.execute(
                "SELECT has_table_privilege(%s,'agent_runtime_model_gateway_operations','SELECT')",
                (role,),
            ).fetchone()[0] is False
        assert connection.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname='agent_runtime_model_gateway_operations'",
        ).fetchone() == (True, True)


def _exercise_states(url: str) -> None:
    prepared = _seed(url, org_id=ORG, user_id=ORG_USER)
    assert _cancel(url, prepared)["outcome"] == "cancelled"
    assert _operation_row(url, prepared) is None

    submitted, _ = _start(url)
    assert _cancel(url, submitted)["outcome"] == "cancelled"
    row = _operation_row(url, submitted)
    assert row is not None and row[:3] == ("failed", 1, None)
    assert row[4:] == (
        "GATEWAY_PARENT_RUN_CANCELLED_BEFORE_DISPATCH", None,
    )

    claimed, operation = _start(url)
    claim = _claim(url, claimed, operation)
    assert _cancel(url, claimed)["outcome"] == "cancelled"
    row = _operation_row(url, claimed)
    assert row is not None and row[0] == "failed" and row[2] is None
    assert str(row[3]) == str(claim["claim_token"])
    for name, params in (
        ("mark_agent_runtime_model_gateway_dispatched", _mutation(claimed, claim, 1)),
        ("renew_agent_runtime_model_gateway_operation", (*_mutation(claimed, claim, 1), 120)),
    ):
        result = _call(url, GATEWAY, name, params)
        assert result["outcome"] == "readback"
        assert result["operation"]["status"] == "failed"
    assert _finalize(url, claimed, claim, 1)["outcome"] == "readback"

    dispatching, operation = _start(url)
    claim = _claim(url, dispatching, operation)
    marked = _call(
        url, GATEWAY, "mark_agent_runtime_model_gateway_dispatched",
        _mutation(dispatching, claim, 1),
    )
    assert marked["outcome"] == "dispatching"
    assert _cancel(url, dispatching)["outcome"] == "cancelled"
    row = _operation_row(url, dispatching)
    assert row is not None and row[0] == "unknown" and row[2] is None
    assert row[5] == "GATEWAY_PARENT_RUN_CANCELLED_AFTER_DISPATCH"
    assert _finalize(url, dispatching, claim, 2)["operation"]["status"] == "unknown"

    for terminal in ("unknown", "completed"):
        ids, operation = _start(url)
        claim = _claim(url, ids, operation)
        assert _call(
            url, GATEWAY, "mark_agent_runtime_model_gateway_dispatched",
            _mutation(ids, claim, 1),
        )["outcome"] == "dispatching"
        assert _finalize(url, ids, claim, 2, terminal)["outcome"] == terminal
        before = _operation_row(url, ids)
        assert _cancel(url, ids)["outcome"] == "cancelled"
        assert _operation_row(url, ids) == before


def _barrier_rpc(
    url: str, role: str, query: str, params: tuple[object, ...], barrier: Barrier,
) -> dict[str, object]:
    role_url = url.replace("postgres@", f"{role}@")
    with psycopg.connect(role_url) as connection:
        kind = "runtime" if role == "everydayai_runtime" else "agent_model_gateway"
        connection.execute("SELECT set_config('app.access_kind',%s,false)", (kind,))
        if role == "everydayai_runtime":
            connection.execute(
                "SELECT set_config('app.actor_user_id',%s,false)", (str(ORG_USER),),
            )
            connection.execute("SELECT set_config('app.org_id',%s,false)", (str(ORG),))
        barrier.wait(timeout=5)
        result = connection.execute(query, params).fetchone()[0]
        connection.commit()
        return result


def _race(
    url: str, ids: dict[str, object], name: str, params: tuple[object, ...],
) -> tuple[dict[str, object], dict[str, object]]:
    barrier = Barrier(2)
    cancel_query = "SELECT test_b4_cancel_agent_run(%s,0,'task_cancel_requested')"
    gateway_query = f"SELECT {name}({','.join(['%s'] * len(params))})"
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(
            _barrier_rpc, url, "everydayai_runtime", cancel_query,
            (ids["run"],), barrier,
        )
        gateway_future = pool.submit(
            _barrier_rpc, url, GATEWAY, gateway_query, params, barrier,
        )
        return cancel_future.result(), gateway_future.result()


def _exercise_races(url: str) -> None:
    ids, operation = _start(url)
    cancelled, claimed = _race(url, ids, CLAIM, _claim_params(ids, operation))
    assert cancelled["outcome"] == "cancelled"
    assert claimed["outcome"] in {"claimed", "fenced"}
    assert _operation_row(url, ids)[0] == "failed"

    ids, operation = _start(url)
    claim = _claim(url, ids, operation)
    cancelled, marked = _race(
        url, ids, "mark_agent_runtime_model_gateway_dispatched",
        _mutation(ids, claim, 1),
    )
    assert cancelled["outcome"] == "cancelled"
    assert marked["outcome"] in {"dispatching", "readback"}
    assert _operation_row(url, ids)[0] in {"failed", "unknown"}

    ids, operation = _start(url)
    claim = _claim(url, ids, operation)
    cancelled, renewed = _race(
        url, ids, "renew_agent_runtime_model_gateway_operation",
        (*_mutation(ids, claim, 1), 120),
    )
    assert cancelled["outcome"] == "cancelled"
    assert renewed["outcome"] in {"renewed", "readback"}
    assert _operation_row(url, ids)[0] == "failed"

    ids, operation = _start(url)
    claim = _claim(url, ids, operation)
    assert _call(
        url, GATEWAY, "mark_agent_runtime_model_gateway_dispatched",
        _mutation(ids, claim, 1),
    )["outcome"] == "dispatching"
    finalize_params = (
        *_mutation(ids, claim, 2), "completed", "provider-request-race", True,
        "8" * 64, "{}", None, None,
    )
    cancelled, finalized = _race(
        url, ids, "finalize_agent_runtime_model_gateway_operation", finalize_params,
    )
    assert cancelled["outcome"] == "cancelled"
    assert finalized["outcome"] in {"completed", "readback"}
    assert _operation_row(url, ids)[0] in {"completed", "unknown"}


def _exercise_rollback(url: str) -> None:
    ids, operation = _start(url)
    _claim(url, ids, operation)
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="AGENT_MODEL_GATEWAY_CANCEL_FENCE_ROLLBACK_PENDING_FACTS",
    ):
        _apply(url, ROLLBACK)
    _cancel(url, ids)
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="AGENT_MODEL_GATEWAY_CANCEL_FENCE_ROLLBACK_PENDING_FACTS",
    ):
        _apply(url, ROLLBACK)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("DELETE FROM agent_runtime_model_gateway_operations")
        connection.commit()

    unresolved, _ = _start(url)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runs SET status='cancelled',state_version=state_version+1,"
            "terminal_reason='test_unsettled_gateway_operation',execution_token=NULL,"
            "lease_expires_at=NULL,completed_at=clock_timestamp(),"
            "updated_at=clock_timestamp() WHERE id=%s",
            (unresolved["run"],),
        )
        connection.commit()
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="AGENT_MODEL_GATEWAY_CANCEL_FENCE_ROLLBACK_PENDING_FACTS",
    ):
        _apply(url, ROLLBACK)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("DELETE FROM agent_runtime_model_gateway_operations")
        connection.commit()

    _apply(url, ROLLBACK)
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT to_regprocedure('_agent_model_gateway_parent_active_v1(uuid)')",
        ).fetchone()[0] is None
        for signature in (
            "mark_agent_runtime_model_gateway_dispatched(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint)",
            "renew_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,integer)",
            "finalize_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,text,text,boolean,text,jsonb,text,text)",
        ):
            assert connection.execute(
                "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
                (signature,),
            ).fetchone() == (True, ["search_path=pg_catalog, public"])
            assert connection.execute(
                "SELECT has_function_privilege('everydayai_agent_model_gateway',%s,'EXECUTE')",
                (signature,),
            ).fetchone()[0] is True
    _apply(url, MIGRATIONS[-1])
    _assert_security(url)
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT to_regprocedure('_agent_model_gateway_parent_active_v1(uuid)')",
        ).fetchone()[0] is not None
        data_directory = Path(connection.execute("SHOW data_directory").fetchone()[0])
    shutil.copyfile(data_directory / "postgres.log", LOG)
    assert LOG.stat().st_size > 0


def test_b4_model_gateway_cancel_database_contract(database: str) -> None:
    _prepare(database)
    _assert_security(database)
    _exercise_states(database)
    _exercise_races(database)
    _exercise_rollback(database)
