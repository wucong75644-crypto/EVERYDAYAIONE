#!/bin/bash

set -euo pipefail

if [ "${ALLOW_RUNTIME_MESSAGE_OWNERSHIP_ROLLBACK:-false}" != "true" ]; then
    echo "❌ 必须设置 ALLOW_RUNTIME_MESSAGE_OWNERSHIP_ROLLBACK=true" >&2
    exit 1
fi
if [ "${RUNTIME_MESSAGE_SERVICES_RESTORED:-false}" != "true" ]; then
    echo "❌ 必须先切回旧数据库 URL，再设置 RUNTIME_MESSAGE_SERVICES_RESTORED=true" >&2
    exit 1
fi
if [ -z "${TENANT_DB_ADMIN_URL:-}" ]; then
    echo "❌ 缺少 TENANT_DB_ADMIN_URL" >&2
    exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
    echo "❌ 未找到 psql" >&2
    exit 1
fi

legacy_owner=${LEGACY_DATABASE_OWNER:-everydayai}
if [[ ! "$legacy_owner" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
    echo "❌ LEGACY_DATABASE_OWNER 不是合法 PostgreSQL 角色名" >&2
    exit 1
fi

{
    cat <<SQL
\\set ON_ERROR_STOP on
BEGIN;

DO \$rollback\$
DECLARE
    target_tables CONSTANT TEXT[] := ARRAY[
        'users', 'organizations', 'org_members', 'org_configs',
        'org_invitations',
        'wecom_user_mappings', 'wecom_chat_targets', 'conversations',
        'messages', 'tasks', 'credits_history', 'credit_transactions',
        'image_generations', 'detail_projects', 'detail_project_images',
        'refresh_tokens', 'user_subscriptions', 'user_memory_settings'
    ];
    target_functions CONSTANT TEXT[] := ARRAY[
        '_prepare_generation_messages(text,uuid,uuid,uuid,jsonb,jsonb)',
        '_prepare_generation_tasks(jsonb,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid)',
        'claim_message_generation_request(uuid,uuid,uuid,character varying,character,character varying,uuid)',
        'prepare_generation(uuid,text,uuid,uuid,uuid,uuid,jsonb,jsonb,jsonb)',
        'attach_generation_external_task(uuid,text,uuid,uuid,text,jsonb)',
        'fail_prepared_generation_task(uuid,text,text,uuid)',
        'enqueue_generation_turn(jsonb,uuid,uuid,text,jsonb,uuid)',
        'bind_generation_turn(uuid,uuid,uuid,uuid,text,uuid)',
        'close_generation_turn(uuid,uuid,uuid,uuid)',
        'cancel_generation_turn(uuid,uuid,uuid)',
        'deduct_credits_atomic(uuid,integer,text,text,uuid)',
        'atomic_refund_credits(uuid,text)',
        'partial_refund_credits(uuid,integer,text,uuid)',
        'increment_message_count(uuid,uuid)',
        'record_user_activity(uuid,text,uuid,text,text,text,timestamp with time zone,jsonb)',
        'resolve_wecom_conversation(uuid,text,text,text,uuid)',
        'stage_wecom_attachment_v2(uuid,uuid,text,uuid,text,jsonb,text,text,text,text,text,bigint,jsonb,uuid)',
        'enqueue_wecom_generation_turn_v2(jsonb,uuid,uuid,uuid,jsonb,jsonb,uuid)',
        'update_wecom_conversation_setting(uuid,uuid,text,text,uuid)',
        'wecom_get_or_create_user(text,text,uuid,text,text)',
        'claim_legacy_wecom_conversation(uuid,text,text,uuid)',
        'current_attachment_parts(uuid,uuid)',
        'bind_task_attachments(uuid,uuid,uuid,uuid,uuid)',
        'enqueue_wecom_task_record(jsonb,uuid,uuid,text,jsonb)',
        'renew_generation_lease(uuid,uuid,integer)',
        'update_generation_progress(uuid,uuid,text,jsonb)',
        'fail_generation_turn(uuid,uuid,text,text)',
        'commit_generation_turn_with_context_v2(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)',
        'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)',
        'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb)',
        'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb)',
        'close_generation_turn(uuid,uuid,uuid)'
    ];
    forced_tables TEXT;
    table_name TEXT;
    function_name TEXT;
    sequence_record RECORD;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '${legacy_owner}'
    ) THEN
        RAISE EXCEPTION 'LEGACY_DATABASE_OWNER_MISSING';
    END IF;
    SELECT string_agg(class.relname, ', ' ORDER BY class.relname)
      INTO forced_tables
      FROM pg_catalog.pg_class class
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = class.relnamespace
     WHERE namespace.nspname = 'public'
       AND class.relforcerowsecurity
       AND class.relname = ANY(target_tables);
    IF forced_tables IS NOT NULL THEN
        RAISE EXCEPTION 'DISABLE_FORCE_RLS_BEFORE_RUNTIME_MESSAGE_ROLLBACK: %',
            forced_tables;
    END IF;

    FOREACH table_name IN ARRAY target_tables LOOP
        EXECUTE format(
            'ALTER TABLE public.%I OWNER TO %I',
            table_name, '${legacy_owner}'
        );
    END LOOP;
    FOR sequence_record IN
        SELECT DISTINCT sequence.relname
          FROM pg_catalog.pg_class sequence
          JOIN pg_catalog.pg_depend dependency ON dependency.objid = sequence.oid
          JOIN pg_catalog.pg_class target ON target.oid = dependency.refobjid
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = target.relnamespace
         WHERE sequence.relkind = 'S'
           AND namespace.nspname = 'public'
           AND target.relname = ANY(target_tables)
           AND dependency.deptype IN ('a', 'i')
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE public.%I OWNER TO %I',
            sequence_record.relname, '${legacy_owner}'
        );
    END LOOP;
    FOREACH function_name IN ARRAY target_functions LOOP
        EXECUTE format(
            'ALTER FUNCTION public.%s OWNER TO %I',
            function_name, '${legacy_owner}'
        );
    END LOOP;
END
\$rollback\$;

REVOKE USAGE ON SCHEMA public FROM everydayai_wecom_runtime;

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Runtime/Message 第二批所有权已恢复"
