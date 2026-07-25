#!/bin/bash

set -euo pipefail

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

DO \$transfer\$
DECLARE
    target_tables CONSTANT TEXT[] := ARRAY[
        'users', 'organizations', 'org_members', 'org_configs',
        'org_invitations',
        'wecom_user_mappings', 'wecom_chat_targets', 'conversations',
        'messages', 'tasks', 'credits_history', 'credit_transactions',
        'image_generations', 'detail_projects', 'detail_project_images',
        'refresh_tokens', 'user_subscriptions', 'user_memory_settings',
        'conversation_deliveries'
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
        'enqueue_wecom_generation_turn_v2(jsonb,uuid,uuid,uuid,jsonb,jsonb)',
        'enqueue_wecom_generation_turn_v2(jsonb,uuid,uuid,uuid,jsonb,jsonb,uuid)',
        'update_wecom_conversation_setting(uuid,uuid,text,text,uuid)',
        'get_wecom_generation_context(uuid,uuid)',
        'update_wecom_ingress_display_name(uuid,text,text,uuid,text)',
        'reset_wecom_conversation(uuid,uuid,uuid)',
        'get_wecom_manual_memories(uuid,uuid)',
        'clear_wecom_manual_memories(uuid,uuid)',
        'wecom_get_or_create_user(text,text,uuid,text,text)',
        'claim_legacy_wecom_conversation(uuid,text,text,uuid)',
        'current_attachment_parts(uuid,uuid)',
        'bind_task_attachments(uuid,uuid,uuid,uuid,uuid)',
        'enqueue_wecom_task_record(jsonb,uuid,uuid,text,jsonb)',
        'renew_generation_lease(uuid,uuid,integer)',
        'update_generation_progress(uuid,uuid,text,jsonb)',
        'fail_generation_turn(uuid,uuid,text,text)',
        'claim_conversation_delivery(integer,integer)',
        'renew_conversation_delivery(uuid,uuid,integer,jsonb)',
        'complete_conversation_delivery(uuid,uuid,jsonb)',
        'fail_conversation_delivery(uuid,uuid,text,jsonb,integer)',
        'commit_generation_turn_with_context_v2(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)',
        'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)',
        'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb,jsonb)',
        'commit_generation_turn(uuid,uuid,uuid,jsonb,jsonb,integer,jsonb)',
        'close_generation_turn(uuid,uuid,uuid)'
    ];
    missing_roles TEXT;
    missing_tables TEXT;
    missing_functions TEXT;
    unexpected_owners TEXT;
    unexpected_function_owners TEXT;
    unexpected_sequence_owners TEXT;
    table_name TEXT;
    function_name TEXT;
    sequence_record RECORD;
