from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from services.agent.runtime.catalog.data_read_release import (
    build_data_read_snapshot,
)
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_erp_read_configuration_postgres_external import (
    _prepare as _prepare_erp_read,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_58_agent_runtime_data_read_release.sql"
ROLLBACK = ROOT / "migrations/rollback/227_58_agent_runtime_data_read_release_rollback.sql"


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection, connection.transaction():
        connection.execute(path.read_text(encoding="utf-8"))


def test_data_read_release_apply_readback_rollback_reapply(database: str) -> None:
    _prepare_erp_read(database)
    _apply(database, MIGRATION)
    expected = build_data_read_snapshot(
        scope="user", channel="web", gate_state="enabled",
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        row = connection.execute(
            "SELECT c.catalog_document,d.definition_revision,"
            "e.toolset_document FROM agent_runtime_catalog_facts c "
            "JOIN agent_runtime_definition_facts d USING(catalog_revision) "
            "JOIN agent_runtime_effective_toolset_facts e USING(catalog_revision) "
            "WHERE d.definition_revision='v6' AND e.scope_kind='user' "
            "AND e.channel='web' AND e.gate_state='enabled'",
        ).fetchone()
        assert row is not None
        assert row[0] == expected.catalog_document
        assert row[1] == "v6"
        assert row[2]["toolset_hash"] == expected.toolset_hash
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_definition_facts "
            "WHERE definition_revision='v6'",
        ).fetchone()[0] == 0
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)
