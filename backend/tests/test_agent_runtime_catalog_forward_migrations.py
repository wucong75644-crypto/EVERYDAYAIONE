from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
ROLLBACKS = MIGRATIONS / "rollback"


CURRENT_RELEASES = (
    ("230_01_agent_runtime_catalog_production_v8.sql", "v8"),
    ("230_02_agent_runtime_catalog_safe_read_v9.sql", "v9"),
    ("230_03_agent_runtime_catalog_erp_read_v10.sql", "v10"),
    ("230_04_agent_runtime_catalog_data_read_v11.sql", "v11"),
    ("230_05_agent_runtime_catalog_batch_media_v12.sql", "v12"),
    ("230_06_agent_runtime_catalog_image_v13.sql", "v13"),
)


def test_current_catalog_releases_use_global_definition_revisions() -> None:
    for filename, revision in CURRENT_RELEASES:
        sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
        assert f"'everydayai-default','{revision}'" in sql
        assert f'"revision":"{revision}"' in sql


def test_batch_media_release_is_safe_after_production_release() -> None:
    forward = (MIGRATIONS / CURRENT_RELEASES[4][0]).read_text(encoding="utf-8")
    rollback = (
        ROLLBACKS / "230_05_agent_runtime_catalog_batch_media_v12_rollback.sql"
    ).read_text(encoding="utf-8")
    assert "ON CONFLICT (catalog_revision) DO NOTHING" in forward
    assert "ON CONFLICT (catalog_revision,tool_name) DO NOTHING" in forward
    assert "catalog_revision=rev AND definition_revision='v12'" in rollback
    assert "definition_revision<>'v12'" in rollback


def test_production_rollback_rejects_shared_catalog_revision() -> None:
    rollback = (
        ROLLBACKS / "230_01_agent_runtime_catalog_production_v8_rollback.sql"
    ).read_text(encoding="utf-8")
    assert "definition_revision<>'v8'" in rollback
