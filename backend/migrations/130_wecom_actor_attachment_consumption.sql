-- 130: 企微 Actor 入队原子消费当前会话活动附件，并支持群共享 conversation。

CREATE OR REPLACE FUNCTION consume_active_conversation_attachments(
    p_conversation_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_parts JSONB;
BEGIN
    PERFORM 1
      FROM conversation_attachment_refs
     WHERE conversation_id = p_conversation_id
       AND org_id = p_org_id
       AND status = 'ready'
       AND reference_state = 'active'
     FOR UPDATE;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'type', 'file',
        'url', a.url,
        'workspace_path', a.workspace_path,
        'name', a.original_name,
        'mime_type', a.mime_type,
        'size', a.size,
        'asset_id', a.id
    ) ORDER BY a.created_at), '[]'::JSONB)
      INTO v_parts
      FROM conversation_attachment_refs a
     WHERE a.conversation_id = p_conversation_id
       AND a.org_id = p_org_id
       AND a.status = 'ready'
       AND a.reference_state = 'active';

    UPDATE conversation_attachment_refs
       SET reference_state = 'referenced'
     WHERE conversation_id = p_conversation_id
       AND org_id = p_org_id
       AND status = 'ready'
       AND reference_state = 'active';
    RETURN v_parts;
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_wecom_generation_turn(
    p_task_data JSONB,
    p_input_message_id UUID,
    p_output_message_id UUID,
    p_turn_id UUID,
    p_input_content JSONB,
    p_delivery_context JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_input messages%ROWTYPE;
    v_output messages%ROWTYPE;
    v_conversation_id UUID;
    v_org_id UUID;
    v_user_id UUID;
    v_content JSONB;
    v_result JSONB;
BEGIN
    IF jsonb_typeof(p_task_data) IS DISTINCT FROM 'object'
       OR p_input_message_id IS NULL OR p_output_message_id IS NULL
       OR p_turn_id IS NULL
       OR jsonb_typeof(p_input_content) IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_delivery_context) IS DISTINCT FROM 'object'
       OR NOT (
          p_delivery_context
          @> '{"actor": true, "channel": "wecom"}'::JSONB
       )
       OR COALESCE(p_delivery_context->>'chatid', '') = ''
       OR COALESCE(p_delivery_context->>'transport', '')
          NOT IN ('smart_robot', 'app') THEN
        RAISE EXCEPTION 'WECOM_ACTOR_ENQUEUE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    BEGIN
        v_conversation_id := (p_task_data->>'conversation_id')::UUID;
        v_org_id := NULLIF(p_task_data->>'org_id', '')::UUID;
        v_user_id := (p_task_data->>'user_id')::UUID;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'WECOM_ACTOR_ENQUEUE_ID_INVALID'
            USING ERRCODE = '22023';
    END;

    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    IF NOT FOUND
       OR v_conversation.org_id IS DISTINCT FROM v_org_id
       OR v_conversation.source IS DISTINCT FROM 'wecom'
       OR (
          v_conversation.scope_type = 'user'
          AND v_conversation.user_id IS DISTINCT FROM v_user_id
       )
       OR (
          v_conversation.scope_type = 'channel'
          AND NOT EXISTS (
              SELECT 1 FROM conversation_channel_bindings b
               WHERE b.conversation_id = v_conversation_id
                 AND b.org_id = v_org_id
                 AND b.corp_id = p_delivery_context->>'corp_id'
                 AND b.external_chat_id = p_delivery_context->>'chatid'
                 AND b.chat_type = 'group'
          )
       ) THEN
        RAISE EXCEPTION 'WECOM_ACTOR_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_input FROM messages
     WHERE id = p_input_message_id FOR UPDATE;
    IF v_input.id IS NULL THEN
        v_content := p_input_content
            || consume_active_conversation_attachments(
                v_conversation_id, v_org_id
            );
        INSERT INTO messages(
            id, conversation_id, org_id, role, content, status, turn_id,
            sender_user_id, sender_channel_identity
        ) VALUES (
            p_input_message_id, v_conversation_id, v_org_id, 'user',
            v_content, 'completed', p_turn_id, v_user_id,
            NULLIF(p_delivery_context->>'wecom_userid', '')
        )
        RETURNING * INTO v_input;
    ELSE
        v_content := v_input.content;
    END IF;
    INSERT INTO messages(
        id, conversation_id, org_id, role, content, status, turn_id,
        reply_to_message_id
    ) VALUES (
        p_output_message_id, v_conversation_id, v_org_id, 'assistant',
        '[{"type":"text","text":""}]', 'generating', p_turn_id,
        p_input_message_id
    ) ON CONFLICT (id) DO NOTHING;

    SELECT * INTO v_output FROM messages
     WHERE id = p_output_message_id FOR UPDATE;
    IF v_input.id IS NULL OR v_output.id IS NULL
       OR v_input.conversation_id IS DISTINCT FROM v_conversation_id
       OR v_output.conversation_id IS DISTINCT FROM v_conversation_id
       OR v_input.org_id IS DISTINCT FROM v_org_id
       OR v_output.org_id IS DISTINCT FROM v_org_id
       OR v_input.role::TEXT <> 'user'
       OR v_output.role::TEXT <> 'assistant'
       OR v_input.turn_id IS DISTINCT FROM p_turn_id
       OR v_output.turn_id IS DISTINCT FROM p_turn_id
       OR v_output.reply_to_message_id IS DISTINCT FROM p_input_message_id
       OR jsonb_typeof(v_content) IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_content)
          < jsonb_array_length(p_input_content)
       OR (
          SELECT COALESCE(jsonb_agg(value ORDER BY ordinality), '[]'::JSONB)
            FROM jsonb_array_elements(v_content)
                 WITH ORDINALITY AS existing(value, ordinality)
           WHERE ordinality <= jsonb_array_length(p_input_content)
       ) IS DISTINCT FROM p_input_content THEN
        RAISE EXCEPTION 'WECOM_ACTOR_MESSAGE_CONFLICT'
            USING ERRCODE = '23505';
    END IF;

    SELECT enqueue_generation_turn(
        p_task_data, p_input_message_id, p_turn_id, 'serial',
        p_delivery_context
    ) INTO v_result;
    IF COALESCE((v_result->>'already_enqueued')::BOOLEAN, FALSE) = FALSE THEN
        PERFORM increment_message_count(v_conversation_id, v_org_id);
    END IF;
    RETURN v_result || jsonb_build_object(
        'input_message_id', p_input_message_id,
        'output_message_id', p_output_message_id,
        'attachment_count', jsonb_array_length(v_content)
            - jsonb_array_length(p_input_content)
    );
END;
$$;

REVOKE ALL ON FUNCTION consume_active_conversation_attachments(
    UUID, UUID
) FROM PUBLIC;
