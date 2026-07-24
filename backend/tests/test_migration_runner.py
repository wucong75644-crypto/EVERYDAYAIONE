"""Migration ledger runner contracts."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import migration_runner
from scripts.migration_runner import (
    Migration,
    MigrationError,
    _record_failure,
    _database_url,
    apply_pending,
    baseline_through,
    discover_migrations,
    main,
    run,
    validate_ledger,
)


def _migration(identity: str, checksum: str = "a" * 64) -> Migration:
    return Migration(identity, Path(identity), checksum, None)


def test_discovery_uses_full_filename_and_preserves_duplicate_prefixes(
    tmp_path: Path,
) -> None:
    (tmp_path / "rollback").mkdir()
    (tmp_path / "105_b.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "105_a.sql").write_text("SELECT 1;", encoding="utf-8")

    migrations = discover_migrations(tmp_path)

    assert [item.identity for item in migrations] == ["105_a.sql", "105_b.sql"]
    assert migrations[0].checksum != migrations[1].checksum


def test_discovery_associates_exact_rollback_filename(tmp_path: Path) -> None:
    rollback = tmp_path / "rollback"
    rollback.mkdir()
    (tmp_path / "150_change.sql").write_text("SELECT 1;", encoding="utf-8")
    (rollback / "150_change_rollback.sql").write_text("SELECT 1;", encoding="utf-8")

    [migration] = discover_migrations(tmp_path)

    assert migration.rollback_identity == "150_change_rollback.sql"


def test_discovery_includes_legacy_python_migrations(tmp_path: Path) -> None:
    (tmp_path / "rollback").mkdir()
    (tmp_path / "024_legacy.py").write_text("print('legacy')", encoding="utf-8")

    [migration] = discover_migrations(tmp_path)

    assert migration.identity == "024_legacy.py"


def test_validation_rejects_checksum_drift() -> None:
    migration = _migration("100_change.sql")
    rows = {
        migration.identity: {
            "checksum_sha256": "b" * 64,
            "status": "applied",
            "execution_kind": "migration",
        }
    }

    with pytest.raises(MigrationError, match="checksum drift"):
        validate_ledger([migration], rows)


def test_validation_rejects_failed_and_unknown_history() -> None:
    migration = _migration("100_change.sql")
    failed = {
        migration.identity: {
            "checksum_sha256": migration.checksum,
            "status": "failed",
            "execution_kind": "migration",
        }
    }
    with pytest.raises(MigrationError, match="requires reconciliation"):
        validate_ledger([migration], failed)
    with pytest.raises(MigrationError, match="missing from repository"):
        validate_ledger([migration], {"removed.sql": failed[migration.identity]})


def test_validation_returns_only_pending_in_order() -> None:
    first = _migration("100_first.sql")
    second = _migration("101_second.sql", "b" * 64)
    rows = {
        first.identity: {
            "checksum_sha256": first.checksum,
            "status": "applied",
            "execution_kind": "baseline",
        }
    }

    assert validate_ledger([first, second], rows) == [second]


def test_baseline_requires_known_boundary_and_empty_ledger() -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
    migrations = [_migration("100_first.sql")]

    with pytest.raises(MigrationError, match="unknown baseline boundary"):
        baseline_through(connection, migrations, "missing.sql", "tester")

    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        {"identity": "existing.sql"}
    ]
    with pytest.raises(MigrationError, match="empty ledger"):
        baseline_through(connection, migrations, "100_first.sql", "tester")


def test_apply_rejects_non_sql_and_missing_rollback_before_execution() -> None:
    connection = MagicMock()
    legacy = Migration("024_legacy.py", Path("024_legacy.py"), "a" * 64, None)
    with pytest.raises(MigrationError, match="explicitly baselined"):
        apply_pending(connection, [legacy], "tester")

    migration = _migration("150_change.sql")
    with pytest.raises(MigrationError, match="missing rollback"):
        apply_pending(connection, [migration], "tester")
    connection.transaction.assert_not_called()


def test_apply_executes_sql_and_records_success_in_same_transaction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "150_change.sql"
    path.write_text("SELECT 1;", encoding="utf-8")
    migration = Migration(
        path.name, path, "a" * 64, "150_change_rollback.sql"
    )
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    apply_pending(connection, [migration], "tester")

    assert cursor.execute.call_count == 2
    assert cursor.execute.call_args_list[0].args == ("SELECT 1;",)
    ledger_args = cursor.execute.call_args_list[1].args[1]
    assert ledger_args == (
        migration.identity,
        migration.checksum,
        migration.rollback_identity,
        "tester",
    )


def test_record_failure_truncates_error_and_commits() -> None:
    migration = _migration("150_change.sql")
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    _record_failure(connection, migration, "tester", ValueError("x" * 3000))

    params = cursor.execute.call_args.args[1]
    assert params[:4] == (
        migration.identity,
        migration.checksum,
        None,
        "tester",
    )
    assert len(params[4]) == migration_runner.ERROR_LIMIT
    connection.commit.assert_called_once()


def test_apply_rolls_back_records_failure_and_closes() -> None:
    migration = Migration(
        "150_change.sql",
        Path("150_change.sql"),
        "a" * 64,
        "150_change_rollback.sql",
    )
    connection = MagicMock()

    with (
        patch.object(Path, "read_text", side_effect=RuntimeError("broken SQL")),
        patch.object(migration_runner, "_record_failure") as record_failure,
        pytest.raises(MigrationError, match="migration failed"),
    ):
        apply_pending(connection, [migration], "tester")

    connection.rollback.assert_called_once()
    record_failure.assert_called_once()
    assert record_failure.call_args.args[:3] == (connection, migration, "tester")


def test_baseline_records_every_migration_through_boundary() -> None:
    first = _migration("100_first.sql")
    second = Migration(
        "101_second.sql", Path("101_second.sql"), "b" * 64, "101_rollback.sql"
    )
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    baseline_through(
        connection, [first, second], second.identity, "operator"
    )

    assert cursor.execute.call_count == 3
    inserted = [call.args[1] for call in cursor.execute.call_args_list[1:]]
    assert inserted == [
        (first.identity, first.checksum, None, "operator"),
        (second.identity, second.checksum, second.rollback_identity, "operator"),
    ]


def test_run_always_unlocks_when_validation_fails() -> None:
    bootstrap = _migration(migration_runner.LEDGER_IDENTITY)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    with (
        patch.object(migration_runner, "discover_migrations", return_value=[bootstrap]),
        patch.object(migration_runner, "_bootstrap"),
        patch.object(migration_runner, "_ledger_rows", return_value={}),
        patch.object(
            migration_runner,
            "validate_ledger",
            side_effect=MigrationError("invalid"),
        ),
        pytest.raises(MigrationError, match="invalid"),
    ):
        run(connection, "check", "tester")

    lock_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("pg_advisory_lock" in sql for sql in lock_sql)
    assert any("pg_advisory_unlock" in sql for sql in lock_sql)
    connection.commit.assert_called_once()


def test_run_baseline_requires_through_and_delegates() -> None:
    bootstrap = _migration(migration_runner.LEDGER_IDENTITY)
    connection = MagicMock()

    with (
        patch.object(migration_runner, "discover_migrations", return_value=[bootstrap]),
        patch.object(migration_runner, "_bootstrap"),
        pytest.raises(MigrationError, match="requires --through"),
    ):
        run(connection, "baseline", "tester")

    with (
        patch.object(migration_runner, "discover_migrations", return_value=[bootstrap]),
        patch.object(migration_runner, "_bootstrap"),
        patch.object(migration_runner, "baseline_through") as baseline,
    ):
        assert run(
            connection,
            "baseline",
            "tester",
            through=bootstrap.identity,
        ) == []
    baseline.assert_called_once_with(
        connection, [bootstrap], bootstrap.identity, "tester"
    )


def test_main_rejects_unsafe_baseline_and_missing_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    with patch("sys.argv", ["migration_runner.py", "baseline", "--through", "x"]):
        assert main() == 2
    with (
        patch("sys.argv", ["migration_runner.py", "check"]),
        patch.object(migration_runner, "_database_url", return_value=None),
    ):
        assert main() == 2


def test_database_url_uses_migration_environment_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime")
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", "postgresql://migrator",
    )

    assert _database_url() == "postgresql://migrator"

    monkeypatch.delenv("MIGRATION_DATABASE_URL")
    assert _database_url() is None


def test_runner_adds_backend_root_to_import_path() -> None:
    assert str(migration_runner.ROOT) in migration_runner.sys.path


def test_main_connects_runs_and_prints_pending(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://example")
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    with (
        patch("sys.argv", ["migration_runner.py", "plan", "--applied-by", "ci"]),
        patch.object(
            migration_runner.psycopg,
            "connect",
            return_value=connection_context,
        ),
        patch.object(
            migration_runner,
            "run",
            return_value=["150_change.sql"],
        ) as runner,
    ):
        assert main() == 0

    assert capsys.readouterr().out.strip() == "150_change.sql"
    runner.assert_called_once_with(connection, "plan", "ci", through=None)


def test_main_returns_failure_for_migration_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://example")
    connection_context = MagicMock()
    with (
        patch("sys.argv", ["migration_runner.py", "apply"]),
        patch.object(
            migration_runner.psycopg,
            "connect",
            return_value=connection_context,
        ),
        patch.object(
            migration_runner,
            "run",
            side_effect=MigrationError("blocked"),
        ),
    ):
        assert main() == 1
    assert "blocked" in capsys.readouterr().err
