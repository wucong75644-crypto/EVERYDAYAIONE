-- 246 rollback: 恢复 245 的顶层表单状态迁移函数。
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
    v_updated JSONB := '[]'::JSONB;
    v_found BOOLEAN := FALSE;
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

    FOR v_block IN SELECT value FROM jsonb_array_elements(v_content) LOOP
        IF v_block->>'type' = 'form' AND v_block->>'form_id' = p_form_id THEN
            v_found := TRUE;
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
        END IF;
        v_updated := v_updated || jsonb_build_array(v_block);
    END LOOP;

    IF NOT v_found THEN
        RETURN jsonb_build_object('outcome', 'form_missing');
    END IF;
    UPDATE messages SET content = v_updated::TEXT WHERE id = p_message_id;
    RETURN jsonb_build_object('outcome', 'transitioned', 'status', p_next_status);
END;
$$;

REVOKE ALL ON FUNCTION public.transition_chat_form_state(
    UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.transition_chat_form_state(
    UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT
) TO everydayai;
