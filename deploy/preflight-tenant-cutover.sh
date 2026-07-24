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
    isolated_roles CONSTANT TEXT[] := ARRAY[
        'everydayai_config_import_reader',
        'everydayai_migrator',
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker'
    ];
    first_tables CONSTANT TEXT[] := ARRAY[
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
    ];
    second_tables CONSTANT TEXT[] := ARRAY[
        'users', 'organizations', 'org_members', 'org_configs',
        'org_invitations', 'wecom_user_mappings', 'wecom_chat_targets',
        'conversations', 'messages', 'tasks', 'credits_history',
        'credit_transactions', 'image_generations', 'detail_projects',
        'detail_project_images', 'refresh_tokens', 'user_subscriptions',
        'user_memory_settings'
    ];
    rls_tables CONSTANT TEXT[] := first_tables || ARRAY[
        'users', 'organizations', 'org_members', 'org_configs',
        'wecom_user_mappings', 'wecom_chat_targets', 'conversations',
        'messages', 'tasks', 'credits_history', 'credit_transactions',
        'image_generations', 'detail_projects', 'detail_project_images',
        'refresh_tokens', 'user_subscriptions', 'user_memory_settings'
    ];
    sensitive_tables CONSTANT TEXT[] := ARRAY[
        'governance_audit_log', 'secret_records',
        'configuration_entries', 'configuration_policies',
        'configuration_import_audit_log'
    ];
    expected_migrations CONSTANT TEXT[] := ARRAY[
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
        '161_configuration_legacy_import.sql'
    ];
    expected_checksums CONSTANT TEXT[] := ARRAY[
        '60d765312928e92b525197b778fb64c505c59c60d104c4d7281e2ab713ceface',
        '8888dbe43e0479cf4f4b833de3fa03e1ab5fc4c0758564106b0242144069833c',
        '20f05628c130f1335f9e281dd07cefe6eeb76f99bf49768a4c814ecaba683b8b',
        '88bd8e231c9a7f53dba4cfe94a4a21dcd0a992edf9d096e40957274e6ce6a5a8',
        '999ffed8ef242ec4fe091c2bc56312e57bd712e9cf40be5001e2b605bd2230fb',
        '0b26a94f5160e091dd49ca8b37c451b1fdf936adb46f60e95a1104cd40d0b89c',
        '1f6e12aeef8e0d015ed66a97f4706df32143215ed2b13b616f933d2fee30891f',
        '4a0a14b23d49010c4866a9f94839f8f390b80b71d31f571570e5548275a40044',
        '7bf205f39655a89626ba7dd99d28408ca9b63790b916e3b08e222f75195a6253',
        'd6d2def1a21a500796b703457e2871bc47bda093f6ad2b97999f61a876ef97f2',
        '72567b9ec906dff5d05c87a0c1d340d59d989cee482a1ddd913d6416e026771c',
        '88bf3ce251a44d510b475334874bc070efe618658909197f39b93e1bdf09122f',
        'e6eea45babe80d13c294ac0e78d33bfe777a59cb4365426d44cb92daaa2cb18c',
        '134ea6fa6f4a769e2e9aac121642849dbd37317193b1e610053f0ed3d75fcfc7'
    ];
    key_functions CONSTANT TEXT[] := ARRAY[
        'register_user_asset(uuid,text,text,text,text,text,text,text,text,text,text,text,bigint,text,jsonb,text,uuid,text,text,text,uuid,uuid,uuid,uuid,uuid,integer,text,text,jsonb,timestamp with time zone)',
        'wecom_get_or_create_user(text,text,uuid,text,text)',
        'prepare_generation(uuid,text,uuid,uuid,uuid,uuid,jsonb,jsonb,jsonb)',
        'enqueue_generation_turn(jsonb,uuid,uuid,text,jsonb,uuid)'
    ];
    existing_isolated_roles INTEGER;
    applied_migrations INTEGER;
    first_owner_count INTEGER;
    second_owner_count INTEGER;
    invalid_items TEXT;
    source_orgs BIGINT;
    source_configs BIGINT;
    source_credentials BIGINT;
    target_rows BIGINT := 0;
