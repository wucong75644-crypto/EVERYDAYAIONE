"""Fail before deployment writes unless the local migration executor targets the app DB."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess


IDENTITY_SQL = """SELECT json_build_array(
    current_database(), database.oid,
    floor(extract(epoch FROM pg_postmaster_start_time()))::bigint
)::text
FROM pg_database AS database
WHERE database.datname = current_database()"""


def verify_same_database_instance(application_identity, local_identity):
    if tuple(application_identity) != tuple(local_identity):
        raise ValueError("MIGRATION_DATABASE_IDENTITY_MISMATCH")


def read_local_identity(database_name, run=subprocess.run):
    result = run(
        ["sudo", "-n", "-u", "postgres", "psql", "-X", "-q", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-d", database_name, "-c", IDENTITY_SQL],
        check=True, capture_output=True, text=True,
    )
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise ValueError("MIGRATION_DATABASE_IDENTITY_UNREADABLE")
    identity = json.loads(rows[0])
    if not isinstance(identity, list) or len(identity) != 3:
        raise ValueError("MIGRATION_DATABASE_IDENTITY_UNREADABLE")
    return (str(identity[0]), int(identity[1]), int(identity[2]))


def verify_migration_roles(database_name, run=subprocess.run):
    run(
        ["sudo", "-n", "-u", "postgres", "psql", "-X", "-q", "-v",
         "ON_ERROR_STOP=1", "-d", database_name, "-c",
         "SET ROLE everydayai_owner; RESET ROLE; "
         "SET ROLE everydayai; RESET ROLE;"],
        check=True, capture_output=True, text=True,
    )


def verify_audit_table_owner(database_name, run=subprocess.run):
    result = run(
        ["sudo", "-n", "-u", "postgres", "psql", "-X", "-q", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-d", database_name, "-c",
         "SELECT pg_get_userbyid(relowner) FROM pg_class "
         "WHERE oid = 'public.tool_audit_log'::regclass;"],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip() != "everydayai":
        raise ValueError("MIGRATION_AUDIT_TABLE_OWNER_MISMATCH")


def main():
    import psycopg
    from dotenv import dotenv_values

    root = Path("/var/www/everydayai")
    values = dotenv_values(root / "backend/.env")
    with psycopg.connect(values["DATABASE_URL"], connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            "SELECT current_database(), oid, "
            "floor(extract(epoch FROM pg_postmaster_start_time()))::bigint "
            "FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        if row is None:
            raise ValueError("MIGRATION_DATABASE_IDENTITY_UNREADABLE")
        application_identity = (str(row[0]), int(row[1]), int(row[2]))
        connection.rollback()

    database_name = application_identity[0]
    local_identity = read_local_identity(database_name)
    verify_same_database_instance(application_identity, local_identity)
    verify_migration_roles(database_name)
    verify_audit_table_owner(database_name)
    print("Migration executor database identity and owner role verified")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Migration executor verification failed; deployment stopped")