BEGIN
    SELECT string_agg(required_role, ', ' ORDER BY required_role)
      INTO missing_roles
      FROM unnest(ARRAY[
          'everydayai_owner', 'everydayai_runtime',
          'everydayai_wecom_runtime', 'everydayai_worker', '${legacy_owner}'
      ]) AS required_role
     WHERE NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = required_role
     );
    IF missing_roles IS NOT NULL THEN
        RAISE EXCEPTION 'TENANT_ROLE_MISSING: %', missing_roles;
    END IF;

    SELECT string_agg(required_table, ', ' ORDER BY required_table)
      INTO missing_tables
      FROM unnest(target_tables) AS required_table
     WHERE to_regclass('public.' || required_table) IS NULL;
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'RUNTIME_MESSAGE_TABLE_MISSING: %', missing_tables;
    END IF;

    SELECT string_agg(required_function, ', ' ORDER BY required_function)
      INTO missing_functions
      FROM unnest(target_functions) AS required_function
     WHERE to_regprocedure('public.' || required_function) IS NULL;
    IF missing_functions IS NOT NULL THEN
        RAISE EXCEPTION 'RUNTIME_MESSAGE_FUNCTION_MISSING: %', missing_functions;
    END IF;

    SELECT string_agg(c.relname || '=' || owner_role.rolname, ', ' ORDER BY c.relname)
      INTO unexpected_owners
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
     WHERE n.nspname = 'public'
       AND c.relname = ANY(target_tables)
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_owners IS NOT NULL THEN
        RAISE EXCEPTION 'RUNTIME_MESSAGE_TABLE_OWNER_UNEXPECTED: %',
            unexpected_owners;
    END IF;

    SELECT string_agg(
               procedure.oid::regprocedure::TEXT || '=' || owner_role.rolname,
               ', ' ORDER BY procedure.oid::regprocedure::TEXT
           )
      INTO unexpected_function_owners
      FROM pg_catalog.pg_proc procedure
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = procedure.proowner
     WHERE procedure.oid = ANY(
         SELECT to_regprocedure('public.' || name)
           FROM unnest(target_functions) AS name
     )
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_function_owners IS NOT NULL THEN
        RAISE EXCEPTION 'RUNTIME_MESSAGE_FUNCTION_OWNER_UNEXPECTED: %',
            unexpected_function_owners;
    END IF;

    SELECT string_agg(
               sequence.relname || '=' || owner_role.rolname,
               ', ' ORDER BY sequence.relname
           )
      INTO unexpected_sequence_owners
      FROM pg_catalog.pg_class sequence
      JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = sequence.relowner
      JOIN pg_catalog.pg_depend dependency ON dependency.objid = sequence.oid
      JOIN pg_catalog.pg_class target ON target.oid = dependency.refobjid
      JOIN pg_catalog.pg_namespace namespace ON namespace.oid = target.relnamespace
     WHERE sequence.relkind = 'S'
       AND namespace.nspname = 'public'
       AND target.relname = ANY(target_tables)
       AND dependency.deptype IN ('a', 'i')
       AND owner_role.rolname NOT IN ('${legacy_owner}', 'everydayai_owner');
    IF unexpected_sequence_owners IS NOT NULL THEN
        RAISE EXCEPTION 'RUNTIME_MESSAGE_SEQUENCE_OWNER_UNEXPECTED: %',
            unexpected_sequence_owners;
    END IF;

    FOREACH table_name IN ARRAY target_tables LOOP
        EXECUTE format(
            'ALTER TABLE public.%I OWNER TO everydayai_owner',
            table_name
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
            'ALTER SEQUENCE public.%I OWNER TO everydayai_owner',
            sequence_record.relname
        );
        EXECUTE format(
            'REVOKE ALL ON SEQUENCE public.%I FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker',
            sequence_record.relname
        );
        EXECUTE format(
            'GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.%I TO %I',
            sequence_record.relname, '${legacy_owner}'
        );
    END LOOP;
    FOREACH function_name IN ARRAY target_functions LOOP
        EXECUTE format(
            'ALTER FUNCTION public.%s OWNER TO everydayai_owner',
            function_name
        );
        EXECUTE format(
            'REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker',
            function_name
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.%s TO %I',
            function_name, '${legacy_owner}'
        );
    END LOOP;
END
\$transfer\$;

GRANT USAGE ON SCHEMA public TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION
    public.resolve_wecom_conversation(uuid,text,text,text,uuid),
    public.stage_wecom_attachment_v2(
        uuid,uuid,text,uuid,text,jsonb,text,text,text,text,text,bigint,jsonb,uuid
    ),
    public.enqueue_wecom_generation_turn_v2(
        jsonb,uuid,uuid,uuid,jsonb,jsonb
    ),
    public.update_wecom_conversation_setting(uuid,uuid,text,text,uuid),
    public.record_user_activity(
        uuid,text,uuid,text,text,text,timestamp with time zone,jsonb
    )
TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION public.record_user_activity(
    uuid,text,uuid,text,text,text,timestamp with time zone,jsonb
) TO everydayai_runtime, everydayai_worker;
REVOKE ALL ON TABLE
    public.users, public.organizations, public.org_members, public.org_configs,
    public.org_invitations,
    public.wecom_user_mappings, public.wecom_chat_targets,
    public.conversations, public.messages, public.tasks,
    public.credits_history, public.credit_transactions,
    public.image_generations, public.detail_projects,
    public.detail_project_images, public.refresh_tokens,
    public.user_subscriptions, public.user_memory_settings
FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.users, public.organizations, public.org_members, public.org_configs,
    public.org_invitations,
    public.wecom_user_mappings, public.wecom_chat_targets,
    public.conversations, public.messages, public.tasks,
    public.credits_history, public.credit_transactions,
    public.image_generations, public.detail_projects,
    public.detail_project_images, public.refresh_tokens,
    public.user_subscriptions, public.user_memory_settings
TO ${legacy_owner};

COMMIT;
SQL
} | python3 "$(dirname "$0")/run-psql-admin.py" \
    --no-psqlrc --set=ON_ERROR_STOP=1

echo "✅ Runtime/Message 第二批 18 张表、列序列和业务函数已转移"