BEGIN
    IF session_user IN (
        'everydayai_owner', 'everydayai_config_import_reader',
        'everydayai_migrator', 'everydayai_runtime',
        'everydayai_wecom_runtime', 'everydayai_worker', '${legacy_owner}'
    ) THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_PREFLIGHT_REQUIRES_ADMIN';
    END IF;
    IF to_regclass('public.schema_migration_ledger') IS NULL THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_MIGRATION_LEDGER_MISSING';
    END IF;

    SELECT COUNT(*) INTO existing_isolated_roles
      FROM pg_catalog.pg_roles WHERE rolname = ANY(isolated_roles);
    IF existing_isolated_roles NOT IN (0, cardinality(isolated_roles))
       OR existing_isolated_roles = 0 AND EXISTS (
           SELECT 1 FROM pg_catalog.pg_roles
            WHERE rolname = 'everydayai_owner'
       ) THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_ROLE_SET_PARTIAL: %',
            existing_isolated_roles;
    END IF;
    IF existing_isolated_roles = cardinality(isolated_roles) THEN
        SELECT string_agg(role_row.rolname, ', ' ORDER BY role_row.rolname)
          INTO invalid_items
          FROM pg_catalog.pg_roles role_row
         WHERE role_row.rolname = ANY(isolated_roles)
           AND (
               NOT role_row.rolcanlogin
               OR role_row.rolsuper OR role_row.rolcreatedb
               OR role_row.rolcreaterole OR role_row.rolreplication
               OR role_row.rolbypassrls
           );
        IF invalid_items IS NOT NULL THEN
            RAISE EXCEPTION 'TENANT_CUTOVER_ROLE_CONTRACT_INVALID: %',
                invalid_items;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
             WHERE rolname = 'everydayai_owner'
               AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        ) THEN
            RAISE EXCEPTION 'TENANT_CUTOVER_OWNER_ROLE_INVALID';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_auth_members membership
              JOIN pg_catalog.pg_roles granted_role
                ON granted_role.oid = membership.roleid
              JOIN pg_catalog.pg_roles member_role
                ON member_role.oid = membership.member
             WHERE granted_role.rolname = 'everydayai_owner'
               AND member_role.rolname = ANY(ARRAY[
                   'everydayai_config_import_reader',
                   'everydayai_runtime',
                   'everydayai_wecom_runtime',
                   'everydayai_worker'
               ])
        ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_auth_members membership
              JOIN pg_catalog.pg_roles granted_role
                ON granted_role.oid = membership.roleid
              JOIN pg_catalog.pg_roles member_role
                ON member_role.oid = membership.member
             WHERE granted_role.rolname = 'everydayai_owner'
               AND member_role.rolname = 'everydayai_migrator'
        ) THEN
            RAISE EXCEPTION 'TENANT_CUTOVER_OWNER_MEMBERSHIP_INVALID';
        END IF;
    END IF;

    SELECT string_agg(required_table, ', ' ORDER BY required_table)
      INTO invalid_items
      FROM unnest(
          first_tables || second_tables || ARRAY[
              'kuaimai_external_credentials'
          ]
      ) AS required_table
     WHERE to_regclass('public.' || required_table) IS NULL;
    IF invalid_items IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_TABLE_MISSING: %', invalid_items;
    END IF;
    SELECT string_agg(required_function, ', ' ORDER BY required_function)
      INTO invalid_items
      FROM unnest(key_functions) AS required_function
     WHERE to_regprocedure('public.' || required_function) IS NULL;
    IF invalid_items IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_FUNCTION_MISSING: %', invalid_items;
    END IF;

    SELECT string_agg(
               expected.identity || '=' || COALESCE(ledger.status, 'pending'),
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
        RAISE EXCEPTION 'TENANT_CUTOVER_MIGRATION_INVALID: %', invalid_items;
    END IF;
    SELECT COUNT(*) INTO applied_migrations
      FROM public.schema_migration_ledger
     WHERE identity = ANY(expected_migrations) AND status = 'applied';
    IF applied_migrations NOT IN (0, cardinality(expected_migrations)) THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_MIGRATION_PARTIAL: %',
            applied_migrations;
    END IF;

    SELECT COUNT(*) INTO first_owner_count
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = relation.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
     WHERE namespace.nspname = 'public'
       AND relation.relname = ANY(first_tables)
       AND owner_role.rolname = 'everydayai_owner';
    SELECT COUNT(*) INTO second_owner_count
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = relation.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
     WHERE namespace.nspname = 'public'
       AND relation.relname = ANY(second_tables)
       AND owner_role.rolname = 'everydayai_owner';
    IF NOT (
        first_owner_count = 0 AND second_owner_count = 0
        OR first_owner_count = cardinality(first_tables)
           AND second_owner_count = cardinality(second_tables)
    ) THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_OWNERSHIP_PARTIAL: first=%, second=%',
            first_owner_count, second_owner_count;
    END IF;
    SELECT string_agg(
               relation.relname || '=' || owner_role.rolname,
               ', ' ORDER BY relation.relname
           )
      INTO invalid_items
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = relation.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
     WHERE namespace.nspname = 'public'
       AND relation.relname = ANY(first_tables || second_tables)
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF invalid_items IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_OWNER_UNEXPECTED: %', invalid_items;
    END IF;
    SELECT string_agg(
               procedure.oid::regprocedure::TEXT || '=' || owner_role.rolname,
               ', ' ORDER BY procedure.oid::regprocedure::TEXT
           )
      INTO invalid_items
      FROM unnest(key_functions) AS required_function
      JOIN pg_catalog.pg_proc procedure
        ON procedure.oid = to_regprocedure('public.' || required_function)
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = procedure.proowner
     WHERE owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner')
        OR first_owner_count = cardinality(first_tables)
           AND owner_role.rolname <> 'everydayai_owner';
    IF invalid_items IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_FUNCTION_OWNER_INVALID: %',
            invalid_items;
    END IF;
    IF first_owner_count = cardinality(first_tables)
       AND (
           SELECT owner_role.rolname
             FROM pg_catalog.pg_class relation
             JOIN pg_catalog.pg_roles owner_role
               ON owner_role.oid = relation.relowner
            WHERE relation.oid = 'public.schema_migration_ledger'::regclass
       ) <> 'everydayai_owner' THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_LEDGER_OWNER_INVALID';
    END IF;
    IF first_owner_count > 0
       AND existing_isolated_roles <> cardinality(isolated_roles) THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_ROLES_REQUIRED_AFTER_OWNERSHIP';
    END IF;
    IF applied_migrations > 0
       AND first_owner_count <> cardinality(first_tables) THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_OWNERS_REQUIRED_BEFORE_MIGRATIONS';
    END IF;

    IF applied_migrations = cardinality(expected_migrations) THEN
        SELECT string_agg(required_table, ', ' ORDER BY required_table)
          INTO invalid_items
          FROM unnest(rls_tables) AS required_table
          LEFT JOIN pg_catalog.pg_class relation
            ON relation.oid = to_regclass('public.' || required_table)
         WHERE NOT COALESCE(relation.relrowsecurity, FALSE);
        IF invalid_items IS NOT NULL THEN
            RAISE EXCEPTION 'TENANT_CUTOVER_RLS_INCOMPLETE: %', invalid_items;
        END IF;
        SELECT string_agg(required_table, ', ' ORDER BY required_table)
          INTO invalid_items
          FROM unnest(sensitive_tables) AS required_table
          LEFT JOIN pg_catalog.pg_class relation
            ON relation.oid = to_regclass('public.' || required_table)
         WHERE NOT COALESCE(relation.relforcerowsecurity, FALSE);
        IF invalid_items IS NOT NULL THEN
            RAISE EXCEPTION 'TENANT_CUTOVER_FORCE_RLS_INCOMPLETE: %',
                invalid_items;
        END IF;
    ELSE
        SELECT string_agg(relation.relname, ', ' ORDER BY relation.relname)
          INTO invalid_items
          FROM pg_catalog.pg_class relation
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'public'
           AND relation.relname = ANY(rls_tables)
           AND relation.relforcerowsecurity;
        IF invalid_items IS NOT NULL THEN
            RAISE EXCEPTION 'TENANT_CUTOVER_FORCE_RLS_UNEXPECTED: %',
                invalid_items;
        END IF;
    END IF;

    SELECT COUNT(*) INTO source_orgs FROM public.organizations;
    SELECT COUNT(*) INTO source_configs FROM public.org_configs;
    SELECT COUNT(*) INTO source_credentials
      FROM public.kuaimai_external_credentials;
    IF applied_migrations = cardinality(expected_migrations) THEN
        EXECUTE
            'SELECT (SELECT COUNT(*) FROM public.configuration_entries)'
            ' + (SELECT COUNT(*) FROM public.configuration_policies)'
            ' + (SELECT COUNT(*) FROM public.secret_records)'
            ' + (SELECT COUNT(*) FROM public.configuration_import_audit_log)'
          INTO target_rows;
    END IF;
    RAISE NOTICE
        'cutover_counts organizations=% org_configs=% credentials=% target_rows=%',
        source_orgs, source_configs, source_credentials, target_rows;
    RAISE NOTICE 'cutover_stage=%',
        CASE
            WHEN applied_migrations = cardinality(expected_migrations)
                THEN 'migrations_applied'
            WHEN first_owner_count = cardinality(first_tables)
             AND second_owner_count = cardinality(second_tables)
                THEN 'owners_ready'
            ELSE 'pre_ownership'
        END;
END
\$preflight\$;

SELECT usename AS database_role, COUNT(*) AS connection_count
  FROM pg_catalog.pg_stat_activity
 WHERE usename = ANY(ARRAY[
     '${legacy_owner}', 'everydayai_runtime', 'everydayai_wecom_runtime',
     'everydayai_worker', 'everydayai_migrator',
     'everydayai_config_import_reader'
 ])
 GROUP BY usename
 ORDER BY usename;

ROLLBACK;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 150–161 生产租户切换只读前置检查通过"
