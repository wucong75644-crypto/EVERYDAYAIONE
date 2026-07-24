-- 153: Web 认证门面、Runtime/Message 第二批 RLS 与第一段角色授权。
-- 前置：第二批 owner 已转移；154 完成前不得切换 WeCom/Backend 连接。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_web_auth_scope()
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR tenant_actor_user_id() IS NOT NULL
       OR tenant_org_id() IS NOT NULL THEN
        RAISE EXCEPTION 'WEB_AUTH_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION lookup_web_auth_candidate(
    p_phone TEXT,
    p_org_name TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_web_auth_scope();
    IF COALESCE(BTRIM(p_phone), '') = ''
       OR LENGTH(BTRIM(p_phone)) > 20
       OR (p_org_name IS NOT NULL AND COALESCE(BTRIM(p_org_name), '') = '') THEN
        RAISE EXCEPTION 'WEB_AUTH_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT jsonb_build_object(
        'id', user_row.id, 'nickname', user_row.nickname,
        'avatar_url', user_row.avatar_url, 'phone', user_row.phone,
        'password_hash', user_row.password_hash,
        'login_methods', user_row.login_methods,
        'role', user_row.role, 'credits', user_row.credits,
        'status', user_row.status, 'created_at', user_row.created_at,
        'org_id', organization.id, 'org_name', organization.name,
        'org_status', organization.status,
        'org_role', member.role, 'member_status', member.status
    ) INTO v_result
      FROM public.users user_row
      LEFT JOIN public.organizations organization
        ON p_org_name IS NOT NULL
       AND organization.name = BTRIM(p_org_name)
      LEFT JOIN public.org_members member
        ON member.org_id = organization.id
       AND member.user_id = user_row.id
     WHERE user_row.phone = BTRIM(p_phone)
       AND (p_org_name IS NULL OR member.user_id IS NOT NULL)
     LIMIT 1;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION register_web_identity(
    p_user_id UUID,
    p_phone TEXT,
    p_nickname TEXT,
    p_password_hash TEXT,
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
BEGIN
    PERFORM public._assert_web_auth_scope();
    IF p_user_id IS NULL
       OR COALESCE(BTRIM(p_phone), '') = '' OR LENGTH(BTRIM(p_phone)) > 20
       OR COALESCE(BTRIM(p_nickname), '') = '' OR LENGTH(BTRIM(p_nickname)) > 50
       OR (p_password_hash IS NOT NULL AND (
           COALESCE(BTRIM(p_password_hash), '') = ''
           OR LENGTH(p_password_hash) > 255
       ))
       OR p_refresh_hash !~ '^[0-9a-f]{64}$'
       OR p_refresh_expires_at <= NOW() THEN
        RAISE EXCEPTION 'WEB_AUTH_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(BTRIM(p_phone), 0));
    IF EXISTS (SELECT 1 FROM public.users WHERE phone = BTRIM(p_phone)) THEN
        RAISE EXCEPTION 'WEB_AUTH_PHONE_CONFLICT' USING ERRCODE = '23505';
    END IF;
    INSERT INTO public.users(
        id, phone, nickname, password_hash, login_methods,
        created_by, role, credits, status
    ) VALUES (
        p_user_id, BTRIM(p_phone), BTRIM(p_nickname),
        p_password_hash, '["phone"]'::JSONB,
        'phone'::public.user_created_by, 'user'::public.user_role,
        100, 'active'::public.account_status
    ) RETURNING * INTO v_user;
    INSERT INTO public.credits_history(
        user_id, change_amount, balance_after, change_type, description
    ) VALUES (
        v_user.id, 100, 100, 'register_gift'::public.credits_change_type,
        '新用户注册赠送积分'
    );
    INSERT INTO public.refresh_tokens(user_id, token_hash, expires_at)
    VALUES (v_user.id, p_refresh_hash, p_refresh_expires_at);
    RETURN to_jsonb(v_user) - 'password_hash';
END;
$$;

CREATE OR REPLACE FUNCTION commit_web_login(
    p_user_id UUID,
    p_org_id UUID,
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
    v_org public.organizations%ROWTYPE;
    v_member public.org_members%ROWTYPE;
BEGIN
    PERFORM public._assert_web_auth_scope();
    IF p_user_id IS NULL OR p_refresh_hash !~ '^[0-9a-f]{64}$'
       OR p_refresh_expires_at <= NOW() THEN
        RAISE EXCEPTION 'WEB_AUTH_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_user FROM public.users
     WHERE id = p_user_id FOR UPDATE;
    IF NOT FOUND OR v_user.status::TEXT <> 'active' THEN
        RAISE EXCEPTION 'WEB_AUTH_PRINCIPAL_INACTIVE' USING ERRCODE = '42501';
    END IF;
    IF p_org_id IS NOT NULL THEN
        SELECT * INTO v_org FROM public.organizations
         WHERE id = p_org_id AND status = 'active';
        SELECT * INTO v_member FROM public.org_members
         WHERE org_id = p_org_id AND user_id = p_user_id AND status = 'active';
        IF v_org.id IS NULL OR v_member.user_id IS NULL THEN
            RAISE EXCEPTION 'WEB_AUTH_PRINCIPAL_INACTIVE' USING ERRCODE = '42501';
        END IF;
    END IF;
    UPDATE public.users SET
        current_org_id = p_org_id,
        last_login_at = NOW(),
        last_active_at = NOW()
     WHERE id = p_user_id RETURNING * INTO v_user;
    INSERT INTO public.refresh_tokens(user_id, token_hash, expires_at)
    VALUES (p_user_id, p_refresh_hash, p_refresh_expires_at);
    INSERT INTO public.user_activity_events(
        user_id, org_id, event_type, source, occurred_at
    ) VALUES (
        p_user_id, p_org_id, 'login_success', 'web', NOW()
    );
    RETURN (to_jsonb(v_user) - 'password_hash') || jsonb_build_object(
        'org_id', v_org.id, 'org_name', v_org.name, 'org_role', v_member.role
    );
END;
$$;

CREATE OR REPLACE FUNCTION rotate_web_refresh_token(
    p_old_hash TEXT,
    p_new_hash TEXT,
    p_new_expires_at TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_token public.refresh_tokens%ROWTYPE;
    v_user_status TEXT;
BEGIN
    PERFORM public._assert_web_auth_scope();
    IF p_old_hash !~ '^[0-9a-f]{64}$' OR p_new_hash !~ '^[0-9a-f]{64}$'
       OR p_old_hash = p_new_hash OR p_new_expires_at <= NOW() THEN
        RAISE EXCEPTION 'WEB_AUTH_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_token FROM public.refresh_tokens
     WHERE token_hash = p_old_hash FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'invalid');
    END IF;
    IF v_token.revoked THEN
        UPDATE public.refresh_tokens SET revoked = TRUE, revoked_at = NOW()
         WHERE user_id = v_token.user_id AND revoked = FALSE;
        RETURN jsonb_build_object('outcome', 'reuse');
    END IF;
    IF v_token.expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'expired');
    END IF;
    SELECT status::TEXT INTO v_user_status FROM public.users
     WHERE id = v_token.user_id FOR UPDATE;
    IF v_user_status IS DISTINCT FROM 'active' THEN
        RETURN jsonb_build_object('outcome', 'inactive');
    END IF;
    UPDATE public.refresh_tokens SET revoked = TRUE, revoked_at = NOW()
     WHERE id = v_token.id;
    DELETE FROM public.refresh_tokens
     WHERE user_id = v_token.user_id AND expires_at <= NOW();
    INSERT INTO public.refresh_tokens(user_id, token_hash, expires_at)
    VALUES (v_token.user_id, p_new_hash, p_new_expires_at);
    RETURN jsonb_build_object('outcome', 'rotated', 'user_id', v_token.user_id);
END;
$$;

CREATE OR REPLACE FUNCTION reset_web_password(
    p_phone TEXT,
    p_password_hash TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user_id UUID;
BEGIN
    PERFORM public._assert_web_auth_scope();
    IF COALESCE(BTRIM(p_phone), '') = '' OR LENGTH(BTRIM(p_phone)) > 20
       OR COALESCE(BTRIM(p_password_hash), '') = ''
       OR LENGTH(p_password_hash) > 255 THEN
        RAISE EXCEPTION 'WEB_AUTH_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE public.users SET password_hash = p_password_hash
     WHERE phone = BTRIM(p_phone) AND status = 'active'
    RETURNING id INTO v_user_id;
    IF v_user_id IS NULL THEN
        RETURN FALSE;
    END IF;
    UPDATE public.refresh_tokens SET revoked = TRUE, revoked_at = NOW()
     WHERE user_id = v_user_id AND revoked = FALSE;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION revoke_web_refresh_token(p_token_hash TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_web_auth_scope();
    IF p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'WEB_AUTH_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE public.refresh_tokens SET revoked = TRUE, revoked_at = NOW()
     WHERE token_hash = p_token_hash AND revoked = FALSE;
    RETURN TRUE;
END;
$$;

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE wecom_user_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE wecom_chat_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE credits_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE image_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE detail_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE detail_project_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_memory_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_users ON users
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_user_fact_visible(tenant_org_id(), id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(tenant_org_id(), id));
CREATE POLICY tenant_organizations ON organizations
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR (id = tenant_org_id() AND tenant_actor_is_active_member(id)))
WITH CHECK (current_user = 'everydayai_owner' OR (id = tenant_org_id() AND owner_id = tenant_actor_user_id()));
CREATE POLICY tenant_org_members ON org_members
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR (org_id = tenant_org_id() AND tenant_actor_is_active_member(org_id)))
WITH CHECK (current_user = 'everydayai_owner' OR (org_id = tenant_org_id() AND user_id = tenant_actor_user_id()));
CREATE POLICY tenant_org_configs ON org_configs
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR (org_id = tenant_org_id() AND tenant_actor_is_active_member(org_id)))
WITH CHECK (current_user = 'everydayai_owner' OR (org_id = tenant_org_id() AND tenant_actor_is_active_member(org_id)));
CREATE POLICY tenant_conversations_runtime ON conversations
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_conversation_visible(id, org_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_messages_runtime ON messages
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_conversation_visible(conversation_id, org_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_tasks_runtime ON tasks
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_task_visible(id, org_id))
WITH CHECK (current_user = 'everydayai_owner' OR (
    tenant_user_fact_visible(org_id, user_id)
    AND tenant_conversation_visible(conversation_id, org_id)
));

CREATE POLICY tenant_credits_history ON credits_history
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_credit_transactions ON credit_transactions
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_image_generations ON image_generations
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_detail_projects ON detail_projects
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_detail_project_images ON detail_project_images
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR EXISTS (
    SELECT 1 FROM detail_projects project
     WHERE project.id = detail_project_images.project_id
       AND tenant_user_fact_visible(project.org_id, project.user_id)
))
WITH CHECK (current_user = 'everydayai_owner' OR (
    tenant_user_fact_visible(org_id, user_id)
    AND EXISTS (
        SELECT 1 FROM detail_projects project
         WHERE project.id = detail_project_images.project_id
           AND project.user_id = detail_project_images.user_id
           AND project.org_id IS NOT DISTINCT FROM detail_project_images.org_id
           AND tenant_user_fact_visible(project.org_id, project.user_id)
    )
));
CREATE POLICY tenant_user_subscriptions ON user_subscriptions
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_user_fact_visible(tenant_org_id(), user_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(tenant_org_id(), user_id));
CREATE POLICY tenant_user_memory_settings ON user_memory_settings
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id))
WITH CHECK (current_user = 'everydayai_owner' OR tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_wecom_user_mappings ON wecom_user_mappings
TO everydayai_owner USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');
CREATE POLICY tenant_wecom_chat_targets ON wecom_chat_targets
TO everydayai_owner USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');
CREATE POLICY tenant_refresh_tokens ON refresh_tokens
TO everydayai_owner USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');

GRANT SELECT, UPDATE ON users TO everydayai_runtime;
GRANT SELECT ON organizations, org_members, org_configs, credits_history
TO everydayai_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    conversations, messages, tasks, detail_projects, detail_project_images,
    user_subscriptions, user_memory_settings
TO everydayai_runtime;
GRANT SELECT, INSERT, UPDATE ON credit_transactions, image_generations
TO everydayai_runtime;

REVOKE ALL ON TABLE refresh_tokens, wecom_user_mappings, wecom_chat_targets
FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION _assert_web_auth_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION lookup_web_auth_candidate(TEXT, TEXT),
    register_web_identity(UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ),
    commit_web_login(UUID, UUID, TEXT, TIMESTAMPTZ),
    rotate_web_refresh_token(TEXT, TEXT, TIMESTAMPTZ),
    reset_web_password(TEXT, TEXT),
    revoke_web_refresh_token(TEXT)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION lookup_web_auth_candidate(TEXT, TEXT),
    register_web_identity(UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ),
    commit_web_login(UUID, UUID, TEXT, TIMESTAMPTZ),
    rotate_web_refresh_token(TEXT, TEXT, TIMESTAMPTZ),
    reset_web_password(TEXT, TEXT),
    revoke_web_refresh_token(TEXT)
TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION resolve_wecom_ingress_user(TEXT, TEXT, UUID, TEXT, TEXT),
    update_wecom_ingress_chat_address(TEXT, TEXT, TEXT, TEXT, UUID),
    upsert_wecom_ingress_chat_target(TEXT, TEXT, TEXT, UUID)
TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION tenant_actor_user_id(), tenant_org_id(),
    tenant_database_role_matches_scope(), tenant_actor_is_active_member(UUID),
    tenant_user_fact_visible(UUID, UUID), tenant_conversation_visible(UUID, UUID),
    tenant_task_visible(UUID, UUID)
TO everydayai_wecom_runtime;

RESET ROLE;
