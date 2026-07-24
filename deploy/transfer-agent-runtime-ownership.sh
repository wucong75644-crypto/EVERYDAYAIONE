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

DO \$preflight\$
DECLARE
    missing_roles TEXT;
    missing_tables TEXT;
    missing_functions TEXT;
    unexpected_owners TEXT;
    unexpected_function_owners TEXT;
BEGIN
    SELECT string_agg(required_role, ', ' ORDER BY required_role)
      INTO missing_roles
      FROM unnest(ARRAY[
          'everydayai_owner',
          'everydayai_migrator',
          'everydayai_runtime',
          'everydayai_worker',
          '${legacy_owner}'
      ]) AS required_role
     WHERE NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_roles
          WHERE rolname = required_role
     );
    IF missing_roles IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_ROLE_MISSING: %', missing_roles;
    END IF;

    SELECT string_agg(required_table, ', ' ORDER BY required_table)
      INTO missing_tables
      FROM unnest(ARRAY[
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
          'user_activity_events',
          'schema_migration_ledger'
      ]) AS required_table
     WHERE to_regclass('public.' || required_table) IS NULL;
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_TABLE_MISSING: %', missing_tables;
    END IF;

    SELECT string_agg(required_function, ', ' ORDER BY required_function)
      INTO missing_functions
      FROM unnest(ARRAY[
          '_resolve_user_asset(uuid,text,text,text,text,text,text,text,text,text,text,text,bigint,text,jsonb,timestamp with time zone)',
          '_bind_user_asset_ref(uuid,text,uuid,uuid,text,text,text,uuid,uuid,uuid,uuid,uuid,integer,text,text,jsonb,timestamp with time zone)',
          'register_user_asset(uuid,text,text,text,text,text,text,text,text,text,text,text,bigint,text,jsonb,text,uuid,text,text,text,uuid,uuid,uuid,uuid,uuid,integer,text,text,jsonb,timestamp with time zone)'
      ]) AS required_function
     WHERE to_regprocedure('public.' || required_function) IS NULL;
    IF missing_functions IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_FUNCTION_MISSING: %', missing_functions;
    END IF;

    SELECT string_agg(c.relname || '=' || owner_role.rolname, ', ' ORDER BY c.relname)
      INTO unexpected_owners
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
     WHERE n.nspname = 'public'
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
       ])
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_owners IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_TABLE_OWNER_UNEXPECTED: %', unexpected_owners;
    END IF;

    SELECT string_agg(procedure.proname || '=' || owner_role.rolname, ', ')
      INTO unexpected_function_owners
      FROM pg_catalog.pg_proc procedure
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = procedure.pronamespace
      JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = procedure.proowner
     WHERE namespace.nspname = 'public'
       AND procedure.oid = ANY(ARRAY[
          to_regprocedure('public._resolve_user_asset(uuid,text,text,text,text,text,text,text,text,text,text,text,bigint,text,jsonb,timestamp with time zone)'),
          to_regprocedure('public._bind_user_asset_ref(uuid,text,uuid,uuid,text,text,text,uuid,uuid,uuid,uuid,uuid,integer,text,text,jsonb,timestamp with time zone)'),
          to_regprocedure('public.register_user_asset(uuid,text,text,text,text,text,text,text,text,text,text,text,bigint,text,jsonb,text,uuid,text,text,text,uuid,uuid,uuid,uuid,uuid,integer,text,text,jsonb,timestamp with time zone)')
       ])
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_function_owners IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_FUNCTION_OWNER_UNEXPECTED: %',
            unexpected_function_owners;
    END IF;
END
\$preflight\$;

GRANT everydayai_owner TO ${legacy_owner};

ALTER TABLE public.schema_migration_ledger OWNER TO everydayai_owner;
ALTER TABLE public.conversation_artifacts OWNER TO everydayai_owner;
ALTER TABLE public.conversation_attachment_refs OWNER TO everydayai_owner;
ALTER TABLE public.conversation_channel_bindings OWNER TO everydayai_owner;
ALTER TABLE public.conversation_compactions OWNER TO everydayai_owner;
ALTER TABLE public.conversation_context_items OWNER TO everydayai_owner;
ALTER TABLE public.conversation_context_receipts OWNER TO everydayai_owner;
ALTER TABLE public.conversation_data_evidence OWNER TO everydayai_owner;
ALTER TABLE public.message_generation_requests OWNER TO everydayai_owner;
ALTER TABLE public.task_attachment_refs OWNER TO everydayai_owner;
ALTER TABLE public.memory_atoms OWNER TO everydayai_owner;
ALTER TABLE public.user_assets OWNER TO everydayai_owner;
ALTER TABLE public.user_asset_refs OWNER TO everydayai_owner;
ALTER TABLE public.user_activity_events OWNER TO everydayai_owner;

ALTER FUNCTION public._resolve_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TIMESTAMPTZ
) OWNER TO everydayai_owner;
ALTER FUNCTION public._bind_user_asset_ref(
    UUID, TEXT, UUID, UUID, TEXT, TEXT, TEXT, UUID, UUID, UUID, UUID,
    UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) OWNER TO everydayai_owner;
ALTER FUNCTION public.register_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TEXT, UUID, TEXT, TEXT, TEXT, UUID,
    UUID, UUID, UUID, UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) OWNER TO everydayai_owner;

GRANT USAGE, CREATE ON SCHEMA public TO everydayai_owner;
GRANT USAGE ON SCHEMA public TO everydayai_runtime, everydayai_worker;
GRANT SELECT ON TABLE
    public.organizations,
    public.org_members,
    public.conversations,
    public.tasks
TO everydayai_owner;
GRANT USAGE ON SCHEMA public TO everydayai_migrator;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.schema_migration_ledger TO everydayai_migrator;
REVOKE ALL ON TABLE
    public.conversation_artifacts,
    public.conversation_attachment_refs,
    public.conversation_channel_bindings,
    public.conversation_compactions,
    public.conversation_context_items,
    public.conversation_context_receipts,
    public.conversation_data_evidence,
    public.message_generation_requests,
    public.task_attachment_refs,
    public.memory_atoms,
    public.user_assets,
    public.user_asset_refs,
    public.user_activity_events
FROM everydayai_runtime, everydayai_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.conversation_artifacts,
    public.conversation_attachment_refs,
    public.conversation_channel_bindings,
    public.conversation_compactions,
    public.conversation_context_items,
    public.conversation_context_receipts,
    public.conversation_data_evidence,
    public.message_generation_requests,
    public.task_attachment_refs,
    public.memory_atoms,
    public.user_assets,
    public.user_asset_refs,
    public.user_activity_events
TO ${legacy_owner};

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Agent Runtime 13 张表已转移给 everydayai_owner"
