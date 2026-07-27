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
        'everydayai_migrator',
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker',
        'everydayai_sync',
        'everydayai'
    ) THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_ACCESS_REQUIRES_ADMIN';
    END IF;
    IF to_regrole('everydayai_owner') IS NULL
       OR to_regrole('everydayai_sync') IS NULL
       OR to_regclass('public.wecom_employees') IS NULL THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_ACCESS_PREREQUISITE_MISSING';
    END IF;
    IF has_any_column_privilege(
        'everydayai_sync',
        'public.wecom_employees',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_DIRECT_ACCESS_INVALID';
    END IF;
END
$contract$;

GRANT SELECT (org_id, wecom_userid, name, status)
ON TABLE public.wecom_employees
TO everydayai_owner;

DO $verify$
DECLARE
    required_column TEXT;
BEGIN
    FOREACH required_column IN ARRAY ARRAY[
        'org_id', 'wecom_userid', 'name', 'status'
    ]
    LOOP
        IF NOT has_column_privilege(
            'everydayai_owner',
            'public.wecom_employees',
            required_column,
            'SELECT'
        ) THEN
            RAISE EXCEPTION
                'SYNC_WECOM_EMPLOYEE_OWNER_COLUMN_ACCESS_MISSING: %',
                required_column;
        END IF;
    END LOOP;
END
$verify$;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Sync 企微员工门面字段读取权限已授予"
