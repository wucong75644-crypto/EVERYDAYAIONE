from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import (
    _connect,
    _settings,
    database,
)
from tests.test_agent_runtime_media_atomic_image_batch_postgres_external import (
    _apply,
    _ownership,
    _seed_prepared_batch,
    _set_ready,
    _submit,
)
from tests.test_agent_runtime_media_model_video_projection_fence_postgres_external import (
    _install as _install_through_08e2,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
SLOTS = ROOT / (
    "migrations/228_08f1_agent_runtime_media_prepared_image_batch_projection.sql"
)
SLOTS_ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08f1_agent_runtime_media_prepared_image_batch_projection_rollback.sql"
)
MIGRATION = ROOT / (
    "migrations/228_08f2_agent_runtime_media_atomic_image_batch_ownership.sql"
)
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08f2_agent_runtime_media_atomic_image_batch_ownership_rollback.sql"
)
V1 = (
    "submit_agent_runtime_media_image_batch_v1"
    "(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,text,text,text,"
    "text,text,text,jsonb)"
)
V2 = V1.replace("_v1", "_v2")


def _install_projection_stack(database_url: str) -> None:
    _install_through_08e2(database_url)
    for name in (
        "227_63_agent_runtime_chat_action_submission.sql",
        "227_67_agent_runtime_chat_action_catalog_fix.sql",
    ):
        _apply(database_url, ROOT / "migrations" / name)
    _apply(database_url, SLOTS)


def _candidate(database_url: str):
    task_ids, batch_id, anchor = _seed_prepared_batch(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE tasks SET delivery_context=delivery_context||%s "
            "WHERE id=ANY(%s)",
            (Jsonb({"channel": "web"}), task_ids),
        )
        connection.execute(
            "UPDATE messages SET generation_params=%s WHERE id=(SELECT "
            "assistant_message_id FROM tasks WHERE id=%s)",
            (Jsonb({"type": "image", "num_images": 2}), task_ids[0]),
        )
    return task_ids, batch_id, anchor


def _submit_v2(
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
            f"SELECT submit_agent_runtime_media_image_batch_v2({placeholders})",
            params,
        ).fetchone()[0]


def test_ownership_apply_rollback_reapply_and_acl(database: str) -> None:
    _install_projection_stack(database)
    _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        definition, definer, config = connection.execute(
            "SELECT pg_get_functiondef(procedure.oid),procedure.prosecdef,"
            "procedure.proconfig FROM pg_proc procedure "
            "WHERE procedure.oid=%s::regprocedure", (V2,),
        ).fetchone()
        privileges = connection.execute(
            "SELECT has_function_privilege('everydayai_runtime',%s,'EXECUTE'),"
            "has_function_privilege('everydayai_runtime',%s,'EXECUTE'),"
            "has_function_privilege('everydayai_worker',%s,'EXECUTE')",
            (V1, V2, V2),
        ).fetchone()
    assert "_agent_runtime_media_image_batch_ownership_v1" in definition
    assert definer is True
    assert config == ["search_path=pg_catalog, public"]
    assert privileges == (False, True, False)

    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s),"
            "has_function_privilege('everydayai_runtime',%s,'EXECUTE')",
            (V2, V1),
        ).fetchone() == (None, True)
    _apply(database, SLOTS_ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_prepared_image_batch_slots')",
        ).fetchone()[0] is None
    _apply(database, SLOTS)
    _apply(database, MIGRATION)


def test_migration_rejects_historical_partial_ownership(database: str) -> None:
    _install_projection_stack(database)
    _set_ready(database)
    task_ids, batch_id, anchor = _candidate(database)
    partial_anchor = (*anchor[:-1], [anchor[-1][0]])
    receipt = _submit(database, batch_id, partial_anchor)
    assert receipt["runtime_owned"] is True
    assert _ownership(database, task_ids) == (1, 1, 1)

    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="PARTIAL_OWNERSHIP_RECONCILE_REQUIRED",
    ):
        _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (V2,),
        ).fetchone()[0] is None


def test_runtime_partial_is_read_only_and_not_redispatched(database: str) -> None:
    _install_projection_stack(database)
    _apply(database, MIGRATION)
    _set_ready(database)
    task_ids, batch_id, anchor = _candidate(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(f"GRANT EXECUTE ON FUNCTION {V1} TO everydayai_runtime")
    partial_anchor = (*anchor[:-1], [anchor[-1][0]])
    _submit(database, batch_id, partial_anchor)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(f"REVOKE EXECUTE ON FUNCTION {V1} FROM everydayai_runtime")

    receipt = _submit_v2(database, batch_id, anchor)

    assert receipt["outcome"] == "partial_ownership"
    assert receipt["runtime_owned"] is False
    assert receipt["evidence_count"] == 1
    assert len(receipt["results"]) == 1
    assert _ownership(database, task_ids) == (1, 1, 1)


def test_concurrent_v2_retries_create_once_then_read_back(database: str) -> None:
    _install_projection_stack(database)
    _apply(database, MIGRATION)
    _set_ready(database)
    task_ids, batch_id, anchor = _candidate(database)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(
            lambda _: _submit_v2(database, batch_id, anchor, barrier), range(2),
        ))

    assert {receipt["outcome"] for receipt in receipts} == {
        "created", "already_exists",
    }
    assert all(receipt["runtime_owned"] is True for receipt in receipts)
    assert all(len(receipt["results"]) == 2 for receipt in receipts)
    assert _ownership(database, task_ids) == (2, 2, 2)
