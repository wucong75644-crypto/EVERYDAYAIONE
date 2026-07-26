#!/bin/bash

set -euo pipefail

if [ "${ALLOW_DESTRUCTIVE_TENANT_DB_ROLLBACK:-false}" != "true" ]; then
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
\\set ON_ERROR_STOP on
BEGIN;

DO \$rollback\$
DECLARE
    target_tables CONSTANT TEXT[] := ARRAY[
        'knowledge_nodes', 'knowledge_metrics', 'knowledge_edges',
        'scoring_audit_log', 'tool_audit_log', 'permission_audit_log'
    ];
    invalid_relations TEXT;
    relation_record RECORD;
    sequence_record RECORD;
BEGIN
    IF to_regrole('${legacy_owner}') IS NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_LEGACY_ROLE_MISSING';
    END IF;
    IF to_regprocedure('public.maintain_tool_audit_partitions()') IS NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_FUNCTION_MISSING';
    END IF;
    IF (
        SELECT owner_role.rolname
          FROM pg_catalog.pg_proc function_record
          JOIN pg_catalog.pg_roles owner_role
            ON owner_role.oid = function_record.proowner
         WHERE function_record.oid =
               'public.maintain_tool_audit_partitions()'::regprocedure
    ) <> 'everydayai_owner' THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_FUNCTION_OWNER_UNEXPECTED';
    END IF;

    SELECT string_agg(relation.relname, ', ' ORDER BY relation.relname)
      INTO invalid_relations
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = relation.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
     WHERE namespace.nspname = 'public'
       AND (
           relation.relname = ANY(target_tables)
           OR relation.oid IN (
               SELECT child.inhrelid FROM pg_catalog.pg_inherits child
                WHERE child.inhparent = 'public.tool_audit_log'::regclass
           )
       )
       AND (
           owner_role.rolname <> 'everydayai_owner'
           OR relation.relforcerowsecurity
       );
    IF invalid_relations IS NOT NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_ROLLBACK_PRECONDITION_FAILED: %',
            invalid_relations;
    END IF;

    FOR relation_record IN
        SELECT relation.relname
          FROM pg_catalog.pg_class relation
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'public'
           AND (
               relation.relname = ANY(target_tables)
               OR relation.oid IN (
                   SELECT child.inhrelid FROM pg_catalog.pg_inherits child
                    WHERE child.inhparent = 'public.tool_audit_log'::regclass
               )
           )
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I OWNER TO %I',
            relation_record.relname, '${legacy_owner}'
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

ALTER FUNCTION public.maintain_tool_audit_partitions()
    OWNER TO ${legacy_owner};

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Knowledge/Audit 所有权已恢复"
