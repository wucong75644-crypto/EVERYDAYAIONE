-- 155: Web WeCom OAuth capability boundary.
-- Prerequisites: migrations 150-154 and runtime/message object ownership transfer.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_web_wecom_oauth_scope(
    p_org_id UUID,
    p_actor_required BOOLEAN
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id
       OR (p_actor_required AND public.tenant_actor_user_id() IS NULL)
       OR (NOT p_actor_required AND public.tenant_actor_user_id() IS NOT NULL) THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION get_web_wecom_oauth_public_config(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_web_wecom_oauth_scope(p_org_id, FALSE);
    IF p_org_id IS NULL THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT jsonb_build_object(
        'corp_id', organization.wecom_corp_id,
        'agent_id_encrypted', agent.config_value_encrypted,
        'encrypt_key', organization.encrypt_key
    ) INTO v_result
      FROM public.organizations organization
      LEFT JOIN public.org_configs agent
        ON agent.org_id = organization.id
       AND agent.config_key = 'wecom_agent_id'
     WHERE organization.id = p_org_id
       AND organization.status = 'active';
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION get_web_wecom_oauth_exchange_config(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_web_wecom_oauth_scope(p_org_id, FALSE);
    IF p_org_id IS NULL THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT jsonb_build_object(
        'corp_id', organization.wecom_corp_id,
        'agent_secret_encrypted', secret.config_value_encrypted,
        'encrypt_key', organization.encrypt_key
    ) INTO v_result
      FROM public.organizations organization
      LEFT JOIN public.org_configs secret
        ON secret.org_id = organization.id
       AND secret.config_key = 'wecom_agent_secret'
     WHERE organization.id = p_org_id
       AND organization.status = 'active';
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION commit_web_wecom_login(
    p_wecom_userid TEXT,
    p_corp_id TEXT,
    p_org_id UUID,
    p_display_name TEXT,
    p_refresh_hash TEXT,
    p_refresh_expires_at TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user public.users%ROWTYPE;
    v_mapping public.wecom_user_mappings%ROWTYPE;
    v_member public.org_members%ROWTYPE;
    v_org public.organizations%ROWTYPE;
    v_is_new BOOLEAN := FALSE;
BEGIN
    PERFORM public._assert_web_wecom_oauth_scope(p_org_id, FALSE);
    IF p_org_id IS NULL
       OR COALESCE(BTRIM(p_wecom_userid), '') = ''
       OR COALESCE(BTRIM(p_corp_id), '') = ''
       OR COALESCE(BTRIM(p_display_name), '') = ''
       OR LENGTH(BTRIM(p_display_name)) > 50
       OR p_refresh_hash !~ '^[0-9a-f]{64}$'
       OR p_refresh_expires_at <= NOW() THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_org FROM public.organizations
     WHERE id = p_org_id
       AND status = 'active'
       AND BTRIM(wecom_corp_id) = BTRIM(p_corp_id);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_ORG_CORP_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        BTRIM(p_corp_id) || '::' || BTRIM(p_wecom_userid), 0
    ));
    SELECT * INTO v_mapping FROM public.wecom_user_mappings
     WHERE wecom_userid = BTRIM(p_wecom_userid)
       AND corp_id = BTRIM(p_corp_id)
     FOR UPDATE;
    IF v_mapping.id IS NULL THEN
        INSERT INTO public.users(
            nickname, login_methods, created_by, role, credits, status
        ) VALUES (
            BTRIM(p_display_name), '["wecom"]'::JSONB,
            'wecom'::public.user_created_by, 'user'::public.user_role,
            100, 'active'::public.account_status
        ) RETURNING * INTO v_user;
        INSERT INTO public.wecom_user_mappings(
            wecom_userid, corp_id, user_id, channel,
            wecom_nickname, org_id
        ) VALUES (
            BTRIM(p_wecom_userid), BTRIM(p_corp_id), v_user.id,
            'oauth', BTRIM(p_display_name), p_org_id
        );
        INSERT INTO public.credits_history(
            user_id, change_amount, balance_after,
            change_type, description, org_id
        ) VALUES (
            v_user.id, 100, 100,
            'register_gift'::public.credits_change_type,
            '企业微信用户注册赠送积分', p_org_id
        );
        v_is_new := TRUE;
    ELSE
        IF v_mapping.org_id IS NULL THEN
            UPDATE public.wecom_user_mappings
               SET org_id = p_org_id
             WHERE id = v_mapping.id
               AND org_id IS NULL
             RETURNING * INTO v_mapping;
        END IF;
        IF v_mapping.org_id IS DISTINCT FROM p_org_id THEN
            RAISE EXCEPTION 'WEB_WECOM_OAUTH_IDENTITY_SCOPE_CONFLICT'
                USING ERRCODE = '42501';
        END IF;
        SELECT * INTO v_user FROM public.users
         WHERE id = v_mapping.user_id FOR UPDATE;
    END IF;
    IF v_user.id IS NULL OR v_user.status::TEXT <> 'active' THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_PRINCIPAL_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.org_members(org_id, user_id, role, status)
    VALUES (p_org_id, v_user.id, 'member', 'active')
    ON CONFLICT (org_id, user_id) DO NOTHING;
    SELECT * INTO v_member FROM public.org_members
     WHERE org_id = p_org_id AND user_id = v_user.id;
    IF v_member.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_MEMBER_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.users SET
        current_org_id = p_org_id,
        last_login_at = NOW(),
        last_active_at = NOW()
     WHERE id = v_user.id RETURNING * INTO v_user;
    INSERT INTO public.refresh_tokens(user_id, token_hash, expires_at)
    VALUES (v_user.id, p_refresh_hash, p_refresh_expires_at);
    INSERT INTO public.user_activity_events(
        user_id, org_id, event_type, source, occurred_at
    ) VALUES (
        v_user.id, p_org_id, 'login_success', 'wecom', NOW()
    );
    RETURN (to_jsonb(v_user) - 'password_hash') || jsonb_build_object(
        'is_new', v_is_new,
        'org_id', v_org.id,
        'org_name', v_org.name,
        'org_role', v_member.role
    );
END;
$$;

CREATE OR REPLACE FUNCTION bind_web_wecom_identity(
    p_wecom_userid TEXT,
    p_corp_id TEXT,
    p_org_id UUID,
    p_display_name TEXT,
    p_refresh_hash TEXT,
    p_refresh_expires_at TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_keep public.users%ROWTYPE;
    v_mapping public.wecom_user_mappings%ROWTYPE;
    v_actor UUID := public.tenant_actor_user_id();
BEGIN
    PERFORM public._assert_web_wecom_oauth_scope(p_org_id, TRUE);
    IF COALESCE(BTRIM(p_wecom_userid), '') = ''
       OR COALESCE(BTRIM(p_corp_id), '') = ''
       OR COALESCE(BTRIM(p_display_name), '') = ''
       OR LENGTH(BTRIM(p_display_name)) > 50
       OR p_refresh_hash !~ '^[0-9a-f]{64}$'
       OR p_refresh_expires_at <= NOW() THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        BTRIM(p_corp_id) || '::' || BTRIM(p_wecom_userid), 0
    ));
    SELECT * INTO v_keep FROM public.users
     WHERE id = v_actor FOR UPDATE;
    IF NOT FOUND OR v_keep.status::TEXT <> 'active' THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_PRINCIPAL_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.wecom_user_mappings
         WHERE user_id = v_actor
           AND (wecom_userid <> BTRIM(p_wecom_userid)
                OR corp_id <> BTRIM(p_corp_id))
    ) THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_ACTOR_ALREADY_BOUND'
            USING ERRCODE = '23505';
    END IF;
    SELECT * INTO v_mapping FROM public.wecom_user_mappings
     WHERE wecom_userid = BTRIM(p_wecom_userid)
       AND corp_id = BTRIM(p_corp_id)
     FOR UPDATE;
    IF v_mapping.id IS NULL THEN
        INSERT INTO public.wecom_user_mappings(
            wecom_userid, corp_id, user_id, channel,
            wecom_nickname, org_id
        ) VALUES (
            BTRIM(p_wecom_userid), BTRIM(p_corp_id), v_actor,
            'oauth', BTRIM(p_display_name), p_org_id
        );
    ELSIF v_mapping.user_id <> v_actor THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_MERGE_REVIEW_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.users SET
       login_methods = CASE
           WHEN COALESCE(login_methods, '[]'::JSONB) ? 'wecom'
               THEN login_methods
           ELSE COALESCE(login_methods, '[]'::JSONB) || '"wecom"'::JSONB
       END,
       last_login_at = NOW(),
       last_active_at = NOW()
     WHERE id = v_actor RETURNING * INTO v_keep;
    INSERT INTO public.refresh_tokens(user_id, token_hash, expires_at)
    VALUES (v_actor, p_refresh_hash, p_refresh_expires_at);
    INSERT INTO public.user_activity_events(
        user_id, org_id, event_type, source, occurred_at
    ) VALUES (
        v_actor, p_org_id, 'login_success', 'wecom', NOW()
    );
    RETURN (to_jsonb(v_keep) - 'password_hash')
        || jsonb_build_object('merged', FALSE);
