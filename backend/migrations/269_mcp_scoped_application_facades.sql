-- 269: Narrow application-role facades for the reviewed MCP Connector.
-- The existing everydayai_runtime MCP RPC contract remains unchanged.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_mcp_application_actor(
    p_org_id UUID,
    p_admin_required BOOLEAN DEFAULT FALSE
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_org UUID := public.tenant_org_id();
    v_role TEXT;
BEGIN
    IF session_user <> 'everydayai'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'runtime'
       OR v_actor IS NULL
       OR p_org_id IS NULL
       OR v_org IS DISTINCT FROM p_org_id
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = v_actor AND status::TEXT = 'active'
       )
       OR NOT EXISTS (
           SELECT 1 FROM public.organizations
            WHERE id = p_org_id AND status = 'active'
       ) THEN
        RAISE EXCEPTION 'MCP_CONNECTOR_SCOPE_DENIED'
            USING ERRCODE = '42501';
    END IF;

    SELECT role INTO v_role
      FROM public.org_members
     WHERE org_id = p_org_id
       AND user_id = v_actor
       AND status = 'active';
    IF NOT FOUND OR (p_admin_required AND v_role NOT IN ('owner', 'admin')) THEN
        RAISE EXCEPTION 'MCP_CONNECTOR_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_role;
END;
$$;

CREATE OR REPLACE FUNCTION api_get_mcp_test_readonly_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_org UUID := public.tenant_org_id();
BEGIN
    PERFORM public._assert_mcp_application_actor(v_org, FALSE);
    RETURN public._resolve_configuration_bundle(
        'v1', 'mcp.test_readonly', v_actor, v_org
    );
END;
$$;

