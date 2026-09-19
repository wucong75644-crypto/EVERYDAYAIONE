-- Preserve Skill feedback and partial output at the existing atomic failure boundary.
-- No history backfill, permission changes or new tables.
CREATE OR REPLACE FUNCTION fail_generation_turn(
    p_task_id UUID,
    p_execution_token UUID,
    p_error_code TEXT,
    p_error_message TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_conversation_id UUID;
    v_snapshot JSONB := '[]'::JSONB;
    v_blocks_text TEXT;
    v_remaining_text TEXT;
BEGIN
    IF p_execution_token IS NULL
       OR NULLIF(BTRIM(p_error_code), '') IS NULL
       OR NULLIF(BTRIM(p_error_message), '') IS NULL THEN
        RAISE EXCEPTION 'ACTOR_FAIL_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT conversation_id INTO v_conversation_id FROM tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;

    IF v_task.id IS NULL OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_task.org_id IS DISTINCT FROM v_conversation.org_id THEN
        RAISE EXCEPTION 'ACTOR_FAIL_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'failed'
       AND v_task.execution_token IS NOT DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'already_failed', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    -- Materialize fenced progress atomically with the failure, as with pause/cancel.
    -- The prefix rule matches services.task_utils.merge_blocks_with_text.
    IF jsonb_typeof(v_task.accumulated_blocks) = 'array'
       AND jsonb_array_length(v_task.accumulated_blocks) > 0 THEN
        v_snapshot := v_task.accumulated_blocks;
    END IF;
    SELECT COALESCE(string_agg(item.value->>'text', '' ORDER BY item.ordinality), '')
      INTO v_blocks_text
      FROM jsonb_array_elements(v_snapshot) WITH ORDINALITY AS item(value, ordinality)
     WHERE item.value->>'type' = 'text';
    IF v_task.accumulated_content IS NOT NULL
       AND LEFT(v_task.accumulated_content, LENGTH(v_blocks_text)) = v_blocks_text THEN
        v_remaining_text := SUBSTRING(v_task.accumulated_content FROM LENGTH(v_blocks_text) + 1);
        IF BTRIM(v_remaining_text) <> '' THEN
            v_snapshot := v_snapshot || jsonb_build_array(
                jsonb_build_object('type', 'text', 'text', v_remaining_text)
            );
        END IF;
    END IF;
    -- A failed task cannot keep a running tool spinner in its saved message.
    SELECT COALESCE(jsonb_agg(
        CASE WHEN item.value->>'type' = 'tool_step' AND item.value->>'status' = 'running'
             THEN item.value || jsonb_build_object('status', 'error')
             ELSE item.value END ORDER BY item.ordinality
    ), '[]'::JSONB) INTO v_snapshot
      FROM jsonb_array_elements(v_snapshot) WITH ORDINALITY AS item(value, ordinality);
    v_snapshot := v_snapshot || jsonb_build_array(jsonb_build_object(
        'type', 'text', 'text',
        CASE WHEN jsonb_array_length(v_snapshot) > 0 THEN E'\n\n' ELSE '' END || p_error_message
    ));

    UPDATE tasks
       SET status = 'failed',
           fail_code = LEFT(p_error_code, 50),
           error_message = p_error_message,
           completed_at = NOW(),
           lease_expires_at = NULL,
           terminal_reason = 'execution_failed'
     WHERE id = p_task_id;
    UPDATE messages
       SET content = v_snapshot,
           status = 'failed',
           is_error = TRUE
     WHERE id = v_task.assistant_message_id;
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object('outcome', 'failed', 'task_id', p_task_id);
END;
$$;