END;
$$;

CREATE OR REPLACE FUNCTION unbind_web_wecom_identity(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_user public.users%ROWTYPE;
BEGIN
    PERFORM public._assert_web_wecom_oauth_scope(p_org_id, TRUE);
    SELECT * INTO v_user FROM public.users WHERE id = v_actor FOR UPDATE;
    IF NOT EXISTS (
        SELECT 1 FROM public.wecom_user_mappings WHERE user_id = v_actor
    ) THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_BINDING_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_user.phone IS NULL
       AND COALESCE(v_user.login_methods, '[]'::JSONB) = '["wecom"]'::JSONB THEN
        RAISE EXCEPTION 'WEB_WECOM_OAUTH_LAST_LOGIN_METHOD'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM public.wecom_user_mappings WHERE user_id = v_actor;
    UPDATE public.users
       SET login_methods = COALESCE((
           SELECT jsonb_agg(method)
             FROM jsonb_array_elements(login_methods) method
            WHERE method <> '"wecom"'::JSONB
       ), '[]'::JSONB)
     WHERE id = v_actor;
    RETURN jsonb_build_object('success', TRUE);
END;
$$;

CREATE OR REPLACE FUNCTION get_web_wecom_binding_status(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_web_wecom_oauth_scope(p_org_id, TRUE);
    SELECT jsonb_build_object(
        'bound', TRUE,
        'wecom_nickname', mapping.wecom_nickname,
        'bound_at', mapping.bound_at
    ) INTO v_result
      FROM public.wecom_user_mappings mapping
     WHERE mapping.user_id = public.tenant_actor_user_id()
       AND (p_org_id IS NULL OR mapping.org_id = p_org_id)
     LIMIT 1;
    RETURN COALESCE(v_result, jsonb_build_object(
        'bound', FALSE, 'wecom_nickname', NULL, 'bound_at', NULL
    ));
END;
$$;

REVOKE ALL ON FUNCTION _assert_web_wecom_oauth_scope(UUID, BOOLEAN)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION get_web_wecom_oauth_public_config(UUID)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION get_web_wecom_oauth_exchange_config(UUID)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION commit_web_wecom_login(
    TEXT, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ
) FROM PUBLIC;
REVOKE ALL ON FUNCTION bind_web_wecom_identity(
    TEXT, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ
) FROM PUBLIC;
REVOKE ALL ON FUNCTION unbind_web_wecom_identity(UUID)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION get_web_wecom_binding_status(UUID)
    FROM PUBLIC;

GRANT EXECUTE ON FUNCTION get_web_wecom_oauth_public_config(UUID),
    get_web_wecom_oauth_exchange_config(UUID),
    commit_web_wecom_login(TEXT, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ),
    bind_web_wecom_identity(TEXT, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ),
    unbind_web_wecom_identity(UUID),
    get_web_wecom_binding_status(UUID)
TO everydayai_runtime;

RESET ROLE;
