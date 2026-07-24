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

DO $contract$
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
        RAISE EXCEPTION 'CONFIG_EXPORT_ACCESS_REQUIRES_ADMIN';
    END IF;
    IF to_regrole('everydayai_owner') IS NULL
       OR to_regrole('everydayai_config_import_reader') IS NULL
       OR to_regclass(
           'public.kuaimai_external_credentials'
       ) IS NULL THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_ACCESS_PREREQUISITE_MISSING';
    END IF;
    IF has_table_privilege(
        'everydayai_config_import_reader',
        'public.kuaimai_external_credentials',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_READER_TABLE_ACCESS_INVALID';
    END IF;
END
$contract$;

GRANT SELECT ON TABLE public.kuaimai_external_credentials
TO everydayai_owner;

DO $verify$
BEGIN
    IF NOT has_table_privilege(
        'everydayai_owner',
        'public.kuaimai_external_credentials',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_OWNER_SOURCE_ACCESS_INVALID';
    END IF;
END
$verify$;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 旧配置导出 owner 单表只读权限已授予"
