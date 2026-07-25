-- 168: WeCom runtime 读取能力门面。
-- 消除消息热路径对 users、conversations、wecom_user_mappings 的直表访问。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_wecom_generation_context(
    p_user_id UUID,
    p_conversation_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_org UUID := public.tenant_org_id();
    v_credits INTEGER;
    v_conversation public.conversations%ROWTYPE;
BEGIN
    PERFORM public._assert_wecom_message_scope(v_org, v_actor);
    IF p_user_id IS NOT NULL AND p_user_id IS DISTINCT FROM v_actor THEN
        RAISE EXCEPTION 'WECOM_GENERATION_CONTEXT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT credits INTO v_credits
      FROM public.users
     WHERE id = v_actor AND status::TEXT = 'active';
    IF v_credits IS NULL THEN
        RAISE EXCEPTION 'WECOM_GENERATION_CONTEXT_USER_MISSING'
            USING ERRCODE = '42501';
    END IF;

    IF p_conversation_id IS NOT NULL THEN
        SELECT * INTO v_conversation
          FROM public.conversations
         WHERE id = p_conversation_id
           AND org_id = v_org
           AND source = 'wecom'
           AND (
               (scope_type = 'user' AND user_id = v_actor)
               OR scope_type = 'channel'
           );
        IF NOT FOUND THEN
            RAISE EXCEPTION 'WECOM_GENERATION_CONTEXT_CONVERSATION_MISSING'
                USING ERRCODE = '42501';
        END IF;
    END IF;

    RETURN jsonb_build_object(
        'credits', v_credits,
        'model_id', COALESCE(v_conversation.model_id, 'auto'),
        'chat_settings', COALESCE(v_conversation.chat_settings, '{}'::JSONB)
    );
END;
$$;

CREATE FUNCTION update_wecom_ingress_display_name(
    p_user_id UUID,
    p_wecom_userid TEXT,
    p_corp_id TEXT,
    p_org_id UUID,
    p_display_name TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_updated INTEGER;
BEGIN
    PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id);
    PERFORM public._assert_wecom_ingress_scope(p_org_id, p_corp_id);
    IF NULLIF(BTRIM(p_wecom_userid), '') IS NULL
       OR NULLIF(BTRIM(p_display_name), '') IS NULL
       OR LENGTH(BTRIM(p_display_name)) > 100 THEN
        RAISE EXCEPTION 'WECOM_DISPLAY_NAME_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.wecom_user_mappings
       SET wecom_nickname = BTRIM(p_display_name)
     WHERE user_id = p_user_id
       AND org_id = p_org_id
       AND corp_id = BTRIM(p_corp_id)
       AND wecom_userid = BTRIM(p_wecom_userid);
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated <> 1 THEN
        RAISE EXCEPTION 'WECOM_DISPLAY_NAME_MAPPING_MISSING'
            USING ERRCODE = '42501';
    END IF;

    UPDATE public.users
       SET nickname = BTRIM(p_display_name)
     WHERE id = p_user_id
       AND nickname LIKE '企微用户\_%' ESCAPE '\';
    RETURN jsonb_build_object('outcome', 'updated');
END;
$$;

CREATE FUNCTION reset_wecom_conversation(
    p_conversation_id UUID,
    p_user_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_binding public.conversation_channel_bindings%ROWTYPE;
    v_old public.conversations%ROWTYPE;
    v_new_id UUID;
BEGIN
    PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id);
    SELECT * INTO v_binding
      FROM public.conversation_channel_bindings
     WHERE conversation_id = p_conversation_id
       AND org_id = p_org_id
       AND (chat_type = 'group' OR owner_user_id = p_user_id)
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'WECOM_CONVERSATION_RESET_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO STRICT v_old
      FROM public.conversations
     WHERE id = p_conversation_id AND org_id = p_org_id;

    INSERT INTO public.conversations(
        user_id, org_id, title, source, message_count, credits_consumed,
        scope_type, scope_id, model_id, chat_settings
    ) VALUES (
        v_old.user_id, p_org_id,
        CASE WHEN v_binding.chat_type = 'group'
            THEN '企微群聊' ELSE '企微对话' END,
        'wecom', 0, 0, v_old.scope_type, v_old.scope_id,
        'auto', '{}'::JSONB
    ) RETURNING id INTO v_new_id;

    UPDATE public.conversation_channel_bindings
       SET conversation_id = v_new_id, last_seen_at = NOW()
     WHERE id = v_binding.id;
    RETURN jsonb_build_object(
        'outcome', 'reset', 'conversation_id', v_new_id
    );
END;
$$;

CREATE FUNCTION get_wecom_manual_memories(
    p_user_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id);
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', atom.id,
        'memory', atom.content,
        'metadata', jsonb_build_object(
            'source', CASE atom.source_kind
                WHEN 'manual' THEN 'manual' ELSE 'auto' END
        ),
        'created_at', atom.created_at,
        'updated_at', atom.updated_at
    ) ORDER BY atom.updated_at DESC), '[]'::JSONB)
      INTO v_result
      FROM (
          SELECT *
            FROM public.memory_atoms
           WHERE user_id = p_user_id
             AND org_id = p_org_id
             AND status = 'active'
             AND NOT is_deleted
           ORDER BY updated_at DESC
           LIMIT 100
      ) atom;
    RETURN v_result;
END;
$$;

CREATE FUNCTION clear_wecom_manual_memories(
    p_user_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted_count INTEGER;
BEGIN
    PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id);
    UPDATE public.memory_atoms
       SET status = 'deleted', is_deleted = TRUE, updated_at = NOW()
     WHERE user_id = p_user_id
       AND org_id = p_org_id
       AND status = 'active'
       AND NOT is_deleted;
    GET DIAGNOSTICS v_deleted_count = ROW_COUNT;
    RETURN jsonb_build_object(
        'outcome', 'cleared', 'deleted_count', v_deleted_count
    );
END;
$$;

REVOKE ALL ON FUNCTION
    get_wecom_generation_context(UUID, UUID),
    update_wecom_ingress_display_name(UUID, TEXT, TEXT, UUID, TEXT),
    reset_wecom_conversation(UUID, UUID, UUID),
    get_wecom_manual_memories(UUID, UUID),
    clear_wecom_manual_memories(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION
    get_wecom_generation_context(UUID, UUID),
    update_wecom_ingress_display_name(UUID, TEXT, TEXT, UUID, TEXT),
    reset_wecom_conversation(UUID, UUID, UUID),
    get_wecom_manual_memories(UUID, UUID),
    clear_wecom_manual_memories(UUID, UUID)
TO everydayai_wecom_runtime;

DO $legacy_compatibility$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai') THEN
        GRANT EXECUTE ON FUNCTION
            get_wecom_generation_context(UUID, UUID),
            update_wecom_ingress_display_name(UUID, TEXT, TEXT, UUID, TEXT),
            reset_wecom_conversation(UUID, UUID, UUID),
            get_wecom_manual_memories(UUID, UUID),
            clear_wecom_manual_memories(UUID, UUID)
        TO everydayai;
    END IF;
END
$legacy_compatibility$;

RESET ROLE;
