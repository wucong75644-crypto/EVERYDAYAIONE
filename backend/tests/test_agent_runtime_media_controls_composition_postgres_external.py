"""Disposable 228.05 + 228.06 + 228.07 migration composition scaffold."""

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(
    ROOT / "migrations" / name for name in (
        "228_05_agent_runtime_media_manifest_readback.sql",
        "228_06_agent_runtime_media_projection.sql",
        "228_07_agent_runtime_media_controls.sql",
    )
)
ROLLBACKS = tuple(
    ROOT / "migrations/rollback" / name for name in (
        "228_07_agent_runtime_media_controls_rollback.sql",
        "228_06_agent_runtime_media_projection_rollback.sql",
        "228_05_agent_runtime_media_manifest_readback_rollback.sql",
    )
)


def _require_composed_lanes() -> None:
    missing = [path.name for path in (*MIGRATIONS, *ROLLBACKS) if not path.exists()]
    if missing:
        pytest.skip(
            "requires 228.05 commit 1b6bce8d and 228.06 commit 0cc37ef2: "
            + ", ".join(missing),
        )


def _execute_all(connection: psycopg.Connection, paths: tuple[Path, ...]) -> None:
    for path in paths:
        connection.execute(path.read_text(encoding="utf-8"))


def test_composed_media_migrations_apply_rollback_reapply(database: str) -> None:
    _require_composed_lanes()
    _prepare_legacy_schema(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        _execute_all(connection, MIGRATIONS)
        assert connection.execute(
            "SELECT to_regprocedure('retry_agent_runtime_media_slot_v1(uuid,uuid,integer,"
            "uuid,bigint,uuid,uuid,text,text,text)')",
        ).fetchone()[0] is not None
        _execute_all(connection, ROLLBACKS)
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_media_retry_lineage')",
        ).fetchone()[0] is None
        _execute_all(connection, MIGRATIONS)
        assert connection.execute("""
          SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
          WHERE oid='agent_runtime_media_retry_lineage'::regclass
        """).fetchone()[0] is True
