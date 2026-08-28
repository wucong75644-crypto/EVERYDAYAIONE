-- 154: 将既有 WeCom SECURITY INVOKER RPC 收口为无直表权限的安全门面。
-- 前置：152、153 已应用；154 完成前不得切换 WeCom 数据库角色。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_wecom_message_scope(
    p_org_id UUID,
    p_actor_user_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_wecom_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR p_org_id IS NULL
       OR p_actor_user_id IS NULL
       OR tenant_org_id() IS DISTINCT FROM p_org_id
       OR tenant_actor_user_id() IS DISTINCT FROM p_actor_user_id
       OR NOT tenant_actor_is_active_member(p_org_id) THEN
        RAISE EXCEPTION 'WECOM_MESSAGE_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

ALTER FUNCTION resolve_wecom_conversation(
    UUID, TEXT, TEXT, TEXT, UUID
) RENAME TO _resolve_wecom_conversation_core;
ALTER FUNCTION stage_wecom_attachment_v2(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, JSONB, UUID
) RENAME TO _stage_wecom_attachment_v2_core;
ALTER FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
) RENAME TO _enqueue_wecom_generation_turn_v2_core;
ALTER FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
) RENAME TO _enqueue_wecom_generation_turn_v2_core;
ALTER FUNCTION update_wecom_conversation_setting(
    UUID, UUID, TEXT, TEXT, UUID
) RENAME TO _update_wecom_conversation_setting_core;
ALTER FUNCTION record_user_activity(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
) RENAME TO _record_user_activity_core;

ALTER FUNCTION _resolve_wecom_conversation_core(
    UUID, TEXT, TEXT, TEXT, UUID
) SET search_path = pg_catalog, public;
ALTER FUNCTION _stage_wecom_attachment_v2_core(
    UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, JSONB, UUID
) SET search_path = pg_catalog, public;
ALTER FUNCTION _enqueue_wecom_generation_turn_v2_core(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
) SET search_path = pg_catalog, public;
ALTER FUNCTION _enqueue_wecom_generation_turn_v2_core(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
) SET search_path = pg_catalog, public;
ALTER FUNCTION _update_wecom_conversation_setting_core(
    UUID, UUID, TEXT, TEXT, UUID
) SET search_path = pg_catalog, public;
ALTER FUNCTION _record_user_activity_core(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
) SET search_path = pg_catalog, public;

CREATE FUNCTION resolve_wecom_conversation(
    p_user_id UUID, p_corp_id TEXT, p_external_chat_id TEXT,
    p_chat_type TEXT, p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id);
        PERFORM public._assert_wecom_ingress_scope(p_org_id, p_corp_id);
    END IF;
    RETURN public._resolve_wecom_conversation_core(
        p_user_id, p_corp_id, p_external_chat_id, p_chat_type, p_org_id
    );
END;
$$;

