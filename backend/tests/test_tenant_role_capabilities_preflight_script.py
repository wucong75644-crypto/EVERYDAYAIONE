"""Tenant role capability preflight execution tests."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/preflight/tenant-role-capabilities.sh"
WRAPPER = ROOT / "deploy/preflight-tenant-cutover.sh"
ISOLATED_ROLES = (
    "everydayai_config_import_reader",
    "everydayai_migrator",
    "everydayai_runtime",
    "everydayai_wecom_runtime",
    "everydayai_worker",
    "everydayai_sync",
)


@pytest.fixture(scope="module")
def local_postgres(tmp_path_factory):
    binaries = {
        name: shutil.which(name)
        for name in ("initdb", "postgres", "pg_isready", "psql")
    }
    if not all(binaries.values()):
        pytest.skip("local PostgreSQL binaries are unavailable")
    data_dir = tmp_path_factory.mktemp("role-preflight-postgres-data")
    subprocess.run(
        [
            binaries["initdb"], "-D", str(data_dir), "-A", "trust",
            "-U", "postgres", "--no-locale",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])
    process = subprocess.Popen(
        [
            binaries["postgres"], "-D", str(data_dir), "-k", "/tmp",
            "-h", "127.0.0.1", "-p", port,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    connection = [
        binaries["psql"], "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
        "-p", port, "-U", "postgres", "-d", "postgres",
    ]
    try:
        for _ in range(50):
            ready = subprocess.run(
                [
                    binaries["pg_isready"], "-h", "127.0.0.1", "-p", port,
                    "-U", "postgres",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            if process.poll() is not None:
                pytest.fail("temporary PostgreSQL exited during startup")
            time.sleep(0.1)
        else:
            pytest.fail("temporary PostgreSQL did not become ready")
        yield {
            "connection": connection,
            "database_url": f"postgresql://postgres@127.0.0.1:{port}/postgres",
        }
    finally:
        process.terminate()
        process.wait(timeout=10)


def _prepare_role_scenario(
    connection: list[str],
    roles: tuple[str, ...],
    grant_runtime: bool,
) -> None:
    statements = [
        "DROP FUNCTION IF EXISTS public.enqueue_generation_turn("
        "jsonb,uuid,uuid,text,jsonb,uuid);",
        *(f"DROP ROLE IF EXISTS {role};" for role in ISOLATED_ROLES),
        "DROP ROLE IF EXISTS everydayai_owner;",
        *(f"CREATE ROLE {role} LOGIN;" for role in roles),
    ]
    if len(roles) == len(ISOLATED_ROLES):
        statements.extend((
            "CREATE ROLE everydayai_owner NOLOGIN;",
            "GRANT everydayai_owner TO everydayai_migrator;",
        ))
    statements.extend((
        "CREATE FUNCTION public.enqueue_generation_turn("
        "jsonb,uuid,uuid,text,jsonb,uuid) RETURNS integer "
        "LANGUAGE sql AS $$ SELECT 1 $$;",
        "REVOKE ALL ON FUNCTION public.enqueue_generation_turn("
        "jsonb,uuid,uuid,text,jsonb,uuid) FROM PUBLIC;",
    ))
    if grant_runtime:
        statements.append(
            "GRANT EXECUTE ON FUNCTION public.enqueue_generation_turn("
            "jsonb,uuid,uuid,text,jsonb,uuid) TO everydayai_runtime;"
        )
    subprocess.run(
        connection,
        input="\n".join(statements),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("roles", "grant_runtime", "expected_error"),
    (
        (ISOLATED_ROLES, False, None),
        (
            ISOLATED_ROLES,
            True,
            "TENANT_CUTOVER_RUNTIME_LEGACY_ENQUEUE_UNEXPECTED",
        ),
        ((), False, None),
        (("everydayai_runtime",), False, "TENANT_CUTOVER_ROLE_SET_PARTIAL"),
    ),
)
def test_role_preflight_executes_real_postgres_contract(
    local_postgres, roles, grant_runtime, expected_error,
) -> None:
    _prepare_role_scenario(
        local_postgres["connection"], roles, grant_runtime,
    )
    environment = {
        **os.environ,
        "TENANT_DB_ADMIN_URL": local_postgres["database_url"],
        "LEGACY_DATABASE_OWNER": "everydayai",
    }

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    if expected_error is None:
        assert result.returncode == 0, result.stderr
        assert "Runtime 旧入队权限检查通过" in result.stdout
    else:
        assert result.returncode != 0
        assert expected_error in result.stderr


def _wrapper_fixture(tmp_path: Path) -> tuple[Path, Path]:
    wrapper = tmp_path / "preflight-tenant-cutover.sh"
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    preflight = tmp_path / "preflight"
    preflight.mkdir()
    log_path = tmp_path / "calls.log"
    for name in (
        "tenant-role-capabilities.sh",
        "tenant-core.sh",
        "admin-user-assets-capability.sh",
        "worker-control.sh",
    ):
        (preflight / name).write_text(
            "#!/bin/bash\n"
            f"echo '{name}' >> \"$CALL_LOG\"\n"
            f"if [ \"${{FAIL_STEP:-}}\" = '{name}' ]; then\n"
            "    echo 'sentinel failure' >&2\n"
            "    exit 23\n"
            "fi\n",
            encoding="utf-8",
        )
    return wrapper, log_path


def test_wrapper_calls_role_gate_once_before_core(tmp_path: Path) -> None:
    wrapper, log_path = _wrapper_fixture(tmp_path)

    result = subprocess.run(
        ["bash", str(wrapper)],
        env={**os.environ, "CALL_LOG": str(log_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "tenant-role-capabilities.sh",
        "tenant-core.sh",
        "admin-user-assets-capability.sh",
        "worker-control.sh",
    ]


def test_wrapper_stops_and_propagates_role_gate_failure(tmp_path: Path) -> None:
    wrapper, log_path = _wrapper_fixture(tmp_path)

    result = subprocess.run(
        ["bash", str(wrapper)],
        env={
            **os.environ,
            "CALL_LOG": str(log_path),
            "FAIL_STEP": "tenant-role-capabilities.sh",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 23
    assert "sentinel failure" in result.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "tenant-role-capabilities.sh",
    ]
