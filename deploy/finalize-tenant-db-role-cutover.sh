#!/bin/bash

set -euo pipefail

if [ "${ALLOW_TENANT_DB_ROLE_FINALIZE:-false}" != "true" ]; then
    echo "❌ 必须设置 ALLOW_TENANT_DB_ROLE_FINALIZE=true" >&2
    exit 1
fi
if [ "${TENANT_SERVICES_USE_ISOLATED_ROLES:-false}" != "true" ]; then
    echo "❌ 必须确认所有服务已切换独立角色" >&2
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

DO \$finalize\$
DECLARE
    missing_migrations TEXT;
    unexpected_owners TEXT;
    legacy_sessions INTEGER;
BEGIN
    IF session_user = '${legacy_owner}' THEN
        RAISE EXCEPTION 'FINALIZE_REQUIRES_SEPARATE_ADMIN_ROLE';
    END IF;

    SELECT string_agg(required_identity, ', ' ORDER BY required_identity)
      INTO missing_migrations
      FROM unnest(ARRAY[
          '150_agent_runtime_tenant_defense.sql',
          '151_agent_runtime_role_grants.sql',
          '152_wecom_runtime_capability.sql',
          '153_runtime_message_rls_and_auth.sql',
          '154_wecom_message_rpc_facades.sql',
          '155_web_wecom_oauth_capabilities.sql',
          '156_governance_authority_foundation.sql',
          '157_governance_write_capabilities.sql',
          '158_configuration_control_plane_foundation.sql',
          '159_configuration_management_core.sql',
          '159_configuration_management_facades.sql',
          '160_configuration_resolution_core.sql',
          '160_configuration_resolution_facades.sql',
          '161_configuration_legacy_import.sql',
          '162_configuration_legacy_export_access.sql'
      ]) AS required_identity
     WHERE NOT EXISTS (
         SELECT 1
           FROM public.schema_migration_ledger ledger
          WHERE ledger.identity = required_identity
            AND ledger.status = 'applied'
     );
    IF missing_migrations IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_MIGRATIONS_NOT_APPLIED: %',
            missing_migrations;
    END IF;

    SELECT string_agg(c.relname || '=' || owner_role.rolname, ', '
                      ORDER BY c.relname)
      INTO unexpected_owners
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
     WHERE n.nspname = 'public'
       AND c.relname = ANY(ARRAY[
          'schema_migration_ledger',
          'conversation_artifacts', 'conversation_attachment_refs',
          'conversation_channel_bindings', 'conversation_compactions',
          'conversation_context_items', 'conversation_context_receipts',
          'conversation_data_evidence', 'message_generation_requests',
          'task_attachment_refs', 'memory_atoms', 'user_assets',
          'user_asset_refs', 'user_activity_events',
          'users', 'organizations', 'org_members', 'org_configs',
          'org_invitations', 'governance_audit_log',
          'configuration_definitions', 'configuration_bundle_definitions',
          'configuration_entries', 'configuration_policies',
          'secret_records', 'configuration_import_audit_log',
          'wecom_user_mappings', 'wecom_chat_targets', 'conversations',
          'messages', 'tasks', 'credits_history', 'credit_transactions',
          'image_generations', 'detail_projects', 'detail_project_images',
          'refresh_tokens', 'user_subscriptions', 'user_memory_settings'
       ])
       AND owner_role.rolname <> 'everydayai_owner';
    IF unexpected_owners IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_OWNER_CUTOVER_INCOMPLETE: %',
            unexpected_owners;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_auth_members membership
          JOIN pg_catalog.pg_roles granted_role
            ON granted_role.oid = membership.roleid
          JOIN pg_catalog.pg_roles member_role
            ON member_role.oid = membership.member
         WHERE granted_role.rolname = 'everydayai_owner'
           AND member_role.rolname IN (
               'everydayai_runtime',
               'everydayai_wecom_runtime',
               'everydayai_worker'
           )
    ) THEN
        RAISE EXCEPTION 'SERVICE_ROLE_HAS_OWNER_MEMBERSHIP';
    END IF;

    SELECT COUNT(*)
      INTO legacy_sessions
      FROM pg_catalog.pg_stat_activity
     WHERE usename = '${legacy_owner}'
       AND pid <> pg_backend_pid();
    IF legacy_sessions > 0 THEN
        RAISE EXCEPTION 'LEGACY_DATABASE_SESSIONS_REMAIN: %',
            legacy_sessions;
    END IF;
END
\$finalize\$;

REVOKE everydayai_owner FROM ${legacy_owner};

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 旧数据库角色的临时 owner 兼容能力已撤销"
