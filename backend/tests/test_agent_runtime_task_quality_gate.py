from pathlib import Path

from scripts.check_task_change import check_file


ROOT = Path(__file__).resolve().parents[2]
FROZEN = (
    ROOT / "backend/migrations/228_04_agent_runtime_media_action_bindings.sql",
    ROOT / "backend/migrations/228_05_agent_runtime_media_manifest_readback.sql",
)


def test_frozen_oversized_migrations_keep_exact_checksums() -> None:
    for migration in FROZEN:
        assert check_file(ROOT, migration) == []


def test_frozen_migration_checksum_drift_fails_closed(tmp_path: Path) -> None:
    migration = tmp_path / FROZEN[0].relative_to(ROOT)
    migration.parent.mkdir(parents=True)
    migration.write_bytes(FROZEN[0].read_bytes() + b"\n")
    failures = check_file(tmp_path, migration)
    assert any("frozen migration checksum drift" in item for item in failures)


def test_unlisted_oversized_migration_still_fails(tmp_path: Path) -> None:
    migration = tmp_path / "backend/migrations/999_quality_contract.sql"
    rollback = migration.parent / "rollback/999_quality_contract_rollback.sql"
    rollback.parent.mkdir(parents=True)
    migration.write_text("SELECT 1;\n" * 501, encoding="utf-8")
    rollback.write_text("SELECT 1;\n", encoding="utf-8")
    failures = check_file(tmp_path, migration)
    assert any("501 lines (max 500)" in item for item in failures)
    assert not any("missing rollback" in item for item in failures)


def test_missing_migration_rollback_still_fails(tmp_path: Path) -> None:
    migration = tmp_path / "backend/migrations/999_missing_rollback.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("SELECT 1;\n", encoding="utf-8")
    failures = check_file(tmp_path, migration)
    assert any("missing rollback" in item for item in failures)
