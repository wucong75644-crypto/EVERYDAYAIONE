#!/bin/bash

set -euo pipefail

if [ "${CONFIRM_ADMIN_CREDIT_OWNER_ROLLBACK:-}" != "1" ]; then
    echo "❌ 需要 CONFIRM_ADMIN_CREDIT_OWNER_ROLLBACK=1" >&2
    exit 1
fi
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
        RAISE EXCEPTION 'ADMIN_CREDIT_OWNERSHIP_ROLLBACK_REQUIRES_ADMIN';
    END IF;
    IF to_regrole('everydayai') IS NULL
       OR NOT EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc procedure
            WHERE procedure.oid =
                  'public.admin_adjust_credits(uuid,integer,text,uuid,uuid)'::REGPROCEDURE
              AND pg_get_userbyid(procedure.proowner) = 'everydayai_owner'
              AND procedure.proconfig @> ARRAY['search_path=public']
       ) THEN
        RAISE EXCEPTION 'ADMIN_CREDIT_OWNERSHIP_ROLLBACK_STATE_INVALID';
    END IF;
END
$contract$;

ALTER FUNCTION public.admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) OWNER TO everydayai;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 管理员积分调整函数已恢复给 everydayai"
