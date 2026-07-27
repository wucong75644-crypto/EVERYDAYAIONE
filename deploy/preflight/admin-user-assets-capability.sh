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
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.schema_migration_ledger
         WHERE identity = '209_platform_admin_user_assets_capability.sql'
           AND status = 'applied'
    ) THEN
        RAISE NOTICE 'admin_asset_capability=pending';
        RETURN;
    END IF;

    IF (
        SELECT procedure.oid IS NULL
            OR owner_role.rolname <> 'everydayai_owner'
            OR NOT procedure.prosecdef
            OR procedure.proconfig IS DISTINCT FROM
               ARRAY['search_path=pg_catalog, public']
            OR NOT has_function_privilege(
                'everydayai_runtime', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai_wecom_runtime', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai_worker', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai_sync', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'service_role', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai', procedure.oid, 'EXECUTE'
            )
            OR EXISTS (
                SELECT 1
                  FROM aclexplode(COALESCE(
                      procedure.proacl,
                      acldefault('f', procedure.proowner)
                  )) acl
                 WHERE acl.privilege_type = 'EXECUTE'
                   AND (
                       acl.grantee NOT IN (
                           procedure.proowner,
                           to_regrole('everydayai_runtime')::OID
                       )
                       OR acl.grantee =
                          to_regrole('everydayai_runtime')::OID
                          AND acl.is_grantable
                   )
            )
          FROM (SELECT to_regprocedure(
              'public.list_platform_admin_user_assets(uuid,text,text,integer,timestamp with time zone,uuid)'
          ) AS oid) expected
          LEFT JOIN pg_catalog.pg_proc procedure ON procedure.oid = expected.oid
          LEFT JOIN pg_catalog.pg_roles owner_role
            ON owner_role.oid = procedure.proowner
    ) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_CAPABILITY_INVALID';
    END IF;

    IF (
        SELECT procedure.oid IS NULL
            OR owner_role.rolname <> 'everydayai_owner'
            OR NOT procedure.prosecdef
            OR procedure.proconfig IS DISTINCT FROM
               ARRAY['search_path=pg_catalog, public']
            OR NOT has_function_privilege(
                'everydayai_runtime', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai_wecom_runtime', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai_worker', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai_sync', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'service_role', procedure.oid, 'EXECUTE'
            )
            OR has_function_privilege(
                'everydayai', procedure.oid, 'EXECUTE'
            )
            OR EXISTS (
                SELECT 1
                  FROM aclexplode(COALESCE(
                      procedure.proacl,
                      acldefault('f', procedure.proowner)
                  )) acl
                 WHERE acl.privilege_type = 'EXECUTE'
                   AND (
                       acl.grantee NOT IN (
                           procedure.proowner,
                           to_regrole('everydayai_runtime')::OID
                       )
                       OR acl.grantee =
                          to_regrole('everydayai_runtime')::OID
                          AND acl.is_grantable
                   )
            )
          FROM (SELECT to_regprocedure(
              'public.resolve_platform_admin_user_assets_download(uuid,jsonb)'
          ) AS oid) expected
          LEFT JOIN pg_catalog.pg_proc procedure ON procedure.oid = expected.oid
          LEFT JOIN pg_catalog.pg_roles owner_role
            ON owner_role.oid = procedure.proowner
    ) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_DOWNLOAD_CAPABILITY_INVALID';
    END IF;

    IF (
        SELECT procedure.oid IS NULL
            OR owner_role.rolname <> 'everydayai_owner'
            OR NOT procedure.prosecdef
            OR procedure.proconfig IS DISTINCT FROM
               ARRAY['search_path=pg_catalog, public']
            OR EXISTS (
                SELECT 1
                  FROM aclexplode(COALESCE(
                      procedure.proacl,
                      acldefault('f', procedure.proowner)
                  )) acl
                 WHERE acl.privilege_type = 'EXECUTE'
                   AND acl.grantee <> procedure.proowner
            )
          FROM (SELECT to_regprocedure(
              'public._list_admin_user_assets_owner(uuid,text,text,integer,timestamp with time zone,uuid)'
          ) AS oid) expected
          LEFT JOIN pg_catalog.pg_proc procedure ON procedure.oid = expected.oid
          LEFT JOIN pg_catalog.pg_roles owner_role
            ON owner_role.oid = procedure.proowner
    ) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_OWNER_CORE_INVALID';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM unnest(ARRAY['user_assets', 'user_asset_refs']) AS table_name
         WHERE has_table_privilege(
             'everydayai_runtime',
             'public.' || table_name,
             'SELECT, INSERT, UPDATE, DELETE'
         )
            OR has_any_column_privilege(
                'everydayai_runtime',
                'public.' || table_name,
                'SELECT, INSERT, UPDATE'
            )
    ) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_DIRECT_ACCESS_INVALID';
    END IF;
END
$preflight$;

ROLLBACK;
SQL
} | python3 "$(dirname "$0")/../run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 管理员资产数据库能力 ACL 检查通过"
