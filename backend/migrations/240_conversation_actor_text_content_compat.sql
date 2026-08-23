-- 240: 兼容生产 messages.content=text 的 Actor 暂停快照契约。
--
-- 生产 messages.content 是 TEXT，内容本身通常是 JSON 数组字符串。
-- 239 的快照函数直接把该列与 JSONB 混合运算，暂停到安全点时会触发
-- PostgreSQL DatatypeMismatch，导致 accumulated progress 无法物化到消息。
-- 本迁移只替换快照函数，不改变现有表字段和 Actor 状态契约。

CREATE OR REPLACE FUNCTION public.actor_message_text_to_blocks(
    p_content TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public
AS $$
DECLARE
    v_value JSONB;
    v_text TEXT := COALESCE(p_content, '');
BEGIN
    IF BTRIM(v_text) = '' THEN
        RETURN '[]'::JSONB;
    END IF;

    BEGIN
        v_value := v_text::JSONB;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN jsonb_build_array(
            jsonb_build_object('type', 'text', 'text', v_text)
        );
    END;

    IF jsonb_typeof(v_value) = 'array' THEN
        RETURN v_value;
    END IF;
    IF jsonb_typeof(v_value) = 'string' THEN
        RETURN jsonb_build_array(
            jsonb_build_object('type', 'text', 'text', v_value #>> '{}')
        );
    END IF;
    RETURN jsonb_build_array(
        jsonb_build_object('type', 'text', 'text', v_text)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.materialize_actor_cancel_snapshot(
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
    v_message_blocks JSONB;
    v_content JSONB;
    v_block_text TEXT := '';
    v_remaining TEXT := '';
    v_now TIMESTAMPTZ := NOW();
BEGIN
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL OR v_task.assistant_message_id IS NULL THEN
        RETURN jsonb_build_object('saved', FALSE, 'reason', 'message_missing');
    END IF;
    SELECT * INTO v_message FROM messages
     WHERE id = v_task.assistant_message_id FOR UPDATE;
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
    IF jsonb_array_length(v_blocks) = 0
       AND COALESCE(v_task.accumulated_content, '') = '' THEN
        v_message_blocks := public.actor_message_text_to_blocks(v_message.content);
        IF jsonb_typeof(v_message_blocks) = 'array' THEN
            v_blocks := v_message_blocks;
        END IF;
    END IF;

    SELECT COALESCE(
        string_agg(COALESCE(item.value->>'text', ''), '' ORDER BY item.ordinality), ''
    ) INTO v_block_text
      FROM jsonb_array_elements(v_blocks) WITH ORDINALITY AS item(value, ordinality)
     WHERE jsonb_typeof(item.value) = 'object' AND item.value->>'type' = 'text';

    IF COALESCE(v_task.accumulated_content, '') <> ''
       AND LEFT(v_task.accumulated_content, LENGTH(v_block_text)) = v_block_text THEN
        v_remaining := SUBSTRING(v_task.accumulated_content FROM LENGTH(v_block_text) + 1);
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

    SELECT COALESCE(jsonb_agg(
        CASE
            WHEN item.value->>'type' = 'tool_step'
             AND item.value->>'status' = 'running'
            THEN item.value || jsonb_build_object('status', 'cancelled', 'cancelled_at', v_now)
            ELSE item.value
        END ORDER BY item.ordinality
    ), '[]'::JSONB) INTO v_content
      FROM jsonb_array_elements(v_blocks) WITH ORDINALITY AS item(value, ordinality);

    IF NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(v_content) AS item(value)
         WHERE item.value->>'type' = 'interrupt_marker'
    ) THEN
        v_content := v_content || jsonb_build_array(
            jsonb_build_object(
                'type', 'interrupt_marker', 'interrupted_at', v_now,
                'reason', 'user_cancel'
            )
        );
    END IF;

    -- messages.content 的生产契约是 TEXT，保存 JSONB 的文本表示。
    UPDATE messages SET content = v_content::TEXT, status = 'interrupted', is_error = FALSE
     WHERE id = v_message.id AND status::TEXT <> 'completed';
    RETURN jsonb_build_object(
        'saved', TRUE, 'message_id', v_message.id, 'content', v_content
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.materialize_actor_pause_snapshot(
    p_task_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_snapshot JSONB;
    v_content JSONB;
BEGIN
    v_snapshot := public.materialize_actor_cancel_snapshot(p_task_id);
    IF COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE) IS NOT TRUE THEN
        RETURN v_snapshot;
    END IF;

    SELECT COALESCE(jsonb_agg(
        CASE
            WHEN item.value->>'type' = 'interrupt_marker'
            THEN item.value || jsonb_build_object('reason', 'user_pause')
            ELSE item.value
        END ORDER BY item.ordinality
    ), '[]'::JSONB) INTO v_content
      FROM jsonb_array_elements(COALESCE(v_snapshot->'content', '[]'::JSONB))
           WITH ORDINALITY AS item(value, ordinality);
    UPDATE messages SET content = v_content::TEXT
     WHERE id = (v_snapshot->>'message_id')::UUID;
    RETURN v_snapshot || jsonb_build_object('content', v_content);
END;
$$;

-- 240 rollback deliberately leaves the compatibility functions in place.
-- Application rollback must not reintroduce the production text/jsonb mismatch.
