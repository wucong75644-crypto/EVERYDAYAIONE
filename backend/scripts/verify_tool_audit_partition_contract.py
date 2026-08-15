#!/usr/bin/env python3
"""Fail closed when the production tool-audit partition contract is incomplete."""

from __future__ import annotations

import os
import sys

import psycopg


CONTRACT_SQL = """
WITH expected AS (
    SELECT
        (date_trunc('month', CURRENT_DATE)
            + make_interval(months => month_offset))::DATE AS month_start,
        (date_trunc('month', CURRENT_DATE)
            + make_interval(months => month_offset + 1))::DATE AS month_end,
        'tool_audit_log_' || to_char(
            date_trunc('month', CURRENT_DATE)
            + make_interval(months => month_offset),
            'YYYY_MM'
        ) AS partition_name
    FROM generate_series(0, 2) AS month_offset
), attached AS (
    SELECT
        child.relname AS partition_name,
        owner.rolname AS owner_name,
        pg_get_expr(child.relpartbound, child.oid) AS bound_expression
    FROM pg_catalog.pg_inherits inheritance
    JOIN pg_catalog.pg_class child ON child.oid = inheritance.inhrelid
    JOIN pg_catalog.pg_roles owner ON owner.oid = child.relowner
    WHERE inheritance.inhparent = 'public.tool_audit_log'::regclass
)
SELECT expected.partition_name, attached.owner_name, attached.bound_expression
FROM expected
LEFT JOIN attached USING (partition_name)
WHERE attached.partition_name IS NULL
   OR attached.owner_name <> 'everydayai_owner'
   OR position(
        to_char(expected.month_start, 'YYYY-MM-DD')
        IN attached.bound_expression
   ) = 0
   OR position(
        to_char(expected.month_end, 'YYYY-MM-DD')
        IN attached.bound_expression
   ) = 0
ORDER BY expected.partition_name
"""

FUNCTION_SQL = """
SELECT
    owner.rolname,
    procedure.prosecdef,
    pg_get_functiondef(procedure.oid),
    has_function_privilege(
        'everydayai_runtime',
        'public.maintain_tool_audit_partitions()',
        'EXECUTE'
    ),
    has_function_privilege(
        'everydayai_runtime',
        'public.record_runtime_tool_audit(uuid,text,text,integer,text,'
        'integer,integer,text,boolean,boolean,integer,integer,text)',
        'EXECUTE'
    )
FROM pg_catalog.pg_proc procedure
JOIN pg_catalog.pg_roles owner ON owner.oid = procedure.proowner
WHERE procedure.oid = 'public.maintain_tool_audit_partitions()'::regprocedure
"""


def main() -> int:
    database_url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not database_url:
        print("missing MIGRATION_DATABASE_URL", file=sys.stderr)
        return 1

    with psycopg.connect(database_url) as connection:
        invalid = [row[0] for row in connection.execute(CONTRACT_SQL).fetchall()]
        function_row = connection.execute(FUNCTION_SQL).fetchone()

    if invalid:
        print(f"invalid tool-audit partitions: {invalid}", file=sys.stderr)
        return 1
    if function_row is None:
        print("missing tool-audit maintenance function", file=sys.stderr)
        return 1

    owner, security_definer, definition, runtime_maintain, runtime_record = (
        function_row
    )
    if owner != "everydayai_owner" or not security_definer:
        print("invalid tool-audit maintenance owner contract", file=sys.stderr)
        return 1
    if "pg_advisory_xact_lock" not in definition:
        print("tool-audit maintenance lacks concurrency fence", file=sys.stderr)
        return 1
    if runtime_maintain or not runtime_record:
        print("invalid tool-audit runtime capability contract", file=sys.stderr)
        return 1

    print("tool-audit partition contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
