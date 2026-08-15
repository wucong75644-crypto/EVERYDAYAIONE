"""Real PostgreSQL apply, rollback, reapply, retention, and ACL contract for 229."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from uuid import uuid4

import psycopg
import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
PG_BIN_DIR = Path(os.getenv("TOOL_AUDIT_PG_BIN_DIR", "/opt/homebrew/bin"))


def _run(command: list[str], *, capture: bool = True) -> None:
    subprocess.run(
        command, check=True, capture_output=capture, text=capture, timeout=30,
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def database():
    if os.getenv("RUN_TOOL_AUDIT_DB_TEST") != "1":
        pytest.skip("RUN_TOOL_AUDIT_DB_TEST=1 required")
    port = _free_port()
    data_dir = Path(tempfile.mkdtemp(prefix="tool-audit-pg-", dir="/private/tmp"))
    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    initdb = str(PG_BIN_DIR / "initdb")
    pg_ctl = str(PG_BIN_DIR / "pg_ctl")
    try:
        _run([
            initdb, "-D", str(data_dir), "-U", "postgres",
            "--auth-host=trust", "--auth-local=trust",
        ])
        _run([
            pg_ctl, "-D", str(data_dir), "-o",
            f"-p {port} -k {data_dir}", "-l", str(data_dir / "postgres.log"),
            "-w", "start",
        ], capture=False)
        with psycopg.connect(url) as connection:
            connection.execute("""
                CREATE ROLE everydayai_owner NOLOGIN;
                CREATE ROLE everydayai_runtime LOGIN;
                CREATE ROLE everydayai_wecom_runtime LOGIN;
                CREATE ROLE everydayai_worker LOGIN;
                CREATE ROLE everydayai_sync LOGIN;
                CREATE ROLE everydayai LOGIN;
                GRANT everydayai_owner TO postgres;
                ALTER SCHEMA public OWNER TO everydayai_owner;
                SET ROLE everydayai_owner;
                CREATE TABLE tasks(
                    id UUID PRIMARY KEY,
                    conversation_id UUID NOT NULL,
                    user_id UUID NOT NULL,
                    org_id UUID
                );
                CREATE FUNCTION tenant_actor_user_id() RETURNS UUID
                LANGUAGE sql STABLE AS $$
                    SELECT nullif(current_setting('app.actor_user_id', true), '')::UUID
                $$;
                CREATE FUNCTION tenant_org_id() RETURNS UUID
                LANGUAGE sql STABLE AS $$
                    SELECT nullif(current_setting('app.org_id', true), '')::UUID
                $$;
                RESET ROLE;
            """)
            connection.commit()
        for migration in (
            "046_tool_audit_log.sql",
            "088_extend_tool_audit_log.sql",
        ):
            _apply_as_owner(url, ROOT / "migrations" / migration)
        _apply(
            url,
            ROOT / "migrations" / "196_runtime_tool_audit_capability.sql",
        )
        yield url
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "stop"],
            capture_output=True, text=True, timeout=30,
        )
        shutil.rmtree(data_dir, ignore_errors=True)


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _apply_as_owner(url: str, path: Path) -> None:
    with psycopg.connect(url) as connection:
        with connection.transaction():
            connection.execute("SET LOCAL ROLE everydayai_owner")
            connection.execute(path.read_text(encoding="utf-8"))


def _required_partitions(connection) -> list[str]:
    return [
        row[0] for row in connection.execute("""
            SELECT 'tool_audit_log_' || to_char(
                date_trunc('month', CURRENT_DATE)
                + make_interval(months => month_offset),
                'YYYY_MM'
            )
            FROM generate_series(0, 2) AS month_offset
        """).fetchall()
    ]


def test_apply_rollback_reapply_and_runtime_write(
    database: str, monkeypatch,
) -> None:
    migration = ROOT / "migrations" / "229_tool_audit_partition_lifecycle.sql"
    rollback = (
        ROOT / "migrations" / "rollback"
        / "229_tool_audit_partition_lifecycle_rollback.sql"
    )
    _apply(database, migration)

    task_id, conversation_id, user_id, org_id = (uuid4() for _ in range(4))
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO tasks VALUES(%s,%s,%s,%s)",
            (task_id, conversation_id, user_id, org_id),
        )
        connection.commit()
        required = _required_partitions(connection)
        attached = {
            row[0] for row in connection.execute("""
                SELECT child.relname
                FROM pg_inherits inheritance
                JOIN pg_class child ON child.oid = inheritance.inhrelid
                WHERE inheritance.inhparent = 'tool_audit_log'::regclass
            """).fetchall()
        }
        assert set(required) <= attached
        assert connection.execute("""
            SELECT count(*)
            FROM pg_inherits inheritance
            JOIN pg_class child ON child.oid = inheritance.inhrelid
            JOIN pg_roles owner ON owner.oid = child.relowner
            WHERE inheritance.inhparent = 'tool_audit_log'::regclass
              AND child.relname = ANY(%s)
              AND owner.rolname = 'everydayai_owner'
        """, (required,)).fetchone()[0] == 3
        assert connection.execute(
            "SELECT has_function_privilege('everydayai_runtime',"
            "'maintain_tool_audit_partitions()','EXECUTE')"
        ).fetchone()[0] is False

    monkeypatch.setenv("MIGRATION_DATABASE_URL", database)
    from scripts.verify_tool_audit_partition_contract import main as verify

    assert verify() == 0

    runtime_url = database.replace("postgres@", "everydayai_runtime@")
    with psycopg.connect(runtime_url) as connection:
        connection.execute(
            "SELECT set_config('app.actor_user_id',%s,false)", (str(user_id),),
        )
        connection.execute(
            "SELECT set_config('app.org_id',%s,false)", (str(org_id),),
        )
        connection.execute(
            "SELECT set_config('app.access_kind','runtime',false)"
        )
        outcome = connection.execute(
            "SELECT record_runtime_tool_audit("
            "%s,%s,%s,1,%s,1,1,'success',false,false,1,1,%s)",
            (task_id, "erp_agent", "call-1", "hash", "trace-1"),
        ).fetchone()[0]
        assert outcome["outcome"] == "recorded"
        connection.commit()

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        future_partition = _required_partitions(connection)[-1]
        connection.execute(f'DROP TABLE public."{future_partition}"')
        connection.commit()

    def maintain() -> None:
        with psycopg.connect(database) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute("SELECT maintain_tool_audit_partitions()")
            connection.commit()

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _index: maintain(), range(16)))

    with psycopg.connect(database) as connection:
        assert future_partition in {
            row[0] for row in connection.execute("""
                SELECT child.relname FROM pg_inherits inheritance
                JOIN pg_class child ON child.oid = inheritance.inhrelid
                WHERE inheritance.inhparent = 'tool_audit_log'::regclass
            """).fetchall()
        }
        assert connection.execute("""
            SELECT count(*) FROM pg_inherits inheritance
            JOIN pg_class child ON child.oid = inheritance.inhrelid
            WHERE inheritance.inhparent = 'tool_audit_log'::regclass
              AND child.relname ~ '^tool_audit_log_[0-9]{4}_[0-9]{2}$'
              AND to_date(substring(child.relname FROM 16), 'YYYY_MM')
                    + interval '1 month'
                  <= CURRENT_DATE - interval '90 days'
        """).fetchone()[0] == 0

    _apply(database, rollback)
    with psycopg.connect(database) as connection:
        definition = connection.execute(
            "SELECT pg_get_functiondef('maintain_tool_audit_partitions()'::regprocedure)"
        ).fetchone()[0]
        assert "pg_advisory_xact_lock" not in definition
        assert set(_required_partitions(connection)) <= {
            row[0] for row in connection.execute("""
                SELECT child.relname FROM pg_inherits inheritance
                JOIN pg_class child ON child.oid = inheritance.inhrelid
                WHERE inheritance.inhparent = 'tool_audit_log'::regclass
            """).fetchall()
        }

    _apply(database, migration)
