-- 126: 企业微信对话设置的原子持久化
-- model 写入一等字段 model_id；thinking_mode 原子合并到 chat_settings。

CREATE OR REPLACE FUNCTION update_wecom_conversation_setting(
    p_conversation_id UUID,
    p_user_id UUID,
    p_setting_key TEXT,
    p_setting_value TEXT,
    p_org_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
BEGIN
    IF p_conversation_id IS NULL
       OR p_user_id IS NULL
       OR p_setting_key NOT IN ('model', 'thinking_mode')
       OR COALESCE(p_setting_value, '') = ''
       OR (
           p_setting_key = 'thinking_mode'
           AND p_setting_value NOT IN ('deep', 'fast')
       ) THEN
        RAISE EXCEPTION 'WECOM_SETTING_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
       AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND source = 'wecom'
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'WECOM_SETTING_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    IF p_setting_key = 'model' THEN
        UPDATE conversations
           SET model_id = p_setting_value,
               updated_at = NOW()
         WHERE id = p_conversation_id
         RETURNING * INTO v_conversation;
    ELSE
        UPDATE conversations
           SET chat_settings = jsonb_set(
                   COALESCE(chat_settings, '{}'::JSONB),
                   '{thinking_mode}',
                   to_jsonb(p_setting_value),
                   TRUE
               ),
               updated_at = NOW()
         WHERE id = p_conversation_id
         RETURNING * INTO v_conversation;
    END IF;

    RETURN jsonb_build_object(
        'model_id', v_conversation.model_id,
        'chat_settings', COALESCE(v_conversation.chat_settings, '{}'::JSONB)
    );
END;
$$;

REVOKE ALL ON FUNCTION update_wecom_conversation_setting(
    UUID, UUID, TEXT, TEXT, UUID
) FROM PUBLIC;

COMMENT ON FUNCTION update_wecom_conversation_setting(
    UUID, UUID, TEXT, TEXT, UUID
) IS '按 user/org/source 校验并原子持久化企业微信对话模型或思考模式';
