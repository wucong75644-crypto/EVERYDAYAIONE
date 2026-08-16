from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg
import pytest

from services.agent.runtime.catalog.batch_media_release import (
    build_batch_media_snapshot,
)
from scripts.generate_agent_runtime_batch_media_seed import main as generate_seed
from tests.test_agent_runtime_ar17_postgres_external import database


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/230_05_agent_runtime_catalog_batch_media_v12.sql"
ROLLBACK = ROOT / "migrations/rollback/230_05_agent_runtime_catalog_batch_media_v12_rollback.sql"


def test_batch_media_release_uses_full_catalog_and_standard_image_ingress() -> None:
    snapshot = build_batch_media_snapshot(
        scope="user", channel="web", gate_state="enabled",
    )
    catalog_names = {
        tool.canonical_name for tool in snapshot.receipt.catalog.definitions()
    }
    toolset_names = {tool.canonical_name for tool in snapshot.toolset.definitions}
    assert len(catalog_names) == 42
    assert {"generate_image", "image_agent"}.issubset(catalog_names)
    assert "generate_image" in toolset_names
    assert "image_agent" not in toolset_names


def test_batch_media_release_is_disabled_and_rollback_is_guarded() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")
    match = re.search(
        r"catalog_doc JSONB := \$seed\$(.*?)\$seed\$::JSONB",
        sql, re.DOTALL,
    )
    assert match is not None
    stored = json.loads(match.group(1))
    expected = build_batch_media_snapshot(
        scope="user", channel="web", gate_state="enabled",
    ).receipt.document()
    assert stored == expected
    assert sql.count("FALSE, TRUE") == 8
    assert sql.count("FALSE,TRUE") == 2
    assert "definition_revision='v12'" in rollback
    assert "agent_runs" in rollback
    assert "agent_actions" in rollback
    assert "agent_runtime_tenant_provider_bindings" in rollback
    assert "catalog_revision=rev AND definition_revision='v12'" in rollback
    assert "ON CONFLICT (catalog_revision) DO NOTHING" in sql
    assert "ON CONFLICT (catalog_revision,tool_name) DO NOTHING" in sql


def test_batch_media_release_generator_is_byte_deterministic(tmp_path: Path) -> None:
    generated = tmp_path / MIGRATION.name
    generate_seed(generated)
    assert generated.read_bytes() == MIGRATION.read_bytes()


@pytest.mark.external
def test_batch_media_release_apply_readback_rollback_reapply(database: str) -> None:
    dependencies = (
        "227_01_agent_runtime_production_closure.sql",
        "227_03_agent_runtime_tenant_provider_bindings.sql",
    )
    with psycopg.connect(database) as connection:
        for name in dependencies:
            connection.execute((ROOT / "migrations" / name).read_text())
        connection.execute(MIGRATION.read_text())
        connection.commit()

    _assert_release_readback(database)
    with psycopg.connect(database) as connection:
        connection.execute(ROLLBACK.read_text())
        connection.commit()
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_definition_facts "
            "WHERE definition_revision='v12'",
        ).fetchone()[0] == 0
        connection.execute(MIGRATION.read_text())
        connection.commit()
    _assert_release_readback(database)


def _assert_release_readback(database: str) -> None:
    with psycopg.connect(database) as connection:
        definition = connection.execute(
            "SELECT catalog_revision,enabled_for_new_ingress,recoverable "
            "FROM agent_runtime_definition_facts "
            "WHERE agent_key='everydayai-default' AND definition_revision='v12'",
        ).fetchone()
        assert definition is not None
        assert definition[1:] == (False, True)
        catalog = connection.execute(
            "SELECT jsonb_array_length(catalog_document->'tools'),"
            "enabled_for_new_ingress,recoverable FROM agent_runtime_catalog_facts "
            "WHERE catalog_revision=%s", (definition[0],),
        ).fetchone()
        assert catalog == (42, False, True)
        names = connection.execute(
            "SELECT toolset_document->'tool_names' FROM "
            "agent_runtime_effective_toolset_facts WHERE definition_revision='v12' "
            "AND scope_kind='user' AND channel='web' AND gate_state='enabled'",
        ).fetchone()[0]
        assert "generate_image" in names
        assert "image_agent" not in names
