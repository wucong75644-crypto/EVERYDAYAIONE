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
        'everydayai_worker',
        'everydayai_sync'
    ];
    existing_isolated_roles INTEGER;
    invalid_items TEXT;
BEGIN
    IF session_user IN (
        'everydayai_owner', 'everydayai_config_import_reader',
        'everydayai_migrator', 'everydayai_runtime',
        'everydayai_wecom_runtime', 'everydayai_worker',
        'everydayai_sync', '${legacy_owner}'
    ) THEN
        RAISE EXCEPTION 'TENANT_CUTOVER_PREFLIGHT_REQUIRES_ADMIN';
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
                   'everydayai_worker',
                   'everydayai_sync'
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
        IF has_function_privilege(
            'everydayai_runtime',
            'public.enqueue_generation_turn(jsonb,uuid,uuid,text,jsonb,uuid)',
            'EXECUTE'
        ) THEN
            RAISE EXCEPTION
                'TENANT_CUTOVER_RUNTIME_LEGACY_ENQUEUE_UNEXPECTED';
        END IF;
    END IF;
END
\$preflight\$;

ROLLBACK;
SQL
} | python3 "$(dirname "$0")/../run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 租户隔离角色与 Runtime 旧入队权限检查通过"
