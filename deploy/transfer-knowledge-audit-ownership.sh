#!/bin/bash

set -euo pipefail

if [ -z "${TENANT_DB_ADMIN_URL:-}" ]; then
    echo "❌ 缺少 TENANT_DB_ADMIN_URL" >&2
    exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
    echo "❌ 未找到 psql" >&2
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

DO \$transfer\$
DECLARE
    target_tables CONSTANT TEXT[] := ARRAY[
        'knowledge_nodes', 'knowledge_metrics', 'knowledge_edges',
        'scoring_audit_log', 'tool_audit_log', 'permission_audit_log'
    ];
    missing_roles TEXT;
    missing_tables TEXT;
    unexpected_owners TEXT;
    unexpected_sequence_owners TEXT;
    relation_record RECORD;
    sequence_record RECORD;
BEGIN
    SELECT string_agg(required_role, ', ' ORDER BY required_role)
      INTO missing_roles
      FROM unnest(ARRAY[
          'everydayai_owner', 'everydayai_runtime',
          'everydayai_wecom_runtime', 'everydayai_worker', '${legacy_owner}'
      ]) AS required_role
     WHERE to_regrole(required_role) IS NULL;
    IF missing_roles IS NOT NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_ROLE_MISSING: %', missing_roles;
    END IF;

    SELECT string_agg(required_table, ', ' ORDER BY required_table)
      INTO missing_tables
      FROM unnest(target_tables) AS required_table
     WHERE to_regclass('public.' || required_table) IS NULL;
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_TABLE_MISSING: %', missing_tables;
    END IF;
    IF to_regprocedure('public.maintain_tool_audit_partitions()') IS NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_FUNCTION_MISSING';
    END IF;

    SELECT string_agg(
               relation.relname || '=' || owner_role.rolname,
               ', ' ORDER BY relation.relname
           ) INTO unexpected_owners
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
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_owners IS NOT NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_OWNER_UNEXPECTED: %',
            unexpected_owners;
    END IF;

    SELECT string_agg(
               sequence.relname || '=' || owner_role.rolname,
               ', ' ORDER BY sequence.relname
           )
      INTO unexpected_sequence_owners
      FROM pg_catalog.pg_class sequence
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = sequence.relowner
      JOIN pg_catalog.pg_depend dependency ON dependency.objid = sequence.oid
      JOIN pg_catalog.pg_class target ON target.oid = dependency.refobjid
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = target.relnamespace
     WHERE sequence.relkind = 'S'
       AND namespace.nspname = 'public'
       AND target.relname = ANY(target_tables)
       AND dependency.deptype IN ('a', 'i')
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_sequence_owners IS NOT NULL THEN
        RAISE EXCEPTION 'KNOWLEDGE_AUDIT_SEQUENCE_OWNER_UNEXPECTED: %',
            unexpected_sequence_owners;
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
            'ALTER TABLE public.%I OWNER TO everydayai_owner',
            relation_record.relname
        );
        EXECUTE format(
            'REVOKE ALL ON TABLE public.%I FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker',
            relation_record.relname
        );
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO %I',
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
            'ALTER SEQUENCE public.%I OWNER TO everydayai_owner',
            sequence_record.relname
        );
        EXECUTE format(
            'REVOKE ALL ON SEQUENCE public.%I FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker',
            sequence_record.relname
        );
        EXECUTE format(
            'GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.%I TO %I',
            sequence_record.relname, '${legacy_owner}'
        );
    END LOOP;
END
\$transfer\$;

ALTER FUNCTION public.maintain_tool_audit_partitions()
    OWNER TO everydayai_owner;
REVOKE ALL ON FUNCTION public.maintain_tool_audit_partitions()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION public.maintain_tool_audit_partitions()
TO ${legacy_owner};

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Knowledge/Audit 表、分区、序列和维护函数已转移"
