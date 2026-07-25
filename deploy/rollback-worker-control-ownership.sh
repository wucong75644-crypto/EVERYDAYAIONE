#!/bin/bash

set -euo pipefail

if [ "${ALLOW_DESTRUCTIVE_TENANT_DB_ROLLBACK:-}" != "true" ]; then
    echo "❌ 必须显式设置 ALLOW_DESTRUCTIVE_TENANT_DB_ROLLBACK=true" >&2
    exit 1
fi
if [ -z "${TENANT_DB_ADMIN_URL:-}" ]; then
    echo "❌ 缺少 TENANT_DB_ADMIN_URL" >&2
    exit 1
fi

legacy_owner=${LEGACY_DATABASE_OWNER:-everydayai}
if [[ ! "$legacy_owner" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
    echo "❌ LEGACY_DATABASE_OWNER 不是合法 PostgreSQL 角色名" >&2
    exit 1
fi

{
    cat <<SQL
\set ON_ERROR_STOP on
BEGIN;

DO \$rollback\$
DECLARE
    target_tables CONSTANT TEXT[] := ARRAY[
        'error_logs',
        'knowledge_metrics',
        'scheduled_tasks',
        'scheduled_task_runs'
    ];
    invalid_tables TEXT;
    table_name TEXT;
    sequence_record RECORD;
BEGIN
    SELECT string_agg(relation.relname, ', ' ORDER BY relation.relname)
      INTO invalid_tables
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = relation.relnamespace
      JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = relation.relowner
     WHERE namespace.nspname = 'public'
       AND relation.relname = ANY(target_tables)
       AND (
           owner_role.rolname <> 'everydayai_owner'
           OR relation.relforcerowsecurity
       );
    IF invalid_tables IS NOT NULL THEN
        RAISE EXCEPTION 'WORKER_CONTROL_ROLLBACK_PRECONDITION_FAILED: %',
            invalid_tables;
    END IF;

    FOREACH table_name IN ARRAY target_tables LOOP
        EXECUTE format(
            'ALTER TABLE public.%I OWNER TO %I',
            table_name, '${legacy_owner}'
        );
    END LOOP;

    FOR sequence_record IN
        SELECT DISTINCT sequence.relname
          FROM pg_catalog.pg_class sequence
          JOIN pg_catalog.pg_depend dependency ON dependency.objid = sequence.oid
          JOIN pg_catalog.pg_class target ON target.oid = dependency.refobjid
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = target.relnamespace
         WHERE sequence.relkind = 'S'
           AND namespace.nspname = 'public'
           AND target.relname = ANY(target_tables)
           AND dependency.deptype IN ('a', 'i')
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE public.%I OWNER TO %I',
            sequence_record.relname, '${legacy_owner}'
        );
    END LOOP;
END
\$rollback\$;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Worker Control 表及列序列所有权已恢复"
