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
\\set ON_ERROR_STOP on
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
          'conversation_artifacts',
          'conversation_attachment_refs',
          'conversation_channel_bindings',
          'conversation_compactions',
          'conversation_context_items',
          'conversation_context_receipts',
          'conversation_data_evidence',
          'message_generation_requests',
          'task_attachment_refs',
          'memory_atoms',
          'user_assets',
          'user_asset_refs',
          'user_activity_events'
       ]);
    IF forced_tables IS NOT NULL THEN
        RAISE EXCEPTION 'DISABLE_FORCE_RLS_BEFORE_OWNERSHIP_ROLLBACK: %',
            forced_tables;
    END IF;
END
\$preflight\$;

ALTER TABLE public.schema_migration_ledger OWNER TO ${legacy_owner};
ALTER TABLE public.conversation_artifacts OWNER TO ${legacy_owner};
ALTER TABLE public.conversation_attachment_refs OWNER TO ${legacy_owner};
ALTER TABLE public.conversation_channel_bindings OWNER TO ${legacy_owner};
ALTER TABLE public.conversation_compactions OWNER TO ${legacy_owner};
ALTER TABLE public.conversation_context_items OWNER TO ${legacy_owner};
ALTER TABLE public.conversation_context_receipts OWNER TO ${legacy_owner};
ALTER TABLE public.conversation_data_evidence OWNER TO ${legacy_owner};
ALTER TABLE public.message_generation_requests OWNER TO ${legacy_owner};
ALTER TABLE public.task_attachment_refs OWNER TO ${legacy_owner};
ALTER TABLE public.memory_atoms OWNER TO ${legacy_owner};
ALTER TABLE public.user_assets OWNER TO ${legacy_owner};
ALTER TABLE public.user_asset_refs OWNER TO ${legacy_owner};
ALTER TABLE public.user_activity_events OWNER TO ${legacy_owner};

ALTER FUNCTION public._resolve_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TIMESTAMPTZ
) OWNER TO ${legacy_owner};
ALTER FUNCTION public._bind_user_asset_ref(
    UUID, TEXT, UUID, UUID, TEXT, TEXT, TEXT, UUID, UUID, UUID, UUID,
    UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) OWNER TO ${legacy_owner};
ALTER FUNCTION public.register_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TEXT, UUID, TEXT, TEXT, TEXT, UUID,
    UUID, UUID, UUID, UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) OWNER TO ${legacy_owner};

REVOKE USAGE ON SCHEMA public FROM everydayai_runtime, everydayai_worker;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Agent Runtime 13 张表所有权已恢复"
