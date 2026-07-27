"""管理员 psql 安全启动器合同测试。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy/run-psql-admin.py"
SPEC = spec_from_file_location("run_psql_admin", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_environment_decodes_credentials_without_url_argument(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.9")
    monkeypatch.setenv("PGPASSFILE", "/tmp/untrusted")
    monkeypatch.setenv("PGSSLMODE", "disable")
    environment = MODULE.build_psql_environment(
        "postgresql://admin:p%40ss@db.internal:6543/everydayai"
        "?sslmode=require&connect_timeout=5",
    )

    assert environment["PGHOST"] == "db.internal"
    assert environment["PGPORT"] == "6543"
    assert environment["PGDATABASE"] == "everydayai"
    assert environment["PGUSER"] == "admin"
    assert environment["PGPASSWORD"] == "p@ss"
    assert environment["PGSSLMODE"] == "require"
    assert environment["PGCONNECT_TIMEOUT"] == "5"
    assert "PGHOSTADDR" not in environment
    assert "PGPASSFILE" not in environment


def test_build_environment_decodes_unix_socket_host() -> None:
    environment = MODULE.build_psql_environment(
        "postgresql://postgres@%2Fvar%2Frun%2Fpostgresql/everydayai"
    )

    assert environment["PGHOST"] == "/var/run/postgresql"
    assert environment["PGUSER"] == "postgres"
    assert "PGPASSWORD" not in environment


@pytest.mark.parametrize(
    "database_url",
    (
        "https://admin@db/everydayai",
        "postgresql://db/everydayai",
        "postgresql://admin@db/",
        "postgresql://admin@db/everydayai#fragment",
        "postgresql://admin@db/everydayai\nunsafe",
        "postgresql://admin@db/everydayai?sslmode=require&sslmode=disable",
        "postgresql://admin@db/everydayai?unknown=value",
    ),
)
def test_build_environment_rejects_unsafe_url(database_url: str) -> None:
    with pytest.raises(ValueError):
        MODULE.build_psql_environment(database_url)


def test_main_rejects_missing_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TENANT_DB_ADMIN_URL", raising=False)

    assert MODULE.main() == 1
    assert "缺少 TENANT_DB_ADMIN_URL" in capsys.readouterr().err


def test_main_rejects_missing_psql(monkeypatch, capsys) -> None:
    monkeypatch.setenv(
        "TENANT_DB_ADMIN_URL",
        "postgresql://admin@db/everydayai",
    )
    monkeypatch.setattr(MODULE.shutil, "which", lambda _: None)

    assert MODULE.main() == 1
    assert "未找到 psql" in capsys.readouterr().err


def test_main_rejects_invalid_url(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TENANT_DB_ADMIN_URL", "not-a-database-url")
    monkeypatch.setattr(MODULE.shutil, "which", lambda _: "/bin/psql")

    assert MODULE.main() == 1
    assert "不是完整 PostgreSQL URL" in capsys.readouterr().err


def test_main_executes_psql_with_component_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "TENANT_DB_ADMIN_URL",
        "postgresql://admin:secret@db:6543/everydayai",
    )
    monkeypatch.setattr(MODULE.shutil, "which", lambda _: "/bin/psql")
    monkeypatch.setattr(MODULE.sys, "argv", ["runner", "--no-psqlrc"])
    captured = {}

    def fake_exec(file, args, environment) -> None:
        captured.update(file=file, args=args, environment=environment)

    monkeypatch.setattr(MODULE.os, "execvpe", fake_exec)

    assert MODULE.main() == 1
    assert captured["file"] == "psql"
    assert captured["args"] == ["psql", "--no-psqlrc"]
    assert captured["environment"]["PGHOST"] == "db"
    assert captured["environment"]["PGPASSWORD"] == "secret"
