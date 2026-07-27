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

bash "$(dirname "$0")/preflight/knowledge-audit-completion.sh"
bash "$(dirname "$0")/preflight/admin-user-assets-capability.sh"

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
    invalid_actor_capabilities TEXT;
    invalid_actor_dependencies TEXT;
    invalid_worker_capabilities TEXT;
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
          '162_configuration_legacy_export_access.sql',
          '163_conversation_actor_worker_discovery.sql',
          '164_actor_task_execution_capabilities.sql',
          '165_memory_runtime_tenant_boundary.sql',
          '166_wecom_worker_discovery.sql',
          '167_wecom_role_cutover_completion.sql',
          '168_wecom_runtime_read_capabilities.sql',
          '169_wecom_generation_context_org_scope.sql',
          '170_wecom_actor_enqueue_role_grant.sql',
          '171_worker_media_task_control.sql',
          '172_worker_video_terminal.sql',
          '173_worker_media_retry.sql',
          '174_worker_error_log_capability.sql',
          '175_worker_media_metric.sql',
          '176_worker_scheduled_scanner.sql',
          '177_worker_scheduled_execution.sql',
          '178_worker_scheduled_credits.sql',
          '179_scheduled_run_fencing.sql',
          '180_scheduled_task_tenant_boundary.sql',
          '181_sync_data_domain_boundary.sql',
          '182_sync_cross_domain_capabilities.sql',
          '183_sync_configuration_capabilities.sql',
          '184_runtime_erp_operator_control.sql',
          '185_external_sync_request_queue.sql',
          '189_web_runtime_access_completion.sql',
          '190_message_idempotency_role_capabilities.sql',
          '191_governance_actor_authority.sql',
          '192_atomic_organization_permission_initialization.sql',
          '193_runtime_assignment_read_capabilities.sql',
          '194_governed_assignment_management.sql',
          '195_organization_member_display_name.sql',
          '196_runtime_tool_audit_capability.sql',
          '197_runtime_knowledge_tenant_boundary.sql',
          '198_worker_model_scoring_capabilities.sql',
          '199_platform_error_monitor_capabilities.sql',
          '200_web_wecom_control_capabilities.sql',
          '201_wecom_callback_inbox.sql',
          '202_knowledge_audit_force_rls_completion.sql',
          '209_platform_admin_user_assets_capability.sql'
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

    SELECT string_agg(required_function, ', ' ORDER BY required_function)
      INTO invalid_actor_capabilities
      FROM unnest(ARRAY[
          'discover_generation_turn_candidates(integer)',
          'worker_claim_next_serial_generation_turn(uuid,integer,integer)',
          'worker_claim_branch_generation_turn(uuid,integer,integer)',
          'worker_get_claimed_generation_task(uuid,uuid)',
          'worker_renew_generation_lease(uuid,uuid,integer)',
          'worker_commit_generation_turn_with_context_v2(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)',
          'worker_fail_generation_turn(uuid,uuid,text,text)',
          'worker_update_generation_progress(uuid,uuid,text,jsonb)',
          'worker_update_generation_model(uuid,uuid,text,jsonb)',
          'worker_get_generation_terminal_snapshot(uuid,uuid)'
      ]) AS required_function
      LEFT JOIN pg_catalog.pg_proc procedure
        ON procedure.oid = to_regprocedure('public.' || required_function)
      LEFT JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = procedure.proowner
     WHERE procedure.oid IS NULL
        OR owner_role.rolname <> 'everydayai_owner'
        OR NOT procedure.prosecdef
        OR NOT has_function_privilege(
            'everydayai_worker', procedure.oid, 'EXECUTE'
        )
        OR has_function_privilege(
            'everydayai_runtime', procedure.oid, 'EXECUTE'
        )
        OR has_function_privilege(
            'everydayai_wecom_runtime', procedure.oid, 'EXECUTE'
        )
        OR EXISTS (
            SELECT 1
              FROM aclexplode(COALESCE(
                  procedure.proacl,
                  acldefault('f', procedure.proowner)
              )) acl
             WHERE acl.grantee = 0
               AND acl.privilege_type = 'EXECUTE'
        );
    IF invalid_actor_capabilities IS NOT NULL
       OR has_table_privilege(
           'everydayai_worker', 'public.tasks', 'SELECT'
       )
       OR has_table_privilege(
           'everydayai_worker', 'public.tasks', 'INSERT'
       )
       OR has_table_privilege(
           'everydayai_worker', 'public.tasks', 'UPDATE'
       )
       OR has_table_privilege(
           'everydayai_worker', 'public.tasks', 'DELETE'
       )
       OR has_any_column_privilege(
           'everydayai_worker', 'public.tasks', 'SELECT'
       )
       OR has_any_column_privilege(
           'everydayai_worker', 'public.tasks', 'INSERT'
       )
       OR has_any_column_privilege(
           'everydayai_worker', 'public.tasks', 'UPDATE'
       )
       OR NOT has_table_privilege(
           'everydayai_worker', 'public.conversations', 'SELECT'
       )
       OR NOT has_table_privilege(
           'everydayai_worker', 'public.messages', 'SELECT'
       )
       OR has_table_privilege(
           'everydayai_worker', 'public.conversations',
           'INSERT, UPDATE, DELETE'
       )
       OR has_table_privilege(
           'everydayai_worker', 'public.messages',
           'INSERT, UPDATE, DELETE'
       ) THEN
        RAISE EXCEPTION 'ACTOR_WORKER_CAPABILITY_CUTOVER_INCOMPLETE: %',
            COALESCE(invalid_actor_capabilities, 'direct_tasks_access');
    END IF;

    SELECT string_agg(required_function, ', ' ORDER BY required_function)
      INTO invalid_actor_dependencies
      FROM unnest(ARRAY[
          'renew_generation_lease(uuid,uuid,integer)',
          'update_generation_progress(uuid,uuid,text,jsonb)',
          'fail_generation_turn(uuid,uuid,text,text)',
          'commit_generation_turn_with_context_v2(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)',
          'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)',
          'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb)',
          'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb)',
          'close_generation_turn(uuid,uuid,uuid)'
      ]) AS required_function
      LEFT JOIN pg_catalog.pg_proc procedure
        ON procedure.oid = to_regprocedure('public.' || required_function)
      LEFT JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = procedure.proowner
     WHERE procedure.oid IS NULL
        OR owner_role.rolname <> 'everydayai_owner';
    IF invalid_actor_dependencies IS NOT NULL THEN
        RAISE EXCEPTION 'ACTOR_CORE_OWNER_CUTOVER_INCOMPLETE: %',
            invalid_actor_dependencies;
    END IF;

    SELECT string_agg(required_function, ', ' ORDER BY required_function)
      INTO invalid_worker_capabilities
      FROM unnest(ARRAY[
          'worker_discover_media_tasks',
          'worker_get_media_task',
          'worker_touch_media_task',
          'worker_claim_media_task_completion',
          'worker_settle_media_batch_item',
          'worker_discover_legacy_active_tasks',
          'worker_fail_legacy_stale_task',
          'worker_get_media_batch_message',
          'worker_commit_media_batch_message',
          'worker_commit_video_terminal',
          'worker_prepare_media_retry',
          'worker_abort_media_retry',
          'worker_commit_media_retry',
          'worker_record_error_log',
          'worker_record_media_metric',
          'worker_claim_due_scheduled_tasks',
          'worker_list_stale_scheduled_tasks',
          'worker_recover_stale_scheduled_task',
          'worker_create_scheduled_run',
          'worker_renew_scheduled_run',
          'worker_get_scheduled_task',
          'worker_append_scheduled_result_message',
          'worker_complete_scheduled_run',
          'worker_fail_scheduled_run',
          'worker_lock_scheduled_credits',
          'worker_settle_scheduled_credits'
      ]) AS required_function
     WHERE NOT EXISTS (
         SELECT 1
           FROM pg_catalog.pg_proc procedure
           JOIN pg_catalog.pg_namespace namespace
             ON namespace.oid = procedure.pronamespace
           JOIN pg_catalog.pg_roles owner_role
             ON owner_role.oid = procedure.proowner
          WHERE namespace.nspname = 'public'
            AND procedure.proname = required_function
            AND procedure.prosecdef
            AND owner_role.rolname = 'everydayai_owner'
            AND has_function_privilege(
                'everydayai_worker', procedure.oid, 'EXECUTE'
            )
     );
    IF invalid_worker_capabilities IS NOT NULL THEN
        RAISE EXCEPTION 'WORKER_CONTROL_CAPABILITY_CUTOVER_INCOMPLETE: %',
            invalid_worker_capabilities;
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
          , 'memory_pipeline_state', 'memory_session_logs',
          'memory_consolidation_runs', 'error_logs', 'knowledge_metrics',
          'knowledge_nodes', 'knowledge_edges', 'scoring_audit_log',
          'tool_audit_log', 'permission_audit_log',
          'wecom_callback_inbox', 'scheduled_tasks', 'scheduled_task_runs'
       ])
       AND owner_role.rolname <> 'everydayai_owner';
    IF unexpected_owners IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_OWNER_CUTOVER_INCOMPLETE: %',
            unexpected_owners;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM unnest(ARRAY[
              'error_logs',
              'knowledge_metrics',
              'scheduled_tasks',
              'scheduled_task_runs'
          ]) AS table_name
         WHERE has_table_privilege(
                   'everydayai_worker',
                   'public.' || table_name,
                   'SELECT'
               )
            OR has_table_privilege(
                   'everydayai_worker',
                   'public.' || table_name,
                   'INSERT'
               )
            OR has_table_privilege(
                   'everydayai_worker',
                   'public.' || table_name,
                   'UPDATE'
               )
            OR has_table_privilege(
                   'everydayai_worker',
                   'public.' || table_name,
                   'DELETE'
               )
    ) THEN
        RAISE EXCEPTION 'WORKER_CONTROL_DIRECT_TABLE_ACCESS_PRESENT';
    END IF;

    IF NOT has_table_privilege(
        'everydayai_runtime', 'public.scheduled_tasks',
        'SELECT, INSERT, UPDATE, DELETE'
    ) OR NOT has_table_privilege(
        'everydayai_runtime', 'public.scheduled_task_runs', 'SELECT'
    ) OR has_table_privilege(
        'everydayai_runtime', 'public.scheduled_task_runs',
        'INSERT, UPDATE, DELETE'
    ) THEN
        RAISE EXCEPTION 'WORKER_CONTROL_RUNTIME_ACL_INVALID';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES
              ('users', 'SELECT'), ('users', 'UPDATE'),
              ('organizations', 'SELECT'), ('org_members', 'SELECT'),
              ('org_configs', 'SELECT'), ('credits_history', 'SELECT'),
              ('conversations', 'SELECT'), ('conversations', 'INSERT'),
              ('conversations', 'UPDATE'), ('conversations', 'DELETE'),
              ('messages', 'SELECT'), ('messages', 'INSERT'),
              ('messages', 'UPDATE'), ('messages', 'DELETE'),
              ('tasks', 'SELECT'), ('tasks', 'INSERT'),
              ('tasks', 'UPDATE'), ('tasks', 'DELETE'),
              ('detail_projects', 'SELECT'), ('detail_projects', 'INSERT'),
              ('detail_projects', 'UPDATE'), ('detail_projects', 'DELETE'),
              ('detail_project_images', 'SELECT'),
              ('detail_project_images', 'INSERT'),
              ('detail_project_images', 'UPDATE'),
              ('detail_project_images', 'DELETE'),
              ('user_subscriptions', 'SELECT'),
              ('user_subscriptions', 'INSERT'),
              ('user_subscriptions', 'UPDATE'),
              ('user_subscriptions', 'DELETE'),
              ('user_memory_settings', 'SELECT'),
              ('user_memory_settings', 'INSERT'),
              ('user_memory_settings', 'UPDATE'),
              ('user_memory_settings', 'DELETE'),
              ('credit_transactions', 'SELECT'),
              ('credit_transactions', 'INSERT'),
              ('credit_transactions', 'UPDATE'),
              ('image_generations', 'SELECT'),
              ('image_generations', 'INSERT'),
              ('image_generations', 'UPDATE')
          ) AS required(table_name, privilege_name)
         WHERE NOT has_table_privilege(
                   'everydayai_runtime',
                   'public.' || required.table_name,
                   required.privilege_name
               )
    ) OR EXISTS (
        SELECT 1
          FROM (VALUES
              ('users', 'INSERT'), ('users', 'DELETE'),
              ('organizations', 'INSERT'), ('organizations', 'UPDATE'),
              ('organizations', 'DELETE'), ('org_members', 'INSERT'),
              ('org_members', 'UPDATE'), ('org_members', 'DELETE'),
              ('org_configs', 'INSERT'), ('org_configs', 'UPDATE'),
              ('org_configs', 'DELETE'), ('credits_history', 'INSERT'),
              ('credits_history', 'UPDATE'), ('credits_history', 'DELETE'),
              ('credit_transactions', 'DELETE'),
              ('image_generations', 'DELETE'),
              ('refresh_tokens', 'SELECT'), ('refresh_tokens', 'INSERT'),
              ('refresh_tokens', 'UPDATE'), ('refresh_tokens', 'DELETE'),
              ('wecom_user_mappings', 'SELECT'),
              ('wecom_user_mappings', 'INSERT'),
              ('wecom_user_mappings', 'UPDATE'),
              ('wecom_user_mappings', 'DELETE'),
              ('wecom_chat_targets', 'SELECT'),
              ('wecom_chat_targets', 'INSERT'),
              ('wecom_chat_targets', 'UPDATE'),
              ('wecom_chat_targets', 'DELETE')
          ) AS forbidden(table_name, privilege_name)
         WHERE has_table_privilege(
                   'everydayai_runtime',
                   'public.' || forbidden.table_name,
                   forbidden.privilege_name
               )
    ) THEN
        RAISE EXCEPTION 'WEB_RUNTIME_CORE_ACL_INVALID';
    END IF;

    SELECT string_agg(relation.relname, ', ' ORDER BY relation.relname)
      INTO unexpected_owners
      FROM pg_catalog.pg_class relation
     WHERE relation.oid = ANY(ARRAY[
         'public.memory_pipeline_state'::regclass,
         'public.memory_session_logs'::regclass,
         'public.memory_consolidation_runs'::regclass,
         'public.memory_atoms'::regclass,
         'public.knowledge_nodes'::regclass,
         'public.knowledge_edges'::regclass,
         'public.knowledge_metrics'::regclass,
         'public.scoring_audit_log'::regclass,
         'public.tool_audit_log'::regclass,
         'public.error_logs'::regclass,
         'public.permission_audit_log'::regclass,
         'public.wecom_callback_inbox'::regclass,
         'public.scheduled_tasks'::regclass,
         'public.scheduled_task_runs'::regclass
     ])
       AND NOT relation.relforcerowsecurity;
    IF unexpected_owners IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_FORCE_RLS_CUTOVER_INCOMPLETE: %',
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
               'everydayai_worker',
               'everydayai_sync'
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
