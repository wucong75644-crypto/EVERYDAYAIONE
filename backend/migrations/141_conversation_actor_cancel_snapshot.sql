-- 141: Conversation Actor 取消安全点与中断快照。
-- 取消不能只终止 tasks；必须先把当前 fencing owner 已保存的进度
-- 投影到 messages，保证后续新 turn 可以读取完整 interrupted history。

CREATE OR REPLACE FUNCTION materialize_actor_cancel_snapshot(
    p_task_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;
    v_blocks JSONB;
    v_content JSONB;
    v_block_text TEXT := '';
    v_remaining TEXT := '';
    v_now TIMESTAMPTZ := NOW();
BEGIN
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL OR v_task.assistant_message_id IS NULL THEN
        RETURN jsonb_build_object('saved', FALSE, 'reason', 'message_missing');
    END IF;

    SELECT * INTO v_message
      FROM messages
     WHERE id = v_task.assistant_message_id
     FOR UPDATE;
    IF v_message.id IS NULL THEN
        RETURN jsonb_build_object('saved', FALSE, 'reason', 'message_missing');
    END IF;
    IF v_message.status::TEXT = 'completed' THEN
        RETURN jsonb_build_object('saved', FALSE, 'reason', 'already_completed');
    END IF;

    v_blocks := CASE
        WHEN jsonb_typeof(COALESCE(v_task.accumulated_blocks, '[]'::JSONB)) = 'array'
            THEN COALESCE(v_task.accumulated_blocks, '[]'::JSONB)
        ELSE '[]'::JSONB
    END;

    -- 如果 Actor 还没有写进度，保留消息表中已有的非空内容。
    IF jsonb_array_length(v_blocks) = 0
       AND COALESCE(v_task.accumulated_content, '') = ''
       AND jsonb_typeof(COALESCE(v_message.content, '[]'::JSONB)) = 'array' THEN
        v_blocks := COALESCE(v_message.content, '[]'::JSONB);
    END IF;

    SELECT COALESCE(
        string_agg(COALESCE(item.value->>'text', ''), '' ORDER BY item.ordinality),
        ''
    )
      INTO v_block_text
      FROM jsonb_array_elements(v_blocks) WITH ORDINALITY AS item(value, ordinality)
     WHERE jsonb_typeof(item.value) = 'object'
       AND item.value->>'type' = 'text';

    IF COALESCE(v_task.accumulated_content, '') <> ''
       AND LEFT(v_task.accumulated_content, LENGTH(v_block_text)) = v_block_text THEN
        v_remaining := SUBSTRING(
            v_task.accumulated_content FROM LENGTH(v_block_text) + 1
        );
        IF BTRIM(v_remaining) <> '' THEN
            v_blocks := v_blocks || jsonb_build_array(
                jsonb_build_object('type', 'text', 'text', v_remaining)
            );
        END IF;
    ELSIF jsonb_array_length(v_blocks) = 0
          AND BTRIM(COALESCE(v_task.accumulated_content, '')) <> '' THEN
        v_blocks := jsonb_build_array(
            jsonb_build_object('type', 'text', 'text', v_task.accumulated_content)
        );
    END IF;

    SELECT COALESCE(
        jsonb_agg(
            CASE
                WHEN item.value->>'type' = 'tool_step'
                 AND item.value->>'status' = 'running'
                THEN item.value || jsonb_build_object(
                    'status', 'cancelled',
                    'cancelled_at', v_now
                )
                ELSE item.value
            END
            ORDER BY item.ordinality
        ),
        '[]'::JSONB
    )
      INTO v_content
      FROM jsonb_array_elements(v_blocks) WITH ORDINALITY AS item(value, ordinality);

    IF NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_content) AS item(value)
         WHERE item.value->>'type' = 'interrupt_marker'
    ) THEN
        v_content := v_content || jsonb_build_array(
            jsonb_build_object(
                'type', 'interrupt_marker',
                'interrupted_at', v_now,
                'reason', 'user_cancel'
            )
        );
    END IF;

    UPDATE messages
       SET content = v_content,
           status = 'interrupted',
           is_error = FALSE
     WHERE id = v_message.id
       AND status::TEXT <> 'completed';

    RETURN jsonb_build_object(
        'saved', TRUE,
        'message_id', v_message.id,
        'content', v_content
    );
