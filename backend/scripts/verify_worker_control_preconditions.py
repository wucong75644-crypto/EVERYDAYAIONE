"""Fail closed before applying Worker Control migrations 171–180."""
from __future__ import annotations

import os
from typing import Any

import psycopg


EXPECTED_OWNER = "everydayai_owner"
TARGET_TABLES = (
    "error_logs",
    "knowledge_metrics",
    "scheduled_tasks",
    "scheduled_task_runs",
)


class WorkerControlPreconditionError(RuntimeError):
    """Worker Control administrator prerequisites are incomplete."""


def verify_preconditions(connection: psycopg.Connection[Any]) -> None:
    """Require exact table and owned-sequence ownership before migrations."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relation.relname, owner_role.rolname
              FROM pg_catalog.pg_class relation
              JOIN pg_catalog.pg_namespace namespace
                ON namespace.oid = relation.relnamespace
              JOIN pg_catalog.pg_roles owner_role
                ON owner_role.oid = relation.relowner
             WHERE namespace.nspname = 'public'
               AND relation.relname = ANY(%s)
            """,
            (list(TARGET_TABLES),),
        )
        owners = dict(cursor.fetchall())
        invalid = [
            f"{table}={owners.get(table, 'missing')}"
            for table in TARGET_TABLES
            if owners.get(table) != EXPECTED_OWNER
        ]

        cursor.execute(
            """
            SELECT sequence.relname, owner_role.rolname
              FROM pg_catalog.pg_class sequence
              JOIN pg_catalog.pg_roles owner_role
                ON owner_role.oid = sequence.relowner
              JOIN pg_catalog.pg_depend dependency
                ON dependency.objid = sequence.oid
              JOIN pg_catalog.pg_class target
                ON target.oid = dependency.refobjid
              JOIN pg_catalog.pg_namespace namespace
                ON namespace.oid = target.relnamespace
             WHERE sequence.relkind = 'S'
               AND namespace.nspname = 'public'
               AND target.relname = ANY(%s)
               AND dependency.deptype IN ('a', 'i')
            """,
            (list(TARGET_TABLES),),
        )
        invalid.extend(
            f"{sequence}={owner}"
            for sequence, owner in cursor.fetchall()
            if owner != EXPECTED_OWNER
        )
        cursor.execute(
            """
            SELECT role_name, table_name, privilege_type
              FROM unnest(%s::TEXT[]) AS role_name
              CROSS JOIN unnest(%s::TEXT[]) AS table_name
              CROSS JOIN unnest(%s::TEXT[]) AS privilege_type
             WHERE has_table_privilege(
                 role_name,
                 'public.' || table_name,
                 privilege_type
             )
            """,
            (
                [
                    "everydayai_runtime",
                    "everydayai_wecom_runtime",
                    "everydayai_worker",
                ],
                list(TARGET_TABLES),
                ["SELECT", "INSERT", "UPDATE", "DELETE"],
            ),
        )
        invalid.extend(
            f"{role}.{table}={privilege}"
            for role, table, privilege in cursor.fetchall()
        )
    connection.rollback()
    if invalid:
        raise WorkerControlPreconditionError(
            "WORKER_CONTROL_OWNERSHIP_INCOMPLETE: " + ", ".join(invalid)
        )


def main() -> int:
    database_url = os.getenv("MIGRATION_DATABASE_URL")
    if not database_url:
        raise WorkerControlPreconditionError(
            "MIGRATION_DATABASE_URL_REQUIRED"
        )
    with psycopg.connect(database_url) as connection:
        verify_preconditions(connection)
    print("Worker Control migration prerequisites verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
