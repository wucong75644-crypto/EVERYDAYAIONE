-- 149: 修复统一生成消息内容类型边界
-- messages.content 是 TEXT；JSONB payload 必须在 COALESCE 前显式转换。
-- 迁移 148 已发布，使用 CREATE OR REPLACE 保持原函数签名与调用方兼容。

CREATE OR REPLACE FUNCTION _prepare_generation_messages(
    p_operation TEXT, p_conversation_id UUID, p_org_id UUID, p_turn_id UUID,
    p_input_message JSONB,
    p_output_message JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_input messages%ROWTYPE;
    v_output messages%ROWTYPE;
    v_bound_task tasks%ROWTYPE;
    v_input_id UUID;
    v_output_id UUID;
    v_turn_id UUID;
BEGIN
    BEGIN
        v_output_id := (p_output_message->>'id')::UUID;
        IF NULLIF(p_input_message->>'id', '') IS NOT NULL THEN
            v_input_id := (p_input_message->>'id')::UUID;
        END IF;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END;
    IF v_output_id IS NULL
       OR (p_operation NOT IN ('retry', 'regenerate_single') AND v_input_id IS NULL) THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    IF p_operation IN ('retry', 'regenerate_single') THEN
        SELECT * INTO v_output FROM messages WHERE id = v_output_id FOR UPDATE;
        IF NOT FOUND OR v_output.conversation_id IS DISTINCT FROM p_conversation_id
           OR v_output.org_id IS DISTINCT FROM p_org_id
           OR v_output.role::TEXT <> 'assistant'
           OR (p_operation = 'retry' AND v_output.status::TEXT <> 'failed') THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_MESSAGE_CONFLICT' USING ERRCODE = '23505';
        END IF;
        IF EXISTS (
            SELECT 1 FROM tasks WHERE assistant_message_id = v_output_id
              AND status IN ('preparing', 'pending', 'running')
        ) THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_TASK_CONFLICT' USING ERRCODE = '23505';
        END IF;
        IF v_output.reply_to_message_id IS NOT NULL AND v_output.turn_id IS NOT NULL THEN
            v_input_id := v_output.reply_to_message_id;
            v_turn_id := v_output.turn_id;
        ELSE
            SELECT * INTO v_bound_task FROM tasks
             WHERE assistant_message_id = v_output_id
               AND input_message_id IS NOT NULL AND turn_id IS NOT NULL
             ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'GENERATION_PREPARE_ANCHOR_MISSING' USING ERRCODE = 'P0002';
            END IF;
            v_input_id := v_bound_task.input_message_id;
            v_turn_id := v_bound_task.turn_id;
        END IF;
        IF p_turn_id IS NOT NULL AND p_turn_id IS DISTINCT FROM v_turn_id THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_TURN_CONFLICT' USING ERRCODE = '23505';
        END IF;
    ELSE
        IF p_turn_id IS NULL THEN
            RAISE EXCEPTION 'GENERATION_PREPARE_ARGUMENT_INVALID' USING ERRCODE = '22023';
        END IF;
        v_turn_id := p_turn_id;
        INSERT INTO messages(
            id, conversation_id, org_id, role, content, status, credits_cost,
            client_request_id, created_at, turn_id
        ) VALUES (
            v_input_id, p_conversation_id, p_org_id, 'user',
            COALESCE(p_input_message->'content', '[]'::JSONB), 'completed', 0,
            NULLIF(p_input_message->>'client_request_id', ''),
            COALESCE(NULLIF(p_input_message->>'created_at', '')::TIMESTAMPTZ, NOW()),
            v_turn_id
        ) ON CONFLICT (id) DO NOTHING;
        INSERT INTO messages(
            id, conversation_id, org_id, role, content, status, credits_cost,
            generation_params, created_at, turn_id, reply_to_message_id
        ) VALUES (
            v_output_id, p_conversation_id, p_org_id, 'assistant',
            COALESCE(p_output_message->'content', '[]'::JSONB),
            COALESCE(NULLIF(p_output_message->>'status', ''), 'pending'), 0,
            p_output_message->'generation_params',
            COALESCE(NULLIF(p_output_message->>'created_at', '')::TIMESTAMPTZ, NOW()),
            v_turn_id, v_input_id
        ) ON CONFLICT (id) DO NOTHING;
    END IF;

    SELECT * INTO v_input FROM messages WHERE id = v_input_id FOR UPDATE;
    SELECT * INTO v_output FROM messages WHERE id = v_output_id FOR UPDATE;
    IF v_input.id IS NULL OR v_output.id IS NULL
       OR v_input.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_output.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_input.org_id IS DISTINCT FROM p_org_id OR v_output.org_id IS DISTINCT FROM p_org_id
       OR v_input.role::TEXT <> 'user' OR v_output.role::TEXT <> 'assistant' THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_MESSAGE_CONFLICT' USING ERRCODE = '23505';
    END IF;
    IF (v_input.turn_id IS NOT NULL AND v_input.turn_id IS DISTINCT FROM v_turn_id)
       OR (v_output.turn_id IS NOT NULL AND v_output.turn_id IS DISTINCT FROM v_turn_id)
       OR (v_output.reply_to_message_id IS NOT NULL
           AND v_output.reply_to_message_id IS DISTINCT FROM v_input_id) THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_TURN_CONFLICT' USING ERRCODE = '23505';
    END IF;
    UPDATE messages SET turn_id = v_turn_id WHERE id = v_input_id;
    UPDATE messages SET turn_id = v_turn_id, reply_to_message_id = v_input_id,
        content = CASE WHEN p_operation IN ('retry', 'regenerate_single')
            THEN COALESCE((p_output_message->'content')::TEXT, v_output.content) ELSE content END,
        status = CASE WHEN p_operation IN ('retry', 'regenerate_single')
            THEN COALESCE(NULLIF(p_output_message->>'status', ''), status::TEXT) ELSE status END,
        generation_params = CASE WHEN p_operation IN ('retry', 'regenerate_single')
            THEN COALESCE(p_output_message->'generation_params', v_output.generation_params)
            ELSE generation_params END,
        is_error = CASE WHEN p_operation IN ('retry', 'regenerate_single') THEN FALSE ELSE is_error END
     WHERE id = v_output_id;
    RETURN jsonb_build_object('turn_id', v_turn_id, 'input_message_id', v_input_id, 'output_message_id', v_output_id);
END;
$$;

REVOKE ALL ON FUNCTION _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
) FROM PUBLIC;

COMMENT ON FUNCTION _prepare_generation_messages(TEXT, UUID, UUID, UUID, JSONB, JSONB)
    IS 'prepare_generation 内部消息锚点准备函数；JSONB content 在更新前显式转换为 TEXT';
