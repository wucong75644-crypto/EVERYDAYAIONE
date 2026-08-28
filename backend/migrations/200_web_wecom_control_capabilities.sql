-- 200: Governed Web access to WeCom targets and proactive addressing.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION list_runtime_wecom_chat_targets(
    p_org_id UUID,
    p_groups_only BOOLEAN,
    p_active_only BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', target.id,
        'chatid', target.chatid,
        'chat_type', target.chat_type,
        'chat_name', target.chat_name,
        'last_active', target.last_active,
        'first_seen', target.first_seen,
        'message_count', target.message_count,
        'is_active', target.is_active
    ) ORDER BY target.last_active DESC), '[]'::JSONB)
      INTO v_result
      FROM public.wecom_chat_targets target
     WHERE target.org_id = p_org_id
       AND (NOT p_groups_only OR target.chat_type = 'group')
       AND (NOT p_active_only OR target.is_active);
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_governed_wecom_chat_targets(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    RETURN public.list_runtime_wecom_chat_targets(
        p_org_id, TRUE, FALSE
    );
END;
$$;

CREATE OR REPLACE FUNCTION update_governed_wecom_chat_target_name(
    p_org_id UUID,
    p_target_id UUID,
    p_chat_name TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_updated INTEGER;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    IF NULLIF(BTRIM(p_chat_name), '') IS NULL
       OR length(BTRIM(p_chat_name)) > 256 THEN
        RAISE EXCEPTION 'WECOM_TARGET_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE public.wecom_chat_targets
       SET chat_name = BTRIM(p_chat_name)
     WHERE id = p_target_id
       AND org_id = p_org_id;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN jsonb_build_object('updated', v_updated);
END;
$$;

CREATE OR REPLACE FUNCTION resolve_governed_wecom_push_target(
    p_org_id UUID,
    p_target_user_id UUID,
    p_chatid TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    IF p_chatid IS NOT NULL THEN
        SELECT jsonb_build_object(
            'chatid', target.chatid,
            'chattype', target.chat_type
        ) INTO v_result
          FROM public.wecom_chat_targets target
         WHERE target.org_id = p_org_id
           AND target.chatid = BTRIM(p_chatid)
           AND target.is_active;
    ELSIF p_target_user_id IS NOT NULL THEN
        SELECT jsonb_build_object(
            'chatid', mapping.last_chatid,
            'chattype', COALESCE(mapping.last_chat_type, 'single')
        ) INTO v_result
          FROM public.wecom_user_mappings mapping
          JOIN public.org_members member
            ON member.org_id = mapping.org_id
           AND member.user_id = mapping.user_id
           AND member.status = 'active'
         WHERE mapping.org_id = p_org_id
           AND mapping.user_id = p_target_user_id
           AND mapping.last_chatid IS NOT NULL
         ORDER BY mapping.created_at DESC
         LIMIT 1;
    ELSE
        RAISE EXCEPTION 'WECOM_TARGET_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION is_runtime_wecom_self_target(
    p_org_id UUID,
    p_wecom_userid TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    v_actor := public.tenant_actor_user_id();
    RETURN EXISTS (
        SELECT 1
          FROM public.wecom_user_mappings mapping
         WHERE mapping.org_id = p_org_id
           AND mapping.user_id = v_actor
           AND mapping.wecom_userid = BTRIM(p_wecom_userid)
    );
END;
$$;

REVOKE ALL ON FUNCTION
    list_runtime_wecom_chat_targets(UUID, BOOLEAN, BOOLEAN),
    list_governed_wecom_chat_targets(UUID),
    update_governed_wecom_chat_target_name(UUID, UUID, TEXT),
    resolve_governed_wecom_push_target(UUID, UUID, TEXT),
    is_runtime_wecom_self_target(UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION
    list_runtime_wecom_chat_targets(UUID, BOOLEAN, BOOLEAN),
    list_governed_wecom_chat_targets(UUID),
    update_governed_wecom_chat_target_name(UUID, UUID, TEXT),
    resolve_governed_wecom_push_target(UUID, UUID, TEXT),
    is_runtime_wecom_self_target(UUID, TEXT)
TO everydayai_runtime;

RESET ROLE;
