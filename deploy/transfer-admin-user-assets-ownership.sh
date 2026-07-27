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

DO \$transfer\$
DECLARE
    function_identity CONSTANT TEXT :=
        'public.list_admin_user_assets(uuid,text,text,integer,timestamp with time zone,uuid)';
    current_owner TEXT;
BEGIN
    IF session_user IN (
        'everydayai_owner', 'everydayai_config_import_reader',
        'everydayai_migrator', 'everydayai_runtime',
        'everydayai_wecom_runtime', 'everydayai_worker',
        'everydayai_sync', '${legacy_owner}'
    ) THEN
        RAISE EXCEPTION 'ADMIN_ASSET_OWNER_TRANSFER_REQUIRES_ADMIN';
    END IF;
    IF to_regrole('everydayai_owner') IS NULL
       OR to_regrole('${legacy_owner}') IS NULL THEN
        RAISE EXCEPTION 'ADMIN_ASSET_OWNER_TRANSFER_ROLE_MISSING';
    END IF;
    IF to_regprocedure(function_identity) IS NULL THEN
        RAISE EXCEPTION 'ADMIN_ASSET_OWNER_TRANSFER_FUNCTION_MISSING';
    END IF;

    SELECT owner_role.rolname
      INTO current_owner
      FROM pg_catalog.pg_proc procedure
      JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = procedure.proowner
     WHERE procedure.oid = to_regprocedure(function_identity);

    IF current_owner NOT IN ('${legacy_owner}', 'everydayai_owner') THEN
        RAISE EXCEPTION 'ADMIN_ASSET_OWNER_TRANSFER_OWNER_UNEXPECTED: %',
            current_owner;
    END IF;
    IF current_owner = '${legacy_owner}' THEN
        ALTER FUNCTION public.list_admin_user_assets(
            UUID, TEXT, TEXT, INTEGER, TIMESTAMPTZ, UUID
        ) OWNER TO everydayai_owner;
    END IF;
END
\$transfer\$;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 管理员资产查询函数 owner 已归 everydayai_owner"
