-- 247 rollback: 保留已修正的函数定义。
-- 回退到 246 会重新引入 PostgreSQL 的数组操作符解析错误，因此不降级
-- 已持久化表单状态机；247 本身不引入表或数据结构变化。
CREATE OR REPLACE FUNCTION public.transition_chat_form_state(
    p_message_id UUID,
    p_conversation_id UUID,
    p_form_id TEXT,
    p_expected_status TEXT,
    p_next_status TEXT,
    p_result_message TEXT DEFAULT NULL,
    p_next_form JSONB DEFAULT NULL,
    p_error_message TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_content JSONB;
    v_block JSONB;
    v_path TEXT[];
    v_current_status TEXT;
BEGIN
    IF p_expected_status NOT IN ('open', 'submitting')
       OR p_next_status NOT IN ('open', 'submitting', 'cancelled', 'submitted') THEN
        RAISE EXCEPTION 'CHAT_FORM_STATE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT content::JSONB INTO v_content FROM messages
      WHERE id = p_message_id AND conversation_id = p_conversation_id AND role = 'assistant'
      FOR UPDATE;
    IF NOT FOUND OR jsonb_typeof(v_content) <> 'array' THEN
        RETURN jsonb_build_object('outcome', 'message_missing');
    END IF;

    WITH RECURSIVE form_nodes(path, block) AS (
        SELECT ARRAY[(item.ordinality - 1)::TEXT], item.value
          FROM jsonb_array_elements(v_content) WITH ORDINALITY AS item(value, ordinality)
        UNION ALL
        SELECT array_append(node.path, 'next_form'), node.block->'next_form'
          FROM form_nodes AS node
         WHERE jsonb_typeof(node.block->'next_form') = 'object'
    )
    SELECT path, block INTO v_path, v_block
      FROM form_nodes
     WHERE block->>'type' = 'form' AND block->>'form_id' = p_form_id
     LIMIT 1;

    IF v_path IS NULL THEN
        RETURN jsonb_build_object('outcome', 'form_missing');
    END IF;

    v_current_status := COALESCE(v_block->>'status', 'open');
    IF v_current_status <> p_expected_status THEN
        RETURN jsonb_build_object('outcome', 'state_conflict', 'status', v_current_status);
    END IF;

    v_block := v_block || jsonb_build_object('status', p_next_status);
    IF p_result_message IS NOT NULL THEN
        v_block := v_block || jsonb_build_object('result_message', p_result_message);
    END IF;
    IF p_next_form IS NOT NULL THEN
        v_block := v_block || jsonb_build_object('next_form', p_next_form);
    END IF;
    IF p_error_message IS NOT NULL THEN
        v_block := v_block || jsonb_build_object('error_message', p_error_message);
    ELSIF p_next_status <> 'open' THEN
        v_block := v_block - 'error_message';
    END IF;

    v_content := jsonb_set(v_content, v_path, v_block, FALSE);
    UPDATE messages SET content = v_content::TEXT WHERE id = p_message_id;
    RETURN jsonb_build_object('outcome', 'transitioned', 'status', p_next_status);
END;
$$;

REVOKE ALL ON FUNCTION public.transition_chat_form_state(
    UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.transition_chat_form_state(
    UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT
) TO everydayai;
