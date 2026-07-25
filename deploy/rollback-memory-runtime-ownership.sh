#!/bin/bash

set -euo pipefail

if [ "${ALLOW_TENANT_DB_OWNERSHIP_ROLLBACK:-false}" != "true" ]; then
    echo "❌ 必须显式设置 ALLOW_TENANT_DB_OWNERSHIP_ROLLBACK=true" >&2
    exit 1
fi
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
    forced_tables TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '${legacy_owner}'
    ) THEN
        RAISE EXCEPTION 'LEGACY_DATABASE_OWNER_MISSING';
    END IF;
    SELECT string_agg(c.relname, ', ' ORDER BY c.relname)
      INTO forced_tables
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relforcerowsecurity
       AND c.relname = ANY(ARRAY[
           'memory_pipeline_state',
           'memory_session_logs',
           'memory_consolidation_runs',
           'memory_atoms'
       ]);
    IF forced_tables IS NOT NULL THEN
        RAISE EXCEPTION 'DISABLE_FORCE_RLS_BEFORE_OWNERSHIP_ROLLBACK: %',
            forced_tables;
    END IF;
END
\$preflight\$;

ALTER TABLE public.memory_pipeline_state OWNER TO ${legacy_owner};
ALTER TABLE public.memory_session_logs OWNER TO ${legacy_owner};
ALTER TABLE public.memory_consolidation_runs OWNER TO ${legacy_owner};
ALTER TABLE public.memory_atoms OWNER TO ${legacy_owner};
ALTER FUNCTION public.commit_memory_session_flush(
    UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB, TEXT, TEXT, TEXT
) OWNER TO ${legacy_owner};
ALTER FUNCTION public.commit_memory_consolidation(
    UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
) OWNER TO ${legacy_owner};

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Memory Runtime 四表及两个提交函数所有权已恢复"
