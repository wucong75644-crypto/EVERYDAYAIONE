-- 266: Organization activation and encrypted credentials for the fixed test MCP Connector.
-- MCP tool access remains behind the global Feature Flag and the normal ToolPolicy.

SET LOCAL ROLE everydayai_owner;

INSERT INTO configuration_definitions(
    definition_version, config_key, contract_json, contract_hash, active
) VALUES (
    'v1', 'mcp.test_readonly.bearer_token',
    '{"allowed_scopes":["organization"],"bundles":["mcp.test_readonly"],"fallback_policy":"none","key":"mcp.test_readonly.bearer_token","secret_name":"mcp.test_readonly_bearer_token","user_override":"deny","validation":{"pattern":"^[A-Za-z0-9._~+/-]+=*$","payload_fields":["token"],"required":["token"]},"value_kind":"secret"}'::JSONB,
    '881e449b8c5b17d348f8d764bd2e80374b3b01fea1cddf6baa81d487a999b99b',
    TRUE
);

INSERT INTO configuration_bundle_definitions(
    definition_version, bundle_name, contract_json, contract_hash, active
) VALUES (
    'v1', 'mcp.test_readonly',
    '{"allowed_consumers":["runtime_actor"],"name":"mcp.test_readonly","optional_keys":[],"required_keys":["mcp.test_readonly.bearer_token"]}'::JSONB,
    '36ea45684c8a5b2ad163d2a23217d9ad43f71108dfe0d68b4a590feb8300a0af',
    TRUE
);

CREATE TABLE organization_mcp_connectors (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    connector_id TEXT NOT NULL CHECK (connector_id = 'test-readonly'),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    health_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (health_status IN ('unknown', 'configured', 'ready', 'error')),
    last_checked_at TIMESTAMPTZ,
    last_error_code TEXT,
    updated_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, connector_id),
    CHECK (last_error_code IS NULL OR last_error_code IN (
        'MCP_AUTH_FAILED', 'MCP_CREDENTIAL_UNAVAILABLE', 'MCP_HEALTH_FAILED',
        'MCP_REMOTE_ERROR', 'MCP_PROTOCOL_ERROR', 'MCP_RESULT_INVALID',
        'MCP_SCHEMA_NOT_REVIEWED', 'MCP_TIMEOUT', 'MCP_TOOL_ERROR',
        'MCP_UNAVAILABLE', 'MCP_CANCELLED'
    ))
);
COMMENT ON TABLE organization_mcp_connectors IS
    'Organization enablement and token-free health facts for platform-reviewed MCP Connectors';

ALTER TABLE organization_mcp_connectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_mcp_connectors FORCE ROW LEVEL SECURITY;
CREATE POLICY organization_mcp_connectors_owner_only ON organization_mcp_connectors
    TO everydayai_owner
    USING (current_user = 'everydayai_owner')
    WITH CHECK (current_user = 'everydayai_owner');
REVOKE ALL ON TABLE organization_mcp_connectors
    FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
         everydayai_worker, everydayai_sync;

CREATE OR REPLACE FUNCTION get_mcp_test_readonly_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_actor(TRUE);
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'mcp.test_readonly', v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_org_mcp_connector_state(
    p_org_id UUID,
    p_connector_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_actor(TRUE);
    v_state public.organization_mcp_connectors%ROWTYPE;
BEGIN
    IF p_org_id IS DISTINCT FROM public.tenant_org_id()
       OR p_connector_id <> 'test-readonly' THEN
        RAISE EXCEPTION 'MCP_CONNECTOR_SCOPE_DENIED'
            USING ERRCODE = '42501';
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

CREATE OR REPLACE FUNCTION set_org_mcp_connector_enabled(
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
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
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

CREATE OR REPLACE FUNCTION record_org_mcp_connector_health(
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
    v_actor UUID := public._assert_configuration_runtime_actor(TRUE);
    v_state public.organization_mcp_connectors%ROWTYPE;
BEGIN
    IF p_org_id IS DISTINCT FROM public.tenant_org_id()
       OR p_connector_id IS DISTINCT FROM 'test-readonly'
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

REVOKE ALL ON FUNCTION get_mcp_test_readonly_bundle(),
    get_org_mcp_connector_state(UUID, TEXT),
    set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN),
    record_org_mcp_connector_health(UUID, TEXT, TEXT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION get_mcp_test_readonly_bundle(),
    get_org_mcp_connector_state(UUID, TEXT),
    set_org_mcp_connector_enabled(UUID, TEXT, BOOLEAN),
    record_org_mcp_connector_health(UUID, TEXT, TEXT, TEXT)
TO everydayai_runtime;

RESET ROLE;
