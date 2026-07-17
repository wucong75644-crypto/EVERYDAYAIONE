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
    v_result JSONB;
BEGIN
    IF p_task_data IS NULL
       OR jsonb_typeof(p_task_data) <> 'object'
       OR p_input_message_id IS NULL
       OR p_output_message_id IS NULL
       OR p_turn_id IS NULL
       OR p_input_content IS NULL
       OR jsonb_typeof(p_input_content) <> 'array'
       OR p_delivery_context IS NULL
       OR jsonb_typeof(p_delivery_context) <> 'object'
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
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'WECOM_ACTOR_ENQUEUE_ID_INVALID'
            USING ERRCODE = '22023';
    END;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = v_conversation_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_conversation.user_id IS DISTINCT FROM
          (p_task_data->>'user_id')::UUID
       OR v_conversation.org_id IS DISTINCT FROM v_org_id
       OR v_conversation.source IS DISTINCT FROM 'wecom' THEN
        RAISE EXCEPTION 'WECOM_ACTOR_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO messages(
        id, conversation_id, org_id, role, content, status, turn_id
    ) VALUES (
        p_input_message_id, v_conversation_id, v_org_id,
        'user', p_input_content, 'completed', p_turn_id
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO messages(
        id, conversation_id, org_id, role, content, status, turn_id,
        reply_to_message_id
    ) VALUES (
        p_output_message_id, v_conversation_id, v_org_id,
        'assistant', '[{"type":"text","text":""}]'::JSONB,
        'generating', p_turn_id, p_input_message_id
    )
    ON CONFLICT (id) DO NOTHING;

    SELECT * INTO v_input FROM messages
     WHERE id = p_input_message_id FOR UPDATE;
    SELECT * INTO v_output FROM messages
     WHERE id = p_output_message_id FOR UPDATE;
    IF v_input.id IS NULL
       OR v_output.id IS NULL
       OR v_input.conversation_id IS DISTINCT FROM v_conversation_id
       OR v_output.conversation_id IS DISTINCT FROM v_conversation_id
       OR v_input.org_id IS DISTINCT FROM v_org_id
       OR v_output.org_id IS DISTINCT FROM v_org_id
       OR v_input.role::TEXT <> 'user'
       OR v_output.role::TEXT <> 'assistant'
       OR v_input.turn_id IS DISTINCT FROM p_turn_id
       OR v_output.turn_id IS DISTINCT FROM p_turn_id
       OR v_output.reply_to_message_id IS DISTINCT FROM p_input_message_id
       OR v_input.content IS DISTINCT FROM p_input_content THEN
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
        'output_message_id', p_output_message_id
    );
END;
$$;

REVOKE ALL ON FUNCTION enqueue_wecom_generation_turn(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
) FROM PUBLIC;

COMMENT ON FUNCTION enqueue_wecom_generation_turn(
    JSONB, UUID, UUID, UUID, JSONB, JSONB
) IS '按稳定 ID 原子创建企微输入/输出消息并幂等进入 Conversation Actor 队列';

DROP FUNCTION IF EXISTS consume_active_conversation_attachments(UUID, UUID);
