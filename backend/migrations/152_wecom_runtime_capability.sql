-- 152: WeCom 入站 runtime 独立能力边界。
-- 部署前置：everydayai_wecom_runtime 已创建，第二批对象 owner 已转移。
-- 本迁移只创建安全门面并撤销 PUBLIC；角色 EXECUTE 由后续最小授权迁移授予。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION tenant_database_role_matches_scope()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT CASE session_user
        WHEN 'everydayai_runtime' THEN
            current_setting('app.access_kind', TRUE) = 'runtime'
        WHEN 'everydayai_wecom_runtime' THEN
            current_setting('app.access_kind', TRUE) = 'runtime'
        WHEN 'everydayai_worker' THEN
            current_setting('app.access_kind', TRUE) = 'worker'
        ELSE FALSE
    END
$$;

CREATE OR REPLACE FUNCTION _assert_wecom_ingress_scope(
    p_org_id UUID,
    p_corp_id TEXT
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_wecom_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'WECOM_INGRESS_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_org_id IS NULL OR COALESCE(BTRIM(p_corp_id), '') = '' THEN
        RAISE EXCEPTION 'WECOM_INGRESS_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF (
        SELECT COUNT(*)
          FROM public.organizations organization
         WHERE organization.status = 'active'
           AND BTRIM(organization.wecom_corp_id) = BTRIM(p_corp_id)
    ) <> 1 OR NOT EXISTS (
        SELECT 1 FROM public.organizations organization
         WHERE organization.id = p_org_id
           AND organization.status = 'active'
           AND BTRIM(organization.wecom_corp_id) = BTRIM(p_corp_id)
    ) THEN
        RAISE EXCEPTION 'WECOM_INGRESS_ORG_CORP_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION resolve_wecom_ingress_user(
    p_wecom_userid TEXT,
    p_corp_id TEXT,
    p_org_id UUID,
    p_channel TEXT,
    p_display_name TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user_id UUID;
    v_is_new BOOLEAN := FALSE;
    v_member_status TEXT;
    v_lock_key BIGINT;
BEGIN
    PERFORM public._assert_wecom_ingress_scope(p_org_id, p_corp_id);
    IF COALESCE(BTRIM(p_wecom_userid), '') = ''
       OR p_channel NOT IN ('smart_robot', 'app')
       OR COALESCE(BTRIM(p_display_name), '') = '' THEN
        RAISE EXCEPTION 'WECOM_INGRESS_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    v_lock_key := hashtextextended(
        p_org_id::TEXT || '::' || BTRIM(p_corp_id)
        || '::' || BTRIM(p_wecom_userid),
        0
    );
    PERFORM pg_advisory_xact_lock(v_lock_key);

    SELECT mapping.user_id
      INTO v_user_id
      FROM public.wecom_user_mappings mapping
     WHERE mapping.wecom_userid = BTRIM(p_wecom_userid)
       AND mapping.corp_id = BTRIM(p_corp_id)
       AND mapping.org_id = p_org_id;

    IF v_user_id IS NULL THEN
        UPDATE public.wecom_user_mappings
           SET org_id = p_org_id
         WHERE wecom_userid = BTRIM(p_wecom_userid)
           AND corp_id = BTRIM(p_corp_id)
           AND org_id IS NULL
        RETURNING user_id INTO v_user_id;
    END IF;

    IF v_user_id IS NULL THEN
        INSERT INTO public.users(
            nickname, login_methods, created_by, role,
            credits, status, last_login_at
        ) VALUES (
            BTRIM(p_display_name), '["wecom"]'::JSONB,
            'wecom'::public.user_created_by, 'user'::public.user_role,
            100, 'active'::public.account_status, NOW()
        )
        RETURNING id INTO v_user_id;

        INSERT INTO public.wecom_user_mappings(
            wecom_userid, corp_id, user_id, channel,
            wecom_nickname, org_id
        ) VALUES (
            BTRIM(p_wecom_userid), BTRIM(p_corp_id), v_user_id,
            p_channel, BTRIM(p_display_name), p_org_id
        );
        INSERT INTO public.credits_history(
            user_id, change_amount, balance_after,
            change_type, description, org_id
        ) VALUES (
            v_user_id, 100, 100,
            'register_gift'::public.credits_change_type,
            '企业微信用户注册赠送积分', p_org_id
        );
        v_is_new := TRUE;
    ELSE
        UPDATE public.users
           SET last_login_at = NOW()
         WHERE id = v_user_id
           AND status = 'active';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'WECOM_INGRESS_USER_INACTIVE'
                USING ERRCODE = '42501';
        END IF;
    END IF;

    INSERT INTO public.org_members(org_id, user_id, role, status)
    VALUES (p_org_id, v_user_id, 'member', 'active')
    ON CONFLICT (org_id, user_id) DO NOTHING;

    SELECT member.status
      INTO v_member_status
      FROM public.org_members member
     WHERE member.org_id = p_org_id
       AND member.user_id = v_user_id;
    IF v_member_status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'WECOM_INGRESS_MEMBER_INACTIVE'
            USING ERRCODE = '42501';
    END IF;

    RETURN jsonb_build_object(
        'user_id', v_user_id,
        'is_new', v_is_new
    );
