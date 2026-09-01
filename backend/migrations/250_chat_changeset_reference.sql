-- 250: 将聊天表单与 ChangeSet 的关系作为展示引用持久化。
--
-- ChangeSet 仍是业务状态唯一事实来源；messages.content 只保存 form 的
-- 生命周期快照和 change_set_id，供刷新/跨端渲染定位 ChangeSet。

CREATE OR REPLACE FUNCTION public.attach_chat_form_changeset(
    p_message_id UUID,
    p_conversation_id UUID,
    p_org_id UUID,
    p_form_id TEXT,
    p_change_set_id UUID,
    p_result_message TEXT DEFAULT NULL
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
    v_current_change_set_id TEXT;
BEGIN
    IF p_form_id IS NULL OR p_form_id = '' OR p_change_set_id IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.change_sets
            WHERE id = p_change_set_id AND org_id = p_org_id
       ) THEN
        RETURN jsonb_build_object('outcome', 'changeset_missing');
    END IF;

    SELECT content::JSONB INTO v_content
      FROM public.messages
     WHERE id = p_message_id
       AND conversation_id = p_conversation_id
       AND role = 'assistant'
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
    v_current_change_set_id := v_block->>'change_set_id';
    IF v_current_status = 'submitted' AND v_current_change_set_id = p_change_set_id::TEXT THEN
        RETURN jsonb_build_object('outcome', 'existing', 'change_set_id', p_change_set_id);
    END IF;
    IF v_current_status NOT IN ('open', 'submitting') THEN
        RETURN jsonb_build_object('outcome', 'state_conflict', 'status', v_current_status);
    END IF;

    v_block := v_block || jsonb_build_object(
        'status', 'submitted', 'change_set_id', p_change_set_id
    );
    IF p_result_message IS NOT NULL THEN
        v_block := v_block || jsonb_build_object('result_message', p_result_message);
    END IF;
    v_block := v_block - 'error_message';
    v_content := jsonb_set(v_content, v_path, v_block, FALSE);
    UPDATE public.messages SET content = v_content::TEXT WHERE id = p_message_id;
    RETURN jsonb_build_object('outcome', 'transitioned', 'change_set_id', p_change_set_id);
END;
$$;

REVOKE ALL ON FUNCTION public.attach_chat_form_changeset(UUID, UUID, UUID, TEXT, UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.attach_chat_form_changeset(UUID, UUID, UUID, TEXT, UUID, TEXT) TO everydayai;
