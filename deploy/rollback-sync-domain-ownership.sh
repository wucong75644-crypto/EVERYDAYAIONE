#!/bin/bash

set -euo pipefail

if [ "${ALLOW_SYNC_DOMAIN_OWNERSHIP_ROLLBACK:-}" != "true" ]; then
    echo "❌ 必须显式设置 ALLOW_SYNC_DOMAIN_OWNERSHIP_ROLLBACK=true" >&2
    exit 1
fi
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

DO \$rollback\$
DECLARE
    relation_record RECORD;
    sequence_record RECORD;
    function_record RECORD;
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_stat_activity
         WHERE usename = 'everydayai_sync'
           AND pid <> pg_backend_pid()
    ) THEN
        RAISE EXCEPTION 'SYNC_DOMAIN_ROLLBACK_ACTIVE_SYNC_SESSIONS';
    END IF;

    FOR relation_record IN
        SELECT relation.relname, relation.relkind
          FROM pg_catalog.pg_class relation
         WHERE relation.relnamespace = 'public'::regnamespace
           AND relation.relowner = 'everydayai_owner'::regrole
           AND (
               relation.relname LIKE 'erp\_%' ESCAPE '\'
               OR relation.relname LIKE 'kuaimai\_%' ESCAPE '\'
               OR relation.relname IN ('deleted_files', 'mv_kit_stock')
           )
           AND relation.relkind IN ('r', 'p', 'm')
    LOOP
        IF relation_record.relkind = 'm' THEN
            EXECUTE format(
                'ALTER MATERIALIZED VIEW public.%I OWNER TO %I',
                relation_record.relname, '${legacy_owner}'
            );
        ELSE
            EXECUTE format(
                'ALTER TABLE public.%I OWNER TO %I',
                relation_record.relname, '${legacy_owner}'
            );
        END IF;
    END LOOP;

    FOR sequence_record IN
        SELECT sequence.relname
          FROM pg_catalog.pg_class sequence
         WHERE sequence.relnamespace = 'public'::regnamespace
           AND sequence.relowner = 'everydayai_owner'::regrole
           AND (
               sequence.relname LIKE 'erp\_%' ESCAPE '\'
               OR sequence.relname LIKE 'kuaimai\_%' ESCAPE '\'
               OR sequence.relname = 'deleted_files_id_seq'
           )
           AND sequence.relkind = 'S'
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE public.%I OWNER TO %I',
            sequence_record.relname, '${legacy_owner}'
        );
    END LOOP;

    FOR function_record IN
        SELECT procedure.oid::regprocedure AS signature
          FROM pg_catalog.pg_proc procedure
         WHERE procedure.pronamespace = 'public'::regnamespace
           AND procedure.proowner = 'everydayai_owner'::regrole
           AND (
               procedure.proname LIKE 'erp\_%' ESCAPE '\'
               OR procedure.proname LIKE 'kuaimai\_%' ESCAPE '\'
           )
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s OWNER TO %I',
            function_record.signature, '${legacy_owner}'
        );
    END LOOP;
END
\$rollback\$;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Sync 数据域对象所有权已回滚到旧角色"