END;
$$;

CREATE OR REPLACE FUNCTION update_wecom_ingress_chat_address(
    p_wecom_userid TEXT,
    p_corp_id TEXT,
    p_chatid TEXT,
    p_chattype TEXT,
    p_org_id UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_wecom_ingress_scope(p_org_id, p_corp_id);
    IF COALESCE(BTRIM(p_wecom_userid), '') = ''
       OR COALESCE(BTRIM(p_chatid), '') = ''
       OR p_chattype NOT IN ('single', 'group') THEN
        RAISE EXCEPTION 'WECOM_INGRESS_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.wecom_user_mappings
       SET last_chatid = BTRIM(p_chatid),
           last_chat_type = p_chattype
     WHERE wecom_userid = BTRIM(p_wecom_userid)
       AND corp_id = BTRIM(p_corp_id)
       AND org_id = p_org_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'WECOM_INGRESS_MAPPING_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION upsert_wecom_ingress_chat_target(
    p_chatid TEXT,
    p_chattype TEXT,
    p_corp_id TEXT,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_target public.wecom_chat_targets%ROWTYPE;
BEGIN
    PERFORM public._assert_wecom_ingress_scope(p_org_id, p_corp_id);
    IF COALESCE(BTRIM(p_chatid), '') = ''
       OR p_chattype NOT IN ('single', 'group') THEN
        RAISE EXCEPTION 'WECOM_INGRESS_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.wecom_chat_targets(
        chatid, chat_type, corp_id, org_id
    ) VALUES (
        BTRIM(p_chatid), p_chattype, BTRIM(p_corp_id), p_org_id
    )
    ON CONFLICT (chatid, corp_id) DO UPDATE
       SET chat_type = EXCLUDED.chat_type,
           org_id = EXCLUDED.org_id,
           last_active = NOW(),
           message_count = public.wecom_chat_targets.message_count + 1,
           is_active = TRUE
     WHERE public.wecom_chat_targets.org_id IS NULL
        OR public.wecom_chat_targets.org_id = EXCLUDED.org_id
    RETURNING * INTO v_target;
    IF v_target.id IS NULL THEN
        RAISE EXCEPTION 'WECOM_INGRESS_TARGET_SCOPE_CONFLICT'
            USING ERRCODE = '42501';
    END IF;
    RETURN jsonb_build_object(
        'target_id', v_target.id,
        'message_count', v_target.message_count
    );
END;
$$;

REVOKE ALL ON FUNCTION wecom_get_or_create_user(
    TEXT, TEXT, UUID, TEXT, TEXT
) FROM PUBLIC;
DO $legacy_compatibility$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai'
    ) THEN
        GRANT EXECUTE ON FUNCTION wecom_get_or_create_user(
            TEXT, TEXT, UUID, TEXT, TEXT
        ) TO everydayai;
    END IF;
END
$legacy_compatibility$;
REVOKE ALL ON FUNCTION _assert_wecom_ingress_scope(
    UUID, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION resolve_wecom_ingress_user(
    TEXT, TEXT, UUID, TEXT, TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION update_wecom_ingress_chat_address(
    TEXT, TEXT, TEXT, TEXT, UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION upsert_wecom_ingress_chat_target(
    TEXT, TEXT, TEXT, UUID
) FROM PUBLIC;

RESET ROLE;
