-- 183: Sync actorless 精确企业配置 Bundle、目标发现与 ERP Token 原子轮换。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_configuration_sync_org()
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org UUID := public.tenant_org_id();
BEGIN
    IF session_user <> 'everydayai_sync'
       OR current_setting('app.access_kind', TRUE) <> 'sync'
       OR public.tenant_actor_user_id() IS NOT NULL
       OR v_org IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.organizations organization
            WHERE organization.id = v_org
              AND organization.status = 'active'
       ) THEN
        RAISE EXCEPTION 'CONFIG_SYNC_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_org;
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
    v_org UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM public._assert_configuration_worker_org();
        v_org := public.tenant_org_id();
    ELSIF session_user = 'everydayai_sync' THEN
        v_org := public._assert_configuration_sync_org();
    ELSE
        v_actor := public._assert_configuration_runtime_actor(TRUE);
        v_org := public.tenant_org_id();
    END IF;
    RETURN public._resolve_configuration_bundle(
        'v1', 'erp.runtime', v_actor, v_org
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
    v_org UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM public._assert_configuration_worker_org();
        v_org := public.tenant_org_id();
    ELSIF session_user = 'everydayai_sync' THEN
        v_org := public._assert_configuration_sync_org();
    ELSE
        v_actor := public._assert_configuration_runtime_org_admin();
        v_org := public.tenant_org_id();
    END IF;
    RETURN public._resolve_configuration_bundle(
        'v1', 'kuaimai_external.thinktank', v_actor, v_org
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
    v_org UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM public._assert_configuration_worker_org();
        v_org := public.tenant_org_id();
    ELSIF session_user = 'everydayai_sync' THEN
        v_org := public._assert_configuration_sync_org();
    ELSE
        v_actor := public._assert_configuration_runtime_org_admin();
        v_org := public.tenant_org_id();
    END IF;
    RETURN public._resolve_configuration_bundle(
        'v1', 'kuaimai_external.viperp', v_actor, v_org
    );
END;
$$;

CREATE OR REPLACE FUNCTION sync_discover_external_targets()
RETURNS TABLE (
    org_id UUID,
    source TEXT
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_sync' THEN
        RAISE EXCEPTION 'SYNC_DISCOVERY_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    WITH source_contract(source, cookie_key, company_key) AS (
        VALUES
          (
            'thinktank'::TEXT,
            'kuaimai_external.thinktank.cookie'::TEXT,
            'kuaimai_external.thinktank.company_id'::TEXT
          ),
          (
            'viperp'::TEXT,
            'kuaimai_external.viperp.cookie'::TEXT,
            'kuaimai_external.viperp.company_id'::TEXT
          )
    )
    SELECT organization.id, contract.source
      FROM public.organizations organization
      CROSS JOIN source_contract contract
     WHERE organization.status = 'active'
       AND EXISTS (
           SELECT 1 FROM public.configuration_entries entry
            WHERE entry.scope_kind = 'organization'
              AND entry.org_id = organization.id
              AND entry.config_key = contract.cookie_key
              AND entry.status = 'active'
       )
       AND EXISTS (
           SELECT 1 FROM public.configuration_entries entry
            WHERE entry.scope_kind = 'organization'
              AND entry.org_id = organization.id
              AND entry.config_key = contract.company_key
              AND entry.status = 'active'
       )
     ORDER BY organization.id, contract.source;
END;
$$;

CREATE OR REPLACE FUNCTION sync_commit_erp_token_pair(
    p_org_id UUID,
    p_secret_envelope JSONB,
    p_expected_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org UUID := public._assert_configuration_sync_org();
BEGIN
    IF p_org_id IS DISTINCT FROM v_org THEN
        RAISE EXCEPTION 'CONFIG_SYNC_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN public._write_configuration_entry(
        'organization',
        p_org_id,
        NULL,
        'v1',
        'erp.token_pair',
        NULL,
        p_secret_envelope,
        p_expected_version,
        NULL
    );
END;
$$;

CREATE OR REPLACE FUNCTION runtime_set_external_configuration(
    p_org_id UUID,
    p_source TEXT,
    p_cookie_envelope JSONB,
    p_company_id TEXT,
    p_expected_cookie_version BIGINT,
    p_expected_company_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_org_admin();
    v_org UUID := public.tenant_org_id();
    v_prefix TEXT;
    v_cookie_result JSONB;
    v_company_result JSONB;
BEGIN
    IF p_org_id IS DISTINCT FROM v_org
       OR p_source NOT IN ('thinktank', 'viperp')
       OR NULLIF(BTRIM(p_company_id), '') IS NULL THEN
        RAISE EXCEPTION 'CONFIG_SCOPE_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    v_prefix := 'kuaimai_external.' || p_source;
    v_cookie_result := public._write_configuration_entry(
        'organization', p_org_id, NULL, 'v1', v_prefix || '.cookie',
        NULL, p_cookie_envelope, p_expected_cookie_version, v_actor
    );
    v_company_result := public._write_configuration_entry(
        'organization', p_org_id, NULL, 'v1', v_prefix || '.company_id',
        to_jsonb(p_company_id), NULL, p_expected_company_version, v_actor
    );
    RETURN jsonb_build_object(
        'source', p_source,
        'cookie', v_cookie_result,
        'company_id', v_company_result
    );
END;
$$;

CREATE OR REPLACE FUNCTION runtime_delete_external_configuration(
    p_org_id UUID,
    p_source TEXT,
    p_expected_cookie_version BIGINT,
    p_expected_company_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_org_admin();
    v_org UUID := public.tenant_org_id();
    v_prefix TEXT;
BEGIN
    IF p_org_id IS DISTINCT FROM v_org
       OR p_source NOT IN ('thinktank', 'viperp') THEN
        RAISE EXCEPTION 'CONFIG_SCOPE_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    v_prefix := 'kuaimai_external.' || p_source;
    RETURN jsonb_build_object(
        'source', p_source,
        'cookie', public._disable_configuration_entry(
            'organization', p_org_id, NULL, v_prefix || '.cookie',
            p_expected_cookie_version, v_actor
        ),
        'company_id', public._disable_configuration_entry(
            'organization', p_org_id, NULL, v_prefix || '.company_id',
            p_expected_company_version, v_actor
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION _assert_configuration_sync_org(),
    sync_discover_external_targets(),
    sync_commit_erp_token_pair(UUID, JSONB, BIGINT),
    runtime_set_external_configuration(
        UUID, TEXT, JSONB, TEXT, BIGINT, BIGINT
    ),
    runtime_delete_external_configuration(UUID, TEXT, BIGINT, BIGINT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

REVOKE ALL ON FUNCTION get_erp_runtime_bundle(),
    get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

GRANT EXECUTE ON FUNCTION get_erp_runtime_bundle(),
    get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle(),
    sync_discover_external_targets(),
    sync_commit_erp_token_pair(UUID, JSONB, BIGINT)
TO everydayai_sync;

GRANT EXECUTE ON FUNCTION get_erp_runtime_bundle(),
    get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle(),
    runtime_set_external_configuration(
        UUID, TEXT, JSONB, TEXT, BIGINT, BIGINT
    ),
    runtime_delete_external_configuration(UUID, TEXT, BIGINT, BIGINT)
TO everydayai_runtime;

GRANT EXECUTE ON FUNCTION get_erp_runtime_bundle(),
    get_kuaimai_thinktank_bundle(),
    get_kuaimai_viperp_bundle()
TO everydayai_worker;

RESET ROLE;
