-- 132: 企微专用 Actor task 写入，允许已验证绑定的 channel conversation。

CREATE OR REPLACE FUNCTION enqueue_wecom_task_record(
    p_task_data JSONB,
    p_input_message_id UUID,
    p_turn_id UUID,
    p_execution_mode TEXT,
    p_delivery_context JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_task_id UUID := (p_task_data->>'id')::UUID;
    v_output_id UUID := (p_task_data->>'assistant_message_id')::UUID;
    v_user_id UUID := (p_task_data->>'user_id')::UUID;
    v_org_id UUID := NULLIF(p_task_data->>'org_id', '')::UUID;
    v_conversation_id UUID := (p_task_data->>'conversation_id')::UUID;
    v_inserted_count BIGINT;
BEGIN
    INSERT INTO tasks(
        id, external_task_id, client_task_id, user_id, org_id, conversation_id,
        type, status, model_id, placeholder_message_id, assistant_message_id,
        request_params, placeholder_created_at, input_message_id, turn_id,
        execution_mode, delivery_context
    ) VALUES (
        v_task_id, p_task_data->>'external_task_id',
        p_task_data->>'client_task_id', v_user_id, v_org_id, v_conversation_id,
        'chat', 'pending', p_task_data->>'model_id',
        NULLIF(p_task_data->>'placeholder_message_id', '')::UUID,
        v_output_id, COALESCE(p_task_data->'request_params', '{}'::JSONB),
        NULLIF(p_task_data->>'placeholder_created_at', '')::TIMESTAMPTZ,
        p_input_message_id, p_turn_id, p_execution_mode, p_delivery_context
    )
    ON CONFLICT (id) DO NOTHING;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;

    SELECT * INTO v_task FROM tasks WHERE id = v_task_id FOR UPDATE;
    IF v_task.id IS NULL OR v_task.type <> 'chat'
       OR v_task.conversation_id IS DISTINCT FROM v_conversation_id
       OR v_task.user_id IS DISTINCT FROM v_user_id
       OR v_task.org_id IS DISTINCT FROM v_org_id
       OR v_task.input_message_id IS DISTINCT FROM p_input_message_id
       OR v_task.turn_id IS DISTINCT FROM p_turn_id
       OR v_task.assistant_message_id IS DISTINCT FROM v_output_id
       OR v_task.execution_mode IS DISTINCT FROM p_execution_mode THEN
        RAISE EXCEPTION 'ACTOR_ENQUEUE_CONFLICT' USING ERRCODE = '23505';
    END IF;
    UPDATE messages SET turn_id = p_turn_id
     WHERE id = p_input_message_id;
    UPDATE messages SET turn_id = p_turn_id,
           reply_to_message_id = p_input_message_id
     WHERE id = v_output_id;
    RETURN jsonb_build_object(
        'task_id', v_task.id,
        'status', v_task.status,
        'queue_sequence', v_task.queue_sequence,
        'execution_mode', v_task.execution_mode,
        'already_enqueued', v_inserted_count = 0
    );
END;
$$;

REVOKE ALL ON FUNCTION enqueue_wecom_task_record(
    JSONB, UUID, UUID, TEXT, JSONB
) FROM PUBLIC;
