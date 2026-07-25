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
\set ON_ERROR_STOP on
BEGIN;

DO \$preflight\$
DECLARE
    missing_objects TEXT;
    invalid_owners TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
         WHERE rolname = 'everydayai_owner'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
         WHERE rolname = '${legacy_owner}'
    ) THEN
        RAISE EXCEPTION 'MEMORY_RUNTIME_REQUIRED_ROLE_MISSING';
    END IF;

    SELECT string_agg(required_object, ', ' ORDER BY required_object)
      INTO missing_objects
      FROM unnest(ARRAY[
          'memory_pipeline_state',
          'memory_session_logs',
          'memory_consolidation_runs',
          'memory_atoms'
      ]) required_object
     WHERE to_regclass('public.' || required_object) IS NULL;
    IF missing_objects IS NOT NULL THEN
        RAISE EXCEPTION 'MEMORY_RUNTIME_TABLE_MISSING: %', missing_objects;
    END IF;

    SELECT string_agg(c.relname || '=' || owner_role.rolname, ', ' ORDER BY c.relname)
      INTO invalid_owners
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
     WHERE n.nspname = 'public'
       AND c.relname = ANY(ARRAY[
           'memory_pipeline_state',
           'memory_session_logs',
           'memory_consolidation_runs',
           'memory_atoms'
       ])
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF invalid_owners IS NOT NULL THEN
        RAISE EXCEPTION 'MEMORY_RUNTIME_OWNER_UNEXPECTED: %', invalid_owners;
    END IF;
END
\$preflight\$;

GRANT everydayai_owner TO ${legacy_owner};
ALTER TABLE public.memory_pipeline_state OWNER TO everydayai_owner;
ALTER TABLE public.memory_session_logs OWNER TO everydayai_owner;
ALTER TABLE public.memory_consolidation_runs OWNER TO everydayai_owner;
ALTER TABLE public.memory_atoms OWNER TO everydayai_owner;
ALTER FUNCTION public.commit_memory_session_flush(
    UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB, TEXT, TEXT, TEXT
) OWNER TO everydayai_owner;
ALTER FUNCTION public.commit_memory_consolidation(
    UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
) OWNER TO everydayai_owner;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Memory Runtime 四表及两个提交函数已转移给 everydayai_owner"
