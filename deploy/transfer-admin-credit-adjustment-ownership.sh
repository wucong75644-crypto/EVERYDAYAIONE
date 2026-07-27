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
DECLARE
    function_owner TEXT;
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
        RAISE EXCEPTION 'ADMIN_CREDIT_OWNERSHIP_REQUIRES_ADMIN';
    END IF;
    IF to_regrole('everydayai_owner') IS NULL
       OR to_regprocedure(
           'public.admin_adjust_credits(uuid,integer,text,uuid,uuid)'
       ) IS NULL THEN
        RAISE EXCEPTION 'ADMIN_CREDIT_OWNERSHIP_PREREQUISITE_MISSING';
    END IF;

    SELECT pg_get_userbyid(procedure.proowner)
      INTO function_owner
      FROM pg_catalog.pg_proc procedure
     WHERE procedure.oid =
           'public.admin_adjust_credits(uuid,integer,text,uuid,uuid)'::REGPROCEDURE;

    IF function_owner NOT IN ('everydayai', 'everydayai_owner') THEN
        RAISE EXCEPTION
            'ADMIN_CREDIT_OWNERSHIP_UNEXPECTED: %', function_owner;
    END IF;
END
$contract$;

ALTER FUNCTION public.admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) OWNER TO everydayai_owner;

REVOKE ALL ON FUNCTION public.admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
DO $$
BEGIN
    IF to_regrole('service_role') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.admin_adjust_credits(
            UUID, INTEGER, TEXT, UUID, UUID
        ) FROM service_role;
    END IF;
END;
$$;

DO $verify$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc procedure
         WHERE procedure.oid =
               'public.admin_adjust_credits(uuid,integer,text,uuid,uuid)'::REGPROCEDURE
           AND pg_get_userbyid(procedure.proowner) = 'everydayai_owner'
    ) THEN
        RAISE EXCEPTION 'ADMIN_CREDIT_OWNERSHIP_TRANSFER_FAILED';
    END IF;
    IF has_function_privilege(
        'everydayai_runtime',
        'public.admin_adjust_credits(uuid,integer,text,uuid,uuid)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'ADMIN_CREDIT_OWNERSHIP_EXECUTE_NOT_CLOSED';
    END IF;
END
$verify$;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ 管理员积分调整函数已转移给 everydayai_owner"
