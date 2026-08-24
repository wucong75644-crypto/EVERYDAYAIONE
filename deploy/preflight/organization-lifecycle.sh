#!/bin/bash
set -euo pipefail

if [ -z "${TENANT_DB_ADMIN_URL:-}" ]; then
    echo "❌ 缺少 TENANT_DB_ADMIN_URL" >&2
    exit 1
fi

psql "$TENANT_DB_ADMIN_URL" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
SET TRANSACTION READ ONLY;

DO $preflight$
DECLARE
    function_name TEXT;
    procedure_oid REGPROCEDURE;
    denied_role TEXT;
    required_role TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration_ledger
         WHERE identity = '232_organization_lifecycle_runtime_role_closure.sql'
           AND status = 'applied'
    ) THEN
        RAISE NOTICE 'organization_lifecycle=pending';
        RETURN;
    END IF;

    FOREACH function_name IN ARRAY ARRAY[
        'suspend_governed_organization(uuid)',
        'restore_governed_organization(uuid)'
    ] LOOP
        procedure_oid := to_regprocedure('public.' || function_name);
        IF procedure_oid IS NULL
           OR NOT has_function_privilege(
               'everydayai_runtime', procedure_oid, 'EXECUTE'
           )
           OR has_function_privilege(
               'everydayai_worker', procedure_oid, 'EXECUTE'
           )
           OR has_function_privilege(
               'everydayai_wecom_runtime', procedure_oid, 'EXECUTE'
           )
           OR has_function_privilege(
               'everydayai_sync', procedure_oid, 'EXECUTE'
           )
           OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc procedure
                 JOIN pg_catalog.pg_roles owner_role
                   ON owner_role.oid = procedure.proowner
                WHERE procedure.oid = procedure_oid
                  AND (
                      owner_role.rolname <> 'everydayai_owner'
                      OR NOT procedure.prosecdef
                      OR procedure.proconfig IS DISTINCT FROM ARRAY[
                          'search_path=pg_catalog, public'
                      ]::TEXT[]
                  )
           )
           OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc procedure
                 CROSS JOIN LATERAL aclexplode(
                     COALESCE(
                         procedure.proacl,
                         acldefault('f', procedure.proowner)
                     )
                 ) acl
                WHERE procedure.oid = procedure_oid
                  AND (
                      acl.grantee = 0
                      OR (
                          acl.grantee = 'everydayai_runtime'::regrole
                          AND acl.is_grantable
                      )
                  )
           ) THEN
            RAISE EXCEPTION 'organization lifecycle ACL mismatch: %',
                function_name;
        END IF;

        FOREACH denied_role IN ARRAY ARRAY[
            'everydayai_worker',
            'everydayai_wecom_runtime',
            'everydayai_sync',
            'service_role',
            'everydayai'
        ] LOOP
            IF to_regrole(denied_role) IS NOT NULL
               AND has_function_privilege(
                   denied_role, procedure_oid, 'EXECUTE'
               ) THEN
                RAISE EXCEPTION
                    'organization lifecycle denied role mismatch: % %',
                    function_name, denied_role;
            END IF;
        END LOOP;
    END LOOP;

    IF to_regprocedure(
        'public.reject_suspended_organization_service_write()'
    ) IS NULL THEN
        RAISE EXCEPTION 'suspended organization execution fence missing';
    END IF;
    FOREACH function_name IN ARRAY ARRAY[
        'public.reject_suspended_organization_service_write()',
        'public.reject_suspended_delivery_service_write()'
    ] LOOP
        procedure_oid := to_regprocedure(function_name);
        FOREACH required_role IN ARRAY ARRAY[
            'everydayai_agent_runtime_worker',
            'everydayai_projection_worker',
            'everydayai_authorization_worker',
            'everydayai_sandbox_worker',
            'everydayai_runtime_admin'
        ] LOOP
            IF position(
                quote_literal(required_role)
                IN pg_get_functiondef(procedure_oid)
            ) = 0 THEN
                RAISE EXCEPTION
                    'suspended organization runtime role fence incomplete: % %',
                    function_name, required_role;
            END IF;
        END LOOP;
    END LOOP;
    IF (
        SELECT count(*)
          FROM pg_catalog.pg_trigger
         WHERE tgname = ANY(ARRAY[
             'tasks_suspended_organization_fence',
             'scheduled_tasks_suspended_organization_fence',
             'scheduled_task_runs_suspended_organization_fence',
             'agent_runtime_sessions_suspended_organization_fence',
             'agent_session_commands_suspended_organization_fence',
             'agent_runs_suspended_organization_fence',
             'agent_run_attempts_suspended_organization_fence',
             'agent_model_steps_suspended_organization_fence',
             'agent_runtime_events_suspended_organization_fence',
             'agent_projection_outbox_suspended_organization_fence',
             'wecom_callback_inbox_suspended_organization_fence',
             'conversation_deliveries_suspended_organization_fence'
         ])
           AND NOT tgisinternal
    ) <> 12 THEN
        RAISE EXCEPTION 'suspended organization trigger set incomplete';
    END IF;
    IF has_table_privilege(
        'everydayai_runtime', 'public.organizations', 'UPDATE'
    ) THEN
        RAISE EXCEPTION 'runtime must not directly update organizations';
    END IF;
END;
$preflight$;

ROLLBACK;
SQL

echo "✅ 企业停用与恢复数据库能力只读检查通过"