CREATE FUNCTION stage_wecom_attachment_v2(
    p_conversation_id UUID, p_source_message_id UUID,
    p_source_provider_id TEXT, p_sender_user_id UUID,
    p_sender_channel_identity TEXT, p_content JSONB,
    p_original_name TEXT, p_url TEXT, p_workspace_path TEXT,
    p_storage_scope TEXT, p_mime_type TEXT, p_size BIGINT,
    p_asset_identity JSONB, p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_message_scope(p_org_id, p_sender_user_id);
    END IF;
    RETURN public._stage_wecom_attachment_v2_core(
        p_conversation_id, p_source_message_id, p_source_provider_id,
        p_sender_user_id, p_sender_channel_identity, p_content,
        p_original_name, p_url, p_workspace_path, p_storage_scope,
        p_mime_type, p_size, p_asset_identity, p_org_id
    );
END;
$$;

CREATE FUNCTION enqueue_wecom_generation_turn_v2(
    p_task_data JSONB, p_input_message_id UUID, p_output_message_id UUID,
    p_turn_id UUID, p_input_content JSONB, p_delivery_context JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org_id UUID;
    v_user_id UUID;
BEGIN
    IF session_user <> 'everydayai' THEN
        BEGIN
            v_org_id := NULLIF(p_task_data->>'org_id', '')::UUID;
            v_user_id := NULLIF(p_task_data->>'user_id', '')::UUID;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'WECOM_MESSAGE_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END;
        PERFORM public._assert_wecom_message_scope(v_org_id, v_user_id);
        PERFORM public._assert_wecom_ingress_scope(
            v_org_id, p_delivery_context->>'corp_id'
        );
    END IF;
    RETURN public._enqueue_wecom_generation_turn_v2_core(
        p_task_data, p_input_message_id, p_output_message_id, p_turn_id,
        p_input_content, p_delivery_context
    );
END;
$$;

CREATE FUNCTION enqueue_wecom_generation_turn_v2(
    p_task_data JSONB, p_input_message_id UUID, p_output_message_id UUID,
    p_turn_id UUID, p_input_content JSONB, p_delivery_context JSONB,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user_id UUID;
BEGIN
    IF session_user <> 'everydayai' THEN
        BEGIN
            v_user_id := NULLIF(p_task_data->>'user_id', '')::UUID;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'WECOM_MESSAGE_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END;
        PERFORM public._assert_wecom_message_scope(p_org_id, v_user_id);
        PERFORM public._assert_wecom_ingress_scope(
            p_org_id, p_delivery_context->>'corp_id'
        );
    END IF;
    RETURN public._enqueue_wecom_generation_turn_v2_core(
        p_task_data, p_input_message_id, p_output_message_id, p_turn_id,
        p_input_content, p_delivery_context, p_org_id
    );
END;
$$;

CREATE FUNCTION update_wecom_conversation_setting(
    p_conversation_id UUID, p_user_id UUID, p_setting_key TEXT,
    p_setting_value TEXT, p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id);
    END IF;
    RETURN public._update_wecom_conversation_setting_core(
        p_conversation_id, p_user_id, p_setting_key, p_setting_value, p_org_id
    );
END;
$$;

CREATE FUNCTION record_user_activity(
    p_user_id UUID, p_event_type TEXT, p_org_id UUID DEFAULT NULL,
    p_source TEXT DEFAULT 'web', p_resource_type TEXT DEFAULT NULL,
    p_resource_id TEXT DEFAULT NULL, p_occurred_at TIMESTAMPTZ DEFAULT NOW(),
    p_metadata JSONB DEFAULT '{}'::JSONB
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai' THEN
        IF NOT tenant_database_role_matches_scope()
           OR tenant_actor_user_id() IS DISTINCT FROM p_user_id
           OR tenant_org_id() IS DISTINCT FROM p_org_id
           OR p_event_type NOT IN (
               'login_success', 'conversation_created', 'message_sent',
               'task_created', 'wecom_message_received', 'file_uploaded'
           )
           OR p_source NOT IN ('web', 'wecom', 'system')
           OR (
               session_user = 'everydayai_wecom_runtime'
               AND p_source <> 'wecom'
           ) THEN
            RAISE EXCEPTION 'USER_ACTIVITY_ROLE_SCOPE_MISMATCH'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    PERFORM public._record_user_activity_core(
        p_user_id, p_event_type, p_org_id, p_source, p_resource_type,
        p_resource_id, p_occurred_at, p_metadata
    );
END;
$$;

REVOKE ALL ON FUNCTION _assert_wecom_message_scope(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION
    _resolve_wecom_conversation_core(UUID, TEXT, TEXT, TEXT, UUID),
    _stage_wecom_attachment_v2_core(
        UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
        BIGINT, JSONB, UUID
    ),
    _enqueue_wecom_generation_turn_v2_core(
        JSONB, UUID, UUID, UUID, JSONB, JSONB
    ),
    _enqueue_wecom_generation_turn_v2_core(
        JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
    ),
    _update_wecom_conversation_setting_core(UUID, UUID, TEXT, TEXT, UUID),
    _record_user_activity_core(
        UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
    )
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION
    resolve_wecom_conversation(UUID, TEXT, TEXT, TEXT, UUID),
    stage_wecom_attachment_v2(
        UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
        BIGINT, JSONB, UUID
    ),
    enqueue_wecom_generation_turn_v2(
        JSONB, UUID, UUID, UUID, JSONB, JSONB
    ),
    enqueue_wecom_generation_turn_v2(
        JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
    ),
    update_wecom_conversation_setting(UUID, UUID, TEXT, TEXT, UUID),
    record_user_activity(
        UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
    )
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION
    resolve_wecom_conversation(UUID, TEXT, TEXT, TEXT, UUID),
    stage_wecom_attachment_v2(
        UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT,
        BIGINT, JSONB, UUID
    ),
    enqueue_wecom_generation_turn_v2(
        JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
    ),
    update_wecom_conversation_setting(UUID, UUID, TEXT, TEXT, UUID),
    record_user_activity(
        UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
    )
TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION record_user_activity(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
) TO everydayai_runtime, everydayai_worker;

DO $legacy_compatibility$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai') THEN
        GRANT EXECUTE ON FUNCTION
            resolve_wecom_conversation(UUID, TEXT, TEXT, TEXT, UUID),
            stage_wecom_attachment_v2(
                UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT, TEXT, TEXT, TEXT,
                TEXT, BIGINT, JSONB, UUID
            ),
            enqueue_wecom_generation_turn_v2(
                JSONB, UUID, UUID, UUID, JSONB, JSONB
            ),
            enqueue_wecom_generation_turn_v2(
                JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
            ),
            update_wecom_conversation_setting(UUID, UUID, TEXT, TEXT, UUID),
            record_user_activity(
                UUID, TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, JSONB
            )
        TO everydayai;
    END IF;
END
$legacy_compatibility$;

REVOKE ALL ON TABLE user_activity_events
FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

RESET ROLE;