END;
$$;

CREATE OR REPLACE FUNCTION cancel_generation_turn_owned(
    p_task_id UUID,
    p_execution_token UUID,
    p_reason TEXT DEFAULT 'user_cancelled'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_snapshot JSONB;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL
       OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'already_cancelled', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = v_task.conversation_id
     FOR UPDATE;
    IF v_conversation.id IS NULL
       OR v_conversation.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    v_snapshot := materialize_actor_cancel_snapshot(p_task_id);

    UPDATE tasks
       SET status = 'cancelled',
           error_message = COALESCE(NULLIF(BTRIM(p_reason), ''), '用户取消了任务'),
           completed_at = NOW(),
           execution_token = NULL,
           lease_expires_at = NULL,
           terminal_reason = 'user_cancelled'
     WHERE id = p_task_id;
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object(
        'outcome', 'cancelled',
        'task_id', p_task_id,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$$;

-- 保持旧用户范围取消入口的兼容性，同时补齐快照落盘。
CREATE OR REPLACE FUNCTION cancel_generation_turn(
    p_task_id UUID,
    p_user_id UUID,
    p_org_id UUID DEFAULT NULL
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
    v_snapshot JSONB;
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_ARGUMENT_INVALID' USING ERRCODE = '22023';
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
       OR v_task.user_id IS DISTINCT FROM p_user_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.user_id IS DISTINCT FROM p_user_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'already_cancelled', 'task_id', p_task_id);
    END IF;
    IF v_task.status NOT IN ('pending', 'running') THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;

    v_snapshot := materialize_actor_cancel_snapshot(p_task_id);
    UPDATE tasks
       SET status = 'cancelled',
           error_message = '用户取消了任务',
           completed_at = NOW(),
           execution_token = NULL,
           lease_expires_at = NULL,
           terminal_reason = 'user_cancelled'
     WHERE id = p_task_id;
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object(
        'outcome', 'cancelled',
        'task_id', p_task_id,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$$;

-- 当前 owner 终止任务时，原先 pending 的 cancel 事件必须同步收敛为 applied。
CREATE OR REPLACE FUNCTION create_actor_cancel_control_command()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
    IF NEW.status = 'cancelled'
       AND OLD.status IS DISTINCT FROM NEW.status
       AND NEW.type = 'chat'
       AND (NEW.delivery_context @> '{"actor": true}'::JSONB) THEN
        INSERT INTO conversation_control_events(
            conversation_id, task_id, turn_id, event_type, dedupe_key, payload,
            status, applied_at
        ) VALUES (
            NEW.conversation_id, NEW.id, NEW.turn_id, 'cancel',
            'cancel:' || NEW.id::TEXT,
            jsonb_build_object(
                'reason', COALESCE(NEW.terminal_reason, 'user_cancelled')
            ),
            'applied', NOW()
        )
        ON CONFLICT (task_id, dedupe_key) DO UPDATE
            SET status = 'applied', applied_at = NOW();
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tasks_actor_cancel_control_event_trigger ON tasks;
CREATE TRIGGER tasks_actor_cancel_control_event_trigger
AFTER UPDATE OF status ON tasks
FOR EACH ROW
EXECUTE FUNCTION create_actor_cancel_control_command();

REVOKE ALL ON FUNCTION materialize_actor_cancel_snapshot(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION cancel_generation_turn_owned(UUID, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION cancel_generation_turn(UUID, UUID, UUID) FROM PUBLIC;

COMMENT ON FUNCTION cancel_generation_turn_owned(UUID, UUID, TEXT)
    IS '当前 fencing owner 在安全点保存 Actor 中断快照并原子终止生成';
