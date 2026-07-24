#!/bin/bash

set -euo pipefail

if [ -z "${TENANT_DB_ADMIN_URL:-}" ]; then
    echo "❌ 缺少 TENANT_DB_ADMIN_URL" >&2
    exit 1
fi

{
    cat <<'SQL'
\set ON_ERROR_STOP on
BEGIN;
SET TRANSACTION READ ONLY;

DO $preflight$
DECLARE
    missing_migrations TEXT;
    invalid_roles TEXT;
    invalid_rls TEXT;
    target_rows BIGINT;
    function_owner TEXT;
BEGIN
    IF session_user IN (
        'everydayai_owner',
        'everydayai_config_import_reader',
        'everydayai_migrator',
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker',
        'everydayai'
    ) THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_PREFLIGHT_REQUIRES_ADMIN';
    END IF;

    IF to_regclass('public.schema_migration_ledger') IS NULL THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_MIGRATION_LEDGER_MISSING';
    END IF;

    SELECT string_agg(required_identity, ', ' ORDER BY required_identity)
      INTO missing_migrations
      FROM unnest(ARRAY[
          '158_configuration_control_plane_foundation.sql',
          '159_configuration_management_core.sql',
          '159_configuration_management_facades.sql',
          '160_configuration_resolution_core.sql',
          '160_configuration_resolution_facades.sql',
          '161_configuration_legacy_import.sql'
      ]) AS required_identity
     WHERE NOT EXISTS (
         SELECT 1
           FROM public.schema_migration_ledger ledger
          WHERE ledger.identity = required_identity
            AND ledger.status = 'applied'
     );
    IF missing_migrations IS NOT NULL THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_MIGRATIONS_MISSING: %',
            missing_migrations;
    END IF;

    IF to_regclass('public.organizations') IS NULL
       OR to_regclass('public.org_configs') IS NULL
       OR to_regclass('public.kuaimai_external_credentials') IS NULL THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_LEGACY_SOURCE_TABLE_MISSING';
    END IF;

    SELECT string_agg(required_role, ', ' ORDER BY required_role)
      INTO invalid_roles
      FROM unnest(ARRAY[
          'everydayai_owner',
          'everydayai_config_import_reader',
          'everydayai_migrator',
          'everydayai_runtime',
          'everydayai_wecom_runtime',
          'everydayai_worker'
      ]) AS required_role
      LEFT JOIN pg_catalog.pg_roles role_row
        ON role_row.rolname = required_role
     WHERE role_row.oid IS NULL
        OR role_row.rolsuper
        OR role_row.rolbypassrls
        OR (required_role = 'everydayai_owner' AND role_row.rolcanlogin)
        OR (required_role IN (
                'everydayai_config_import_reader',
                'everydayai_migrator'
            )
            AND NOT role_row.rolcanlogin);
    IF invalid_roles IS NOT NULL THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_ROLE_CONTRACT_INVALID: %',
            invalid_roles;
    END IF;

    SELECT owner_role.rolname
      INTO function_owner
      FROM pg_catalog.pg_proc procedure
      JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = procedure.proowner
     WHERE procedure.oid = to_regprocedure(
         'public.import_legacy_configuration_batch(uuid,jsonb)'
     );
    IF function_owner IS DISTINCT FROM 'everydayai_owner' THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_FUNCTION_OWNER_INVALID';
    END IF;
    SELECT owner_role.rolname
      INTO function_owner
      FROM pg_catalog.pg_proc procedure
      JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = procedure.proowner
     WHERE procedure.oid = to_regprocedure(
         'public.export_legacy_configuration_snapshot()'
     );
    IF function_owner IS DISTINCT FROM 'everydayai_owner' THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_FUNCTION_OWNER_INVALID';
    END IF;
    IF NOT has_function_privilege(
           'everydayai_migrator',
           'public.import_legacy_configuration_batch(uuid,jsonb)',
           'EXECUTE'
       )
       OR has_function_privilege(
           0::OID,
           'public.import_legacy_configuration_batch(uuid,jsonb)',
           'EXECUTE'
       )
       OR has_function_privilege(
           'everydayai_runtime',
           'public.import_legacy_configuration_batch(uuid,jsonb)',
           'EXECUTE'
       )
       OR has_function_privilege(
           'everydayai_wecom_runtime',
           'public.import_legacy_configuration_batch(uuid,jsonb)',
           'EXECUTE'
       )
       OR has_function_privilege(
           'everydayai_worker',
           'public.import_legacy_configuration_batch(uuid,jsonb)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_FUNCTION_GRANT_INVALID';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc procedure
          CROSS JOIN LATERAL aclexplode(procedure.proacl) acl
         WHERE procedure.oid = to_regprocedure(
             'public.export_legacy_configuration_snapshot()'
         )
           AND acl.grantee = (
               SELECT oid FROM pg_catalog.pg_roles
                WHERE rolname = 'everydayai_config_import_reader'
           )
           AND acl.privilege_type = 'EXECUTE'
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc procedure
          CROSS JOIN LATERAL aclexplode(procedure.proacl) acl
         WHERE procedure.oid = to_regprocedure(
             'public.export_legacy_configuration_snapshot()'
         )
           AND acl.grantee = ANY(ARRAY[
               0::OID,
               (SELECT oid FROM pg_catalog.pg_roles
                 WHERE rolname = 'everydayai_migrator'),
               (SELECT oid FROM pg_catalog.pg_roles
                 WHERE rolname = 'everydayai_runtime'),
               (SELECT oid FROM pg_catalog.pg_roles
                 WHERE rolname = 'everydayai_wecom_runtime'),
               (SELECT oid FROM pg_catalog.pg_roles
                 WHERE rolname = 'everydayai_worker')
           ])
           AND acl.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_FUNCTION_GRANT_INVALID';
    END IF;
    IF has_table_privilege(
           'everydayai_config_import_reader',
           'public.organizations',
           'SELECT'
       )
       OR has_table_privilege(
           'everydayai_config_import_reader',
           'public.org_configs',
           'SELECT'
       )
       OR has_table_privilege(
           'everydayai_config_import_reader',
           'public.kuaimai_external_credentials',
           'SELECT'
       ) THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_READER_TABLE_ACCESS_INVALID';
    END IF;

    SELECT string_agg(required_table, ', ' ORDER BY required_table)
      INTO invalid_rls
      FROM unnest(ARRAY[
          'secret_records',
          'configuration_entries',
          'configuration_policies',
          'configuration_import_audit_log'
      ]) AS required_table
      LEFT JOIN pg_catalog.pg_class relation
        ON relation.oid = to_regclass('public.' || required_table)
     WHERE relation.oid IS NULL
        OR NOT relation.relrowsecurity
        OR NOT relation.relforcerowsecurity;
    IF invalid_rls IS NOT NULL THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_FORCE_RLS_INVALID: %', invalid_rls;
    END IF;

    IF (SELECT COUNT(*) FROM public.configuration_definitions) <> 15
       OR (SELECT COUNT(*) FROM public.configuration_bundle_definitions) <> 11
    THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_REGISTRY_COUNT_INVALID';
    END IF;

    SELECT
        (SELECT COUNT(*) FROM public.configuration_entries)
        + (SELECT COUNT(*) FROM public.configuration_policies)
        + (SELECT COUNT(*) FROM public.secret_records)
        + (SELECT COUNT(*) FROM public.configuration_import_audit_log)
      INTO target_rows;
    IF target_rows <> 0 THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_TARGET_NOT_EMPTY: %', target_rows;
    END IF;
END
$preflight$;

ROLLBACK;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 旧配置导入数据库只读前置检查通过"
