#!/bin/bash

set -euo pipefail

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
SET TRANSACTION READ ONLY;

DO \$preflight\$
DECLARE
    memory_tables CONSTANT TEXT[] := ARRAY[
        'memory_pipeline_state',
        'memory_session_logs',
        'memory_consolidation_runs',
        'memory_atoms'
    ];
    worker_tables CONSTANT TEXT[] := ARRAY[
        'error_logs',
        'knowledge_metrics',
        'scheduled_tasks',
        'scheduled_task_runs'
    ];
    expected_migrations CONSTANT TEXT[] := ARRAY[
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
        '180_scheduled_task_tenant_boundary.sql'
    ];
    expected_checksums CONSTANT TEXT[] := ARRAY[
        '59caf33fade39bcf21c26d52fc9d718f865db8cd4c7cfe505f005060e115375f',
        '40fe4eb080690247ebf3cd594777f86172f5245d49c8eadab3f937a97ff0a806',
        'd89602bf62ee2ce6b34eabdfb952067cca16a3cda05538f5a3f8e719eca3a693',
        '036e2d62dd70d0a8225c73bec934e44a67e2ea04ff264bfefbc62eef499c4148',
        '3ab57c723ccf58e815bf9be1995848730393b8cc1889113ecd7342bd01f1192e',
        '8354c744ee41c8023a2dd8c000165dc3b02aad9c0330ab0ec2931acf67f49094',
        'a8503fd25b53ed2c015db34314c0a680d9c456061f94cfa4606d2d8c1fa3cfa9',
        'e0fb6f08a7acd6b2968e40c346aaef2c8aa2152895bdd3873cfb1eb03ec9db1b',
        'ed488f31e20beb74b5ae500826818a91ee6c3c6aee57eb5d332caa35c6ee2f79',
        'd0f2f4514d34a75566074ab918177b1a4d66151e8390ae35382a16ec17c54800',
        'a8ee958f59f91c0923d2302ae26f3b6213a449fe0e0e7b6f6dfe1e05db4a82f6',
        '403bb59977ce95ce8bd4bb0bb6895d116a32fb22f40b2f7465b40a51ea41fb01',
        '1858f72f2d853f23effaba1a403e673234651ed94db8dfbfde8960aeacb478e7',
        'e4ada4b00dd95c816d6e8e7d0eb4bb2d84cc0619abad8a083995c1ff62678234',
        '6580ab822f6dda8f42c82fae0dcf67b254dd8742174339301c256c3dd53a9bf4',
        '92943c0e77d14e8d85f0d3308e21c24725d3121d4bae295a1979fc3a33a7b3fb'
    ];
    worker_functions CONSTANT TEXT[] := ARRAY[
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
    ];
    invalid_items TEXT;
    memory_owner_count INTEGER;
    worker_owner_count INTEGER;
    worker_migration_count INTEGER;