CREATE OR REPLACE FUNCTION api_get_org_mcp_connector_state(
    p_org_id UUID,
    p_connector_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_state public.organization_mcp_connectors%ROWTYPE;
BEGIN
    PERFORM public._assert_mcp_application_actor(p_org_id, FALSE);
    IF p_connector_id IS DISTINCT FROM 'test-readonly' THEN
        RAISE EXCEPTION 'MCP_CONNECTOR_NOT_ALLOWLISTED'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_state
      FROM public.organization_mcp_connectors
     WHERE org_id = p_org_id AND connector_id = p_connector_id;
    RETURN jsonb_build_object(
        'org_id', p_org_id,
        'connector_id', p_connector_id,
        'enabled', COALESCE(v_state.enabled, FALSE),
        'state', CASE
            WHEN NOT COALESCE(v_state.enabled, FALSE) THEN 'disabled'
            WHEN v_state.health_status = 'ready' THEN 'ready'
            WHEN v_state.health_status = 'error' THEN 'error'
            ELSE 'configured'
        END,
        'health_status', COALESCE(v_state.health_status, 'unknown'),
        'last_checked_at', v_state.last_checked_at,
        'last_error_code', v_state.last_error_code,
        'updated_at', v_state.updated_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION api_set_org_mcp_connector_enabled(
    p_org_id UUID,
    p_connector_id TEXT,
    p_enabled BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_authority TEXT;
    v_state public.organization_mcp_connectors%ROWTYPE;
BEGIN
    v_authority := public._assert_mcp_application_actor(p_org_id, TRUE);
    IF p_connector_id IS DISTINCT FROM 'test-readonly' OR p_enabled IS NULL THEN
        RAISE EXCEPTION 'MCP_CONNECTOR_NOT_ALLOWLISTED'
            USING ERRCODE = '22023';
    END IF;
    IF p_enabled AND NOT EXISTS (
        SELECT 1
          FROM public.configuration_entries entry
          JOIN public.secret_records secret ON secret.id = entry.secret_id
         WHERE entry.scope_kind = 'organization'
           AND entry.org_id = p_org_id
           AND entry.config_key = 'mcp.test_readonly.bearer_token'
           AND entry.status = 'active'
           AND secret.scope_kind = 'organization'
           AND secret.org_id = p_org_id
           AND secret.secret_name = 'mcp.test_readonly_bearer_token'
           AND secret.status = 'active'
    ) THEN
        RAISE EXCEPTION 'MCP_CREDENTIAL_REQUIRED'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.organization_mcp_connectors(
        org_id, connector_id, enabled, health_status,
        last_error_code, updated_by, updated_at
    ) VALUES (
        p_org_id, p_connector_id, p_enabled,
        CASE WHEN p_enabled THEN 'configured' ELSE 'unknown' END,
        NULL, v_actor, NOW()
    )
    ON CONFLICT (org_id, connector_id) DO UPDATE SET
        enabled = EXCLUDED.enabled,
        health_status = CASE WHEN EXCLUDED.enabled
            THEN 'configured' ELSE organization_mcp_connectors.health_status END,
        last_error_code = CASE WHEN EXCLUDED.enabled
            THEN NULL ELSE organization_mcp_connectors.last_error_code END,
        updated_by = EXCLUDED.updated_by,
        updated_at = NOW()
    RETURNING * INTO v_state;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'mcp_connector.set_enabled',
        'mcp_connector', p_connector_id,
        jsonb_build_object('enabled', v_state.enabled)
    );
    RETURN jsonb_build_object(
        'org_id', p_org_id, 'connector_id', p_connector_id,
        'enabled', v_state.enabled,
        'state', CASE WHEN v_state.enabled THEN 'configured' ELSE 'disabled' END,
        'health_status', v_state.health_status,
        'last_checked_at', v_state.last_checked_at,
        'last_error_code', v_state.last_error_code,
        'updated_at', v_state.updated_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION api_record_org_mcp_connector_health(
    p_org_id UUID,
    p_connector_id TEXT,
    p_health_status TEXT,
    p_error_code TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_state public.organization_mcp_connectors%ROWTYPE;
BEGIN
    PERFORM public._assert_mcp_application_actor(p_org_id, FALSE);
    IF p_connector_id IS DISTINCT FROM 'test-readonly'
       OR p_health_status IS NULL
       OR p_health_status NOT IN ('ready', 'error')
       OR (p_error_code IS NOT NULL AND p_error_code NOT IN (
           'MCP_AUTH_FAILED', 'MCP_CREDENTIAL_UNAVAILABLE', 'MCP_HEALTH_FAILED',
           'MCP_REMOTE_ERROR', 'MCP_PROTOCOL_ERROR', 'MCP_RESULT_INVALID',
           'MCP_SCHEMA_NOT_REVIEWED', 'MCP_TIMEOUT', 'MCP_TOOL_ERROR',
           'MCP_UNAVAILABLE', 'MCP_CANCELLED'
       )) THEN
        RAISE EXCEPTION 'MCP_HEALTH_FACT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.organization_mcp_connectors(
        org_id, connector_id, enabled, health_status,
        last_checked_at, last_error_code, updated_by, updated_at
    ) VALUES (
        p_org_id, p_connector_id, FALSE, p_health_status,
        NOW(), p_error_code, v_actor, NOW()
    )
    ON CONFLICT (org_id, connector_id) DO UPDATE SET
        health_status = EXCLUDED.health_status,
        last_checked_at = NOW(),
        last_error_code = EXCLUDED.last_error_code,
        updated_by = EXCLUDED.updated_by,
        updated_at = NOW()
    RETURNING * INTO v_state;
    RETURN jsonb_build_object(
        'org_id', p_org_id, 'connector_id', p_connector_id,
        'enabled', v_state.enabled,
        'state', CASE
            WHEN NOT v_state.enabled THEN 'disabled'
            WHEN v_state.health_status = 'ready' THEN 'ready'
            ELSE 'error'
        END,
        'health_status', v_state.health_status,
        'last_checked_at', v_state.last_checked_at,
        'last_error_code', v_state.last_error_code,
        'updated_at', v_state.updated_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION api_get_org_mcp_connector_credential_status(
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_entry public.configuration_entries%ROWTYPE;
    v_secret public.secret_records%ROWTYPE;
    v_configured BOOLEAN := FALSE;
BEGIN
    PERFORM public._assert_mcp_application_actor(p_org_id, TRUE);
    SELECT * INTO v_entry
      FROM public.configuration_entries
     WHERE scope_kind = 'organization'
       AND org_id = p_org_id
       AND config_key = 'mcp.test_readonly.bearer_token';
    IF FOUND AND v_entry.secret_id IS NOT NULL THEN
        SELECT * INTO v_secret FROM public.secret_records
         WHERE id = v_entry.secret_id AND status = 'active';
        v_configured := FOUND AND v_entry.status = 'active';
    END IF;
    RETURN jsonb_build_object(
        'key', 'mcp.test_readonly.bearer_token',
        'configured', v_configured,
        'version', COALESCE(v_entry.version, 0),
        'source', CASE WHEN v_entry.status = 'active' THEN 'organization' END,
        'updated_at', v_entry.updated_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION api_set_org_mcp_connector_credential(
    p_org_id UUID,
    p_definition_version TEXT,
    p_config_key TEXT,
    p_value_json JSONB,
    p_secret_envelope JSONB,
    p_expected_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_authority TEXT;
    v_result JSONB;
BEGIN
    v_authority := public._assert_mcp_application_actor(p_org_id, TRUE);
    IF p_config_key IS DISTINCT FROM 'mcp.test_readonly.bearer_token'
       OR p_value_json IS NOT NULL
       OR p_secret_envelope IS NULL THEN
        RAISE EXCEPTION 'MCP_CREDENTIAL_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    v_result := public._write_configuration_entry(
        'organization', p_org_id, NULL, p_definition_version, p_config_key,
        p_value_json, p_secret_envelope, p_expected_version, v_actor
    );
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'mcp_connector.set_credential',
        'mcp_connector', 'test-readonly',
        jsonb_build_object('configured', TRUE, 'version', v_result->'version')
    );
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION api_delete_org_mcp_connector_credential(
    p_org_id UUID,
    p_config_key TEXT,
    p_expected_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_authority TEXT;
    v_result JSONB;
BEGIN
    v_authority := public._assert_mcp_application_actor(p_org_id, TRUE);
    IF p_config_key IS DISTINCT FROM 'mcp.test_readonly.bearer_token'
       OR p_expected_version < 0 THEN
        RAISE EXCEPTION 'MCP_CREDENTIAL_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    v_result := public._disable_configuration_entry(
        'organization', p_org_id, NULL, p_config_key,
        p_expected_version, v_actor
    );
    IF (v_result->>'deleted')::BOOLEAN THEN
        PERFORM public._record_governance_audit(
            p_org_id, v_authority, 'mcp_connector.delete_credential',
            'mcp_connector', 'test-readonly',
            jsonb_build_object('configured', FALSE, 'version', v_result->'version')
        );
    END IF;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION _assert_mcp_application_actor(UUID, BOOLEAN),
    api_get_mcp_test_readonly_bundle(),
    api_get_org_mcp_connector_state(UUID, TEXT),
    api_set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN),
    api_record_org_mcp_connector_health(UUID, TEXT, TEXT, TEXT),
    api_get_org_mcp_connector_credential_status(UUID),
    api_set_org_mcp_connector_credential(
        UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
    ),
    api_delete_org_mcp_connector_credential(UUID, TEXT, BIGINT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION api_get_mcp_test_readonly_bundle(),
    api_get_org_mcp_connector_state(UUID, TEXT),
    api_set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN),
    api_record_org_mcp_connector_health(UUID, TEXT, TEXT, TEXT),
    api_get_org_mcp_connector_credential_status(UUID),
    api_set_org_mcp_connector_credential(
        UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
    ),
    api_delete_org_mcp_connector_credential(UUID, TEXT, BIGINT)
TO everydayai;

RESET ROLE;
