from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    CONVERSATION,
    ORG,
    USER,
    _connect,
    database,
)
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare,
    _prepare_legacy_schema,
    _seed_batch,
)
from tests.test_agent_runtime_media_manifest_readback_postgres_external import (
    _prepare_asset_schema,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql"
MIGRATION = ROOT / "migrations/228_08c_agent_runtime_media_worker_scope.sql"
ROLLBACK = ROOT / "migrations/rollback/228_08c_agent_runtime_media_worker_scope_rollback.sql"
FUNCTION = "worker_discover_media_tasks(integer)"


def _apply(database_url: str, path: Path) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(path.read_text(encoding="utf-8"))


def _definition(database_url: str) -> str:
    with psycopg.connect(database_url) as connection:
        return connection.execute(
            "SELECT pg_get_functiondef(%s::regprocedure)",
            (FUNCTION,),
        ).fetchone()[0]


def _discover(database_url: str, access_kind: str | None) -> list[dict[str, object]]:
    with _connect(database_url, "everydayai_worker") as connection:
        if access_kind is not None:
            connection.execute(
                "SELECT set_config('app.access_kind',%s,false)",
                (access_kind,),
            )
        return connection.execute(
            "SELECT worker_discover_media_tasks(100)",
        ).fetchone()[0]


def _assert_denied(database_url: str, access_kind: str | None) -> None:
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege,
        match="MEDIA_WORKER_ROLE_SCOPE_MISMATCH",
    ):
        _discover(database_url, access_kind)


def _task_ids(rows: list[dict[str, object]]) -> set[str]:
    return {str(row["id"]) for row in rows}


def test_worker_scope_apply_readback_rollback_reapply(database: str) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    prepared = _prepare(database, batch.attempts[0])
    runtime_task_id = str(prepared["binding"]["task_id"])

    legacy_task_id = uuid4()
    inactive_task_id = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,"
            "model_id,delivery_context) VALUES"
            "(%s,%s,%s,%s,'image','pending','legacy-image','{}'),"
            "(%s,%s,%s,%s,'video','running','legacy-video','{}')",
            (
                legacy_task_id, USER, ORG, CONVERSATION,
                inactive_task_id, USER, ORG, CONVERSATION,
            ),
        )
        connection.execute(BASELINE.read_text(encoding="utf-8"))

    baseline_definition = _definition(database)
    _apply(database, MIGRATION)

    _assert_denied(database, None)
    _assert_denied(database, "runtime")
    discovered = _task_ids(_discover(database, "worker"))
    assert str(legacy_task_id) in discovered
    assert str(inactive_task_id) in discovered
    assert runtime_task_id not in discovered

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE organizations SET status='suspended' WHERE id=%s",
            (ORG,),
        )
    suspended = _task_ids(_discover(database, "worker"))
    assert str(legacy_task_id) not in suspended
    assert str(inactive_task_id) not in suspended
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE organizations SET status='active' WHERE id=%s",
            (ORG,),
        )

    with psycopg.connect(database) as connection:
        definition, is_definer, search_path = connection.execute(
            "SELECT pg_get_functiondef(procedure.oid),procedure.prosecdef,"
            "procedure.proconfig FROM pg_proc procedure "
            "WHERE procedure.oid=%s::regprocedure",
            (FUNCTION,),
        ).fetchone()
        worker_execute, runtime_execute, public_execute = connection.execute(
            "SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE'),"
            "has_function_privilege('everydayai_runtime',%s,'EXECUTE'),"
            "has_function_privilege('public',%s,'EXECUTE')",
            (FUNCTION, FUNCTION, FUNCTION),
        ).fetchone()
    assert "session_user <> 'everydayai_worker'" in definition
    assert "current_setting('app.access_kind', TRUE)" in definition
    assert is_definer is True
    assert search_path == ["search_path=pg_catalog, public"]
    assert (worker_execute, runtime_execute, public_execute) == (True, False, False)

    _apply(database, ROLLBACK)
    assert _definition(database) == baseline_definition
    assert str(legacy_task_id) in _task_ids(_discover(database, "runtime"))

    _apply(database, MIGRATION)
    _assert_denied(database, None)
    _assert_denied(database, "runtime")
    reapplied = _task_ids(_discover(database, "worker"))
    assert str(legacy_task_id) in reapplied
    assert runtime_task_id not in reapplied
