-- 160 facades: Fixed Bundle capabilities with exact role and Scope checks.
-- Prerequisites: 160_configuration_resolution_core.sql.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_configuration_runtime_actor(
    p_org_required BOOLEAN
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_org UUID := public.tenant_org_id();
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR v_actor IS NULL
       OR (p_org_required AND v_org IS NULL)
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = v_actor AND status::TEXT = 'active'
       )
       OR (
           v_org IS NOT NULL
           AND (
               NOT EXISTS (
                   SELECT 1 FROM public.organizations
                    WHERE id = v_org AND status = 'active'
               )
               OR NOT EXISTS (
                   SELECT 1 FROM public.org_members
                    WHERE org_id = v_org
                      AND user_id = v_actor
                      AND status = 'active'
               )
           )
       ) THEN
        RAISE EXCEPTION 'CONFIG_BUNDLE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_actor;
END;
$$;

CREATE OR REPLACE FUNCTION _assert_configuration_runtime_oauth()
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org UUID := public.tenant_org_id();
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS NOT NULL
       OR v_org IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.organizations
            WHERE id = v_org AND status = 'active'
       ) THEN
        RAISE EXCEPTION 'CONFIG_BUNDLE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION _assert_configuration_worker_org()
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org UUID := public.tenant_org_id();
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) <> 'worker'
       OR public.tenant_actor_user_id() IS NOT NULL
       OR v_org IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.organizations
            WHERE id = v_org AND status = 'active'
       ) THEN
        RAISE EXCEPTION 'CONFIG_BUNDLE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION _assert_configuration_wecom_actor()
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_org UUID := public.tenant_org_id();
BEGIN
    IF session_user <> 'everydayai_wecom_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR v_actor IS NULL
       OR v_org IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = v_actor AND status::TEXT = 'active'
       )
       OR NOT EXISTS (
           SELECT 1 FROM public.organizations
            WHERE id = v_org AND status = 'active'
       )
       OR NOT EXISTS (
           SELECT 1 FROM public.org_members
            WHERE org_id = v_org
              AND user_id = v_actor
              AND status = 'active'
       ) THEN
        RAISE EXCEPTION 'CONFIG_BUNDLE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_actor;
END;
$$;

CREATE OR REPLACE FUNCTION _assert_configuration_runtime_org_admin()
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
BEGIN
    PERFORM public._assert_governance_authority(
        public.tenant_org_id(), ARRAY['owner', 'admin'], FALSE
    );
    RETURN v_actor;
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_dashscope_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.dashscope',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_openrouter_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.openrouter',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_kie_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.kie',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_google_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.google',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_erp_runtime_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM public._assert_configuration_worker_org();
    ELSE
        v_actor := public._assert_configuration_runtime_actor(TRUE);
    END IF;
    RETURN public._resolve_configuration_bundle(
        'v1', 'erp.runtime', v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_wecom_bot_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_configuration_worker_org();
    RETURN public._resolve_configuration_bundle(
        'v1', 'wecom.bot', NULL, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_wecom_oauth_public_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_configuration_runtime_oauth();
    RETURN public._resolve_configuration_bundle(
        'v1', 'wecom.oauth.public', NULL, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_wecom_oauth_exchange_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_configuration_runtime_oauth();
    RETURN public._resolve_configuration_bundle(
        'v1', 'wecom.oauth.exchange', NULL, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_wecom_contact_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_wecom_actor();
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'wecom.contact', v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_kuaimai_thinktank_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM public._assert_configuration_worker_org();
    ELSE
        v_actor := public._assert_configuration_runtime_org_admin();
    END IF;
    RETURN public._resolve_configuration_bundle(
        'v1', 'kuaimai_external.thinktank',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_kuaimai_viperp_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM public._assert_configuration_worker_org();
    ELSE
        v_actor := public._assert_configuration_runtime_org_admin();
    END IF;
    RETURN public._resolve_configuration_bundle(
        'v1', 'kuaimai_external.viperp',
        v_actor, public.tenant_org_id()
    );
END;
$$;

REVOKE ALL ON FUNCTION _assert_configuration_runtime_actor(BOOLEAN),
    _assert_configuration_runtime_oauth(),
    _assert_configuration_worker_org(),
    _assert_configuration_wecom_actor(),
    _assert_configuration_runtime_org_admin()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION get_ai_dashscope_bundle(),
    get_ai_openrouter_bundle(), get_ai_kie_bundle(), get_ai_google_bundle(),
    get_erp_runtime_bundle(), get_wecom_bot_bundle(),
    get_wecom_oauth_public_bundle(), get_wecom_oauth_exchange_bundle(),
    get_wecom_contact_bundle(), get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION get_ai_dashscope_bundle(),
    get_ai_openrouter_bundle(), get_ai_kie_bundle(), get_ai_google_bundle(),
    get_erp_runtime_bundle(), get_wecom_oauth_public_bundle(),
    get_wecom_oauth_exchange_bundle(), get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle()
TO everydayai_runtime;

GRANT EXECUTE ON FUNCTION get_erp_runtime_bundle(),
    get_wecom_bot_bundle(), get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle()
TO everydayai_worker;

GRANT EXECUTE ON FUNCTION get_wecom_contact_bundle()
TO everydayai_wecom_runtime;

RESET ROLE;
