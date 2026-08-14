"""Legacy configuration migration CLI safety contract tests."""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock, patch
import subprocess
import sys

from scripts import migrate_legacy_configuration as script
from services.configuration.legacy_import import (
    LegacyImportItem,
    LegacyImportPlan,
    LegacyImportPlanError,
)


IMPORT_ID = "00000000-0000-0000-0000-00000000a161"
ORG_ID = "00000000-0000-0000-0000-000000000010"


def _plan() -> LegacyImportPlan:
    return LegacyImportPlan(
        IMPORT_ID,
        (
            LegacyImportItem(
                ORG_ID,
                "v1",
                "wecom.corp_id",
                "corp-1",
                None,
            ),
        ),
    )


def _snapshot(can_migrate: bool = True):
    item = SimpleNamespace(status="ready")
    report = SimpleNamespace(
        can_migrate=can_migrate,
        items=(item,),
        unknown_keys=(),
    )
    return SimpleNamespace(
        organizations=(),
        preflight_reports=MappingProxyType({ORG_ID: report}),
    )


def test_default_mode_is_dry_run_and_never_opens_migrator_connection(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv(
        script.SOURCE_DATABASE_ENV,
        "postgresql://source",
    )
    monkeypatch.delenv(script.MIGRATOR_DATABASE_ENV, raising=False)
    monkeypatch.setattr("sys.argv", ["migrate"])

    with (
        patch.object(script, "_read_snapshot", return_value=_snapshot()),
        patch.object(script, "_build_plan", return_value=_plan()),
        patch.object(script.psycopg, "connect") as connect,
    ):
        assert script.main() == 0

    connect.assert_not_called()
    output = capsys.readouterr().out
    assert '"mode": "dry-run"' in output
    assert f"APPLY:{IMPORT_ID}" in output


def test_blocked_preflight_stops_before_plan_or_apply(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv(
        script.SOURCE_DATABASE_ENV,
        "postgresql://source",
    )
    monkeypatch.setattr("sys.argv", ["migrate"])

    with (
        patch.object(
            script,
            "_read_snapshot",
            return_value=_snapshot(False),
        ),
        patch.object(script, "_build_plan") as build_plan,
        patch.object(script.psycopg, "connect") as connect,
    ):
        assert script.main() == 1

    build_plan.assert_not_called()
    connect.assert_not_called()
    assert '"can_migrate": false' in capsys.readouterr().out


def test_apply_requires_import_id_and_migrator_url(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv(
        script.SOURCE_DATABASE_ENV,
        "postgresql://source",
    )
    monkeypatch.setattr("sys.argv", ["migrate", "--apply"])
    assert script.main() == 2
    assert "--import-id is required" in capsys.readouterr().err

    monkeypatch.setattr(
        "sys.argv",
        ["migrate", "--apply", "--import-id", IMPORT_ID],
    )
    monkeypatch.delenv(script.MIGRATOR_DATABASE_ENV, raising=False)
    with (
        patch.object(script, "_read_snapshot", return_value=_snapshot()),
        patch.object(script, "_build_plan", return_value=_plan()),
    ):
        assert script.main() == 2
    assert script.MIGRATOR_DATABASE_ENV in capsys.readouterr().err


def test_apply_uses_separate_migrator_connection(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv(
        script.SOURCE_DATABASE_ENV,
        "postgresql://source",
    )
    monkeypatch.setenv(
        script.MIGRATOR_DATABASE_ENV,
        "postgresql://migrator",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "migrate",
            "--apply",
            "--import-id",
            IMPORT_ID,
            "--confirm",
            f"APPLY:{IMPORT_ID}",
        ],
    )
    connection = MagicMock()
    connection.__enter__.return_value = connection
    result = {
        "import_id": IMPORT_ID,
        "imported_count": 1,
        "version": 1,
    }
    with (
        patch.object(script, "_read_snapshot", return_value=_snapshot()),
        patch.object(script, "_build_plan", return_value=_plan()),
        patch.object(
            script.psycopg,
            "connect",
            return_value=connection,
        ) as connect,
        patch.object(
            script,
            "apply_legacy_import",
            return_value=result,
        ) as apply,
    ):
        assert script.main() == 0

    connect.assert_called_once_with("postgresql://migrator")
    apply.assert_called_once_with(
        connection,
        _plan(),
        confirmation=f"APPLY:{IMPORT_ID}",
    )
    assert '"mode": "apply"' in capsys.readouterr().out


def test_missing_source_url_is_usage_error(monkeypatch, capsys) -> None:
    monkeypatch.delenv(script.SOURCE_DATABASE_ENV, raising=False)
    monkeypatch.setattr("sys.argv", ["migrate"])

    assert script.main() == 2
    assert script.SOURCE_DATABASE_ENV in capsys.readouterr().err


def test_read_snapshot_uses_psycopg_context_and_export_adapter(
    monkeypatch,
) -> None:
    monkeypatch.setenv(script.LEGACY_KEY_ENV, "legacy-key")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    with (
        patch.object(
            script.psycopg,
            "connect",
            return_value=connection,
        ) as connect,
        patch.object(
            script,
            "read_legacy_import_snapshot",
            return_value=_snapshot(),
        ) as read_snapshot,
    ):
        assert script._read_snapshot("postgresql://source") == _snapshot()

    connect.assert_called_once_with("postgresql://source")
    read_snapshot.assert_called_once_with(
        connection,
        global_encrypt_key="legacy-key",
    )


def test_apply_rejects_reused_source_and_migrator_url(
    monkeypatch,
    capsys,
) -> None:
    shared_url = "postgresql://shared"
    monkeypatch.setenv(script.SOURCE_DATABASE_ENV, shared_url)
    monkeypatch.setenv(script.MIGRATOR_DATABASE_ENV, shared_url)
    monkeypatch.setattr(
        "sys.argv",
        ["migrate", "--apply", "--import-id", IMPORT_ID],
    )
    with (
        patch.object(script, "_read_snapshot", return_value=_snapshot()),
        patch.object(script, "_build_plan", return_value=_plan()),
        patch.object(script.psycopg, "connect") as connect,
    ):
        assert script.main() == 2

    connect.assert_not_called()
    assert "must differ" in capsys.readouterr().err


def test_build_plan_wires_kek_material_and_snapshot() -> None:
    snapshot = _snapshot()
    provider = MagicMock()
    material = MagicMock()
    planner = MagicMock()
    planner.build.return_value = _plan()
    with (
        patch.object(
            script.LocalKEKProvider,
            "from_environment",
            return_value=provider,
        ),
        patch.object(
            script,
            "SecretMaterialService",
            return_value=material,
        ) as material_type,
        patch.object(
            script,
            "LegacyImportPlanner",
            return_value=planner,
        ) as planner_type,
    ):
        assert script._build_plan(snapshot, IMPORT_ID) == _plan()

    material_type.assert_called_once_with(provider)
    planner_type.assert_called_once_with(material)
    planner.build.assert_called_once_with(
        import_id=IMPORT_ID,
        organizations=snapshot.organizations,
        preflight_reports=snapshot.preflight_reports,
    )


def test_stable_planning_error_is_reported_without_traceback(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv(
        script.SOURCE_DATABASE_ENV,
        "postgresql://source",
    )
    monkeypatch.setattr("sys.argv", ["migrate"])
    with patch.object(
        script,
        "_read_snapshot",
        side_effect=LegacyImportPlanError("LEGACY_IMPORT_BLOCKED"),
    ):
        assert script.main() == 1

    assert capsys.readouterr().err.strip() == "LEGACY_IMPORT_BLOCKED"


def test_script_help_runs_from_project_root() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(script.BACKEND_ROOT / "scripts/migrate_legacy_configuration.py"),
            "--help",
        ],
        cwd=script.BACKEND_ROOT.parent,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--apply" in result.stdout
