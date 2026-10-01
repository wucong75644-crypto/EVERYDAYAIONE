"""Prove the local admin connection targets the application's live database.

Only a read-only identity probe is executed as postgres. Credentials stay in
the Python process; output contains only the database name and port for psql.
"""
from __future__ import annotations

import re
import secrets
import subprocess

import psycopg
from psycopg import sql


def verify_target(app, local_query):
    database, oid, pid, port = app.execute(
        "SELECT current_database(), oid, pg_backend_pid(), "
        "current_setting('port')::int FROM pg_database WHERE datname=current_database()"
    ).fetchone()
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise RuntimeError("MIGRATION_DATABASE_NAME_UNSUPPORTED")
    # A fresh session lock visible through the local connection proves cluster
    # identity, even when separate clusters have equal database names/OIDs.
    key_a, key_b = secrets.randbelow(2**31), secrets.randbelow(2**31)
    app.execute("SELECT pg_advisory_lock(%s, %s)", (key_a, key_b))
    try:
        probe = sql.SQL("""SELECT EXISTS (
            SELECT 1 FROM pg_locks WHERE locktype='advisory' AND granted
            AND database={oid} AND pid={pid} AND classid={a} AND objid={b}
            AND objsubid=2
        ) AND current_database()={name}
          AND (SELECT oid FROM pg_database WHERE datname=current_database())={oid}
        """).format(oid=sql.Literal(oid), pid=sql.Literal(pid),
                    a=sql.Literal(key_a), b=sql.Literal(key_b), name=sql.Literal(database))
        if local_query(database, port, probe.as_string()).strip() != "t":
            raise RuntimeError("MIGRATION_LOCAL_DATABASE_MISMATCH")
    finally:
        app.execute("SELECT pg_advisory_unlock(%s, %s)", (key_a, key_b))
    return database, port


def query_as_postgres(database, port, statement):
    result = subprocess.run(
        ["sudo", "-n", "-u", "postgres", "psql", "-X", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-h", "/var/run/postgresql",
         "-p", str(port), "-d", database],
        input=statement, text=True, capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("MIGRATION_LOCAL_ADMIN_PROBE_FAILED")
    return result.stdout


def main():
    from dotenv import dotenv_values

    dsn = dotenv_values("/var/www/everydayai/backend/.env").get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("MIGRATION_DATABASE_URL_MISSING")
    with psycopg.connect(dsn, autocommit=True) as app:
        database, port = verify_target(app, query_as_postgres)
    print(database, port)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Connection exceptions can include user/host details. Never echo DSNs.
        raise SystemExit("Migration target verification failed; no migration executed")