BEGIN
    IF session_user IN (
        'everydayai_owner', 'everydayai_migrator',
        'everydayai_runtime', 'everydayai_wecom_runtime',
        'everydayai_worker', '${legacy_owner}'
    ) THEN
        RAISE EXCEPTION 'WORKER_CONTROL_PREFLIGHT_REQUIRES_ADMIN';
    END IF;

    SELECT string_agg(required_table, ', ' ORDER BY required_table)
      INTO invalid_items
      FROM unnest(memory_tables || worker_tables) AS required_table
     WHERE to_regclass('public.' || required_table) IS NULL;
    IF invalid_items IS NOT NULL THEN
        RAISE EXCEPTION 'WORKER_CONTROL_TABLE_MISSING: %', invalid_items;
    END IF;

    SELECT string_agg(
               expected.identity || '=' || ledger.status,
               ', ' ORDER BY expected.identity
           )
      INTO invalid_items
      FROM unnest(expected_migrations, expected_checksums)
           AS expected(identity, checksum)
      JOIN public.schema_migration_ledger ledger
        ON ledger.identity = expected.identity
     WHERE ledger.status <> 'applied'
        OR ledger.checksum_sha256 <> expected.checksum;
    IF invalid_items IS NOT NULL THEN
        RAISE EXCEPTION 'WORKER_CONTROL_MIGRATION_INVALID: %', invalid_items;
    END IF;

    IF (
        SELECT COUNT(*)
          FROM public.schema_migration_ledger
         WHERE identity = ANY(expected_migrations[1:6])
           AND status = 'applied'
    ) <> 6 THEN
        RAISE EXCEPTION 'WORKER_CONTROL_FOUNDATION_MIGRATIONS_INCOMPLETE';
    END IF;

    SELECT COUNT(*) INTO worker_migration_count
      FROM public.schema_migration_ledger
     WHERE identity = ANY(expected_migrations[7:16])
       AND status = 'applied';
    IF worker_migration_count NOT IN (0, 10) THEN
        RAISE EXCEPTION 'WORKER_CONTROL_MIGRATION_PARTIAL: %',
            worker_migration_count;
    END IF;

    SELECT COUNT(*) INTO memory_owner_count
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
     WHERE relation.oid = ANY(
         SELECT to_regclass('public.' || table_name)
           FROM unnest(memory_tables) AS table_name
     )
       AND owner_role.rolname = 'everydayai_owner';
    IF memory_owner_count <> cardinality(memory_tables) THEN
        RAISE EXCEPTION 'WORKER_CONTROL_MEMORY_OWNER_INCOMPLETE: %',
            memory_owner_count;
    END IF;

    SELECT COUNT(*) INTO worker_owner_count
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
     WHERE relation.oid = ANY(
         SELECT to_regclass('public.' || table_name)
           FROM unnest(worker_tables) AS table_name
     )
       AND owner_role.rolname = 'everydayai_owner';
    IF worker_owner_count NOT IN (0, cardinality(worker_tables)) THEN
        RAISE EXCEPTION 'WORKER_CONTROL_OWNER_PARTIAL: %',
            worker_owner_count;
    END IF;
    IF worker_migration_count > 0
       AND worker_owner_count <> cardinality(worker_tables) THEN
        RAISE EXCEPTION 'WORKER_CONTROL_OWNER_REQUIRED_BEFORE_MIGRATIONS';
    END IF;

    IF worker_migration_count = 10 THEN
        SELECT string_agg(required_function, ', ' ORDER BY required_function)
          INTO invalid_items
          FROM unnest(worker_functions) AS required_function
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
        IF invalid_items IS NOT NULL THEN
            RAISE EXCEPTION 'WORKER_CONTROL_CAPABILITY_INCOMPLETE: %',
                invalid_items;
        END IF;

        IF EXISTS (
            SELECT 1
              FROM unnest(worker_tables) AS table_name
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
                OR has_any_column_privilege(
                    'everydayai_worker',
                    'public.' || table_name,
                    'SELECT'
                )
                OR has_any_column_privilege(
                    'everydayai_worker',
                    'public.' || table_name,
                    'INSERT'
                )
                OR has_any_column_privilege(
                    'everydayai_worker',
                    'public.' || table_name,
                    'UPDATE'
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

        SELECT string_agg(relation.relname, ', ' ORDER BY relation.relname)
          INTO invalid_items
          FROM pg_catalog.pg_class relation
         WHERE relation.oid = ANY(ARRAY[
             'public.memory_pipeline_state'::regclass,
             'public.memory_session_logs'::regclass,
             'public.memory_consolidation_runs'::regclass,
             'public.memory_atoms'::regclass,
             'public.scheduled_tasks'::regclass,
             'public.scheduled_task_runs'::regclass
         ])
           AND NOT relation.relforcerowsecurity;
        IF invalid_items IS NOT NULL THEN
            RAISE EXCEPTION 'WORKER_CONTROL_FORCE_RLS_INCOMPLETE: %',
                invalid_items;
        END IF;
    END IF;

    RAISE NOTICE 'worker_control_stage=%',
        CASE
            WHEN worker_migration_count = 10 THEN 'migrations_applied'
            WHEN worker_owner_count = cardinality(worker_tables)
                THEN 'owners_ready'
            ELSE 'pre_ownership'
        END;
END
\$preflight\$;

ROLLBACK;
SQL
} | python3 "$(dirname "$0")/../run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 165–180 Worker Control 只读前置检查通过"
