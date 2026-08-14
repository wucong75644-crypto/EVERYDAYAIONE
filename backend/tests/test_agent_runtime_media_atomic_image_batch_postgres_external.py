from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import (
    _connect,
    _settings,
    database,
)
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema,
    _seed_batch,
)
from tests.test_agent_runtime_media_manifest_readback_postgres_external import (
    _create_legacy_prepared_task,
    _prepare_asset_schema,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_08d_agent_runtime_media_atomic_image_batch.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_08d_agent_runtime_media_atomic_image_batch_rollback.sql"
)
FUNCTION = (
    "submit_agent_runtime_media_image_batch_v1"
    "(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,text,text,text,"
    "text,text,text,jsonb)"
)


def _install_predecessors(database_url: str) -> None:
    _prepare_legacy_schema(database_url)
    _prepare_asset_schema(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for name in (
            "227_63_agent_runtime_chat_action_submission.sql",
            "227_67_agent_runtime_chat_action_catalog_fix.sql",
            "228_05_agent_runtime_media_manifest_readback.sql",
        ):
            connection.execute(
                (ROOT / "migrations" / name).read_text(encoding="utf-8"),
            )


def _apply(database_url: str, path: Path) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(path.read_text(encoding="utf-8"))


def _set_ready(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_media_owner_readiness SET "
            "runtime_enabled=TRUE,provider_probe_passed=TRUE,"
            "production_ready=TRUE,projection_owner_ready=TRUE,"
            "projection_worker_id='atomic-image-batch-test',"
            "projection_revision='228_08d-test',"
            "projection_heartbeat_at=statement_timestamp(),"
            "projection_heartbeat_ttl_seconds=300,state_version=state_version+1 "
            "WHERE singleton",
        )


def _seed_prepared_batch(database_url: str) -> tuple[list[UUID], str, tuple]:
    source = _seed_batch(database_url, 1, credits=1_000)
    task_ids = [
        _create_legacy_prepared_task(
            database_url, source.attempts[0], kind="image",
            model="gpt-image-2-image-to-image",
        )
        for _ in range(2)
    ]
    batch_id = str(uuid4())
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE tasks SET batch_id=%s,image_index=CASE id WHEN %s THEN 0 ELSE 1 END "
            "WHERE id=ANY(%s)",
            (batch_id, task_ids[0], task_ids),
        )
        anchor = connection.execute(
            "SELECT conversation_id,org_id,user_id,input_message_id,"
            "assistant_message_id,turn_id,model_id FROM tasks WHERE id=%s",
            (task_ids[0],),
        ).fetchone()
        requests = connection.execute(
            "SELECT id,request_params FROM tasks WHERE id=ANY(%s) ORDER BY image_index",
            (task_ids,),
        ).fetchall()
    items = [{
        "task_id": str(task_id),
        "idempotency_key": f"atomic-image-batch:{task_id}",
        "arguments": request,
    } for task_id, request in requests]
    return task_ids, batch_id, (*anchor, items)


def _submit(
    database_url: str, batch_id: str, anchor: tuple,
    barrier: Barrier | None = None,
) -> dict[str, object]:
    conversation_id, org_id, user_id, input_id, output_id, turn_id, model_id, items = anchor
    params = (
        conversation_id, org_id, user_id, "user", str(user_id), user_id,
        "everydayai-default", "v1", input_id, output_id, turn_id, batch_id,
        model_id, "runtime", "media-runtime-v1", "runtime-media-v1",
        "runtime-media-v1", Jsonb(items),
    )
    with _connect(database_url, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime", org=org_id, user=user_id)
        if barrier is not None:
            barrier.wait(timeout=10)
        placeholders = ",".join(["%s"] * len(params))
        return connection.execute(
            f"SELECT submit_agent_runtime_media_image_batch_v1({placeholders})",
            params,
        ).fetchone()[0]


def _ownership(database_url: str, task_ids: list[UUID]) -> tuple[int, int, int]:
    with psycopg.connect(database_url) as connection:
        return connection.execute(
            "SELECT "
            "(SELECT count(*) FROM agent_runtime_prepared_media_action_bindings "
            " WHERE task_id=ANY(%s)),"
            "(SELECT count(*) FROM tasks WHERE id=ANY(%s) "
            " AND delivery_context->>'runtime'='true'),"
            "(SELECT count(*) FROM credit_transactions credit "
            " JOIN tasks task ON task.credit_transaction_id=credit.id "
            " WHERE task.id=ANY(%s))",
            (task_ids, task_ids, task_ids),
        ).fetchone()


def test_atomic_image_batch_apply_readback_rollback_reapply(database: str) -> None:
    _install_predecessors(database)
    _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        definition, definer, config = connection.execute(
            "SELECT pg_get_functiondef(procedure.oid),procedure.prosecdef,"
            "procedure.proconfig FROM pg_proc procedure "
            "WHERE procedure.oid=%s::regprocedure",
            (FUNCTION,),
        ).fetchone()
        privileges = connection.execute(
            "SELECT has_function_privilege('everydayai_runtime',%s,'EXECUTE'),"
            "has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE'),"
            "has_function_privilege('everydayai_worker',%s,'EXECUTE'),"
            "has_function_privilege('public',%s,'EXECUTE')",
            (FUNCTION, FUNCTION, FUNCTION, FUNCTION),
        ).fetchone()
    assert "FOR UPDATE OF task" in definition
    assert definer is True
    assert config == ["search_path=pg_catalog, public"]
    assert privileges == (True, True, False, False)

    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (FUNCTION,),
        ).fetchone()[0] is None
    _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (FUNCTION,),
        ).fetchone()[0] is not None


def test_mid_batch_failure_rolls_back_all_runtime_ownership(database: str) -> None:
    _install_predecessors(database)
    _apply(database, MIGRATION)
    _set_ready(database)
    task_ids, batch_id, anchor = _seed_prepared_batch(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
          CREATE TABLE test_atomic_readiness_calls(call_count INTEGER NOT NULL);
          INSERT INTO test_atomic_readiness_calls VALUES(0);
          CREATE OR REPLACE FUNCTION _agent_runtime_media_owner_readiness_v1()
          RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY DEFINER
          SET search_path=pg_catalog,public AS $$
          DECLARE current_call INTEGER;
          BEGIN
            UPDATE test_atomic_readiness_calls SET call_count=call_count+1
             RETURNING call_count INTO current_call;
            RETURN jsonb_build_object(
              'ready',current_call=1,'state_version',current_call
            );
          END $$;
        """)

    receipt = _submit(database, batch_id, anchor)

    assert receipt == {
        "outcome": "media_not_ready", "runtime_owned": False,
        "readiness_revision": 2, "results": [],
    }
    assert _ownership(database, task_ids) == (0, 0, 0)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT call_count FROM test_atomic_readiness_calls",
        ).fetchone()[0] == 0


def test_concurrent_batch_retry_is_idempotent(database: str) -> None:
    _install_predecessors(database)
    _apply(database, MIGRATION)
    _set_ready(database)
    task_ids, batch_id, anchor = _seed_prepared_batch(database)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(
            lambda _: _submit(database, batch_id, anchor, barrier), range(2),
        ))

    assert {receipt["outcome"] for receipt in receipts} == {
        "created", "already_exists",
    }
    assert all(receipt["runtime_owned"] is True for receipt in receipts)
    assert all(len(receipt["results"]) == 2 for receipt in receipts)
    assert _ownership(database, task_ids) == (2, 2, 2)
