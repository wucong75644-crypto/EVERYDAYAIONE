-- 271: Grant the MCP facade owner only the database access it needs.
-- The general configuration control plane stays behind its existing flag.

-- These protected application tables are owned by everydayai in production.
-- Let only the MCP facade owner read identity/configuration facts, write the
-- encrypted configuration envelope, and append its secret-free audit facts.
SET LOCAL ROLE everydayai;
GRANT SELECT ON TABLE public.users, public.organizations, public.org_members,
    public.configuration_definitions, public.configuration_bundle_definitions,
    public.configuration_entries, public.secret_records
TO everydayai_owner;
GRANT INSERT, UPDATE ON TABLE public.configuration_entries,
    public.secret_records
TO everydayai_owner;
GRANT INSERT ON TABLE public.governance_audit_log TO everydayai_owner;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION public.mcp_record_connector_audit(
    p_org_id UUID,
    p_authority TEXT,
    p_action TEXT,
    p_target_kind TEXT,
    p_target_key TEXT,
    p_metadata JSONB
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_audit_id UUID := gen_random_uuid();
BEGIN
    v_authority := public._assert_mcp_application_actor(p_org_id, TRUE);
    IF p_authority IS DISTINCT FROM v_authority
       OR p_action NOT IN (
           'mcp_connector.set_enabled',
           'mcp_connector.set_credential',
           'mcp_connector.delete_credential'
       )
       OR p_target_kind IS DISTINCT FROM 'mcp_connector'
       OR p_target_key IS DISTINCT FROM 'test-readonly'
       OR p_metadata IS NULL
       OR jsonb_typeof(p_metadata) <> 'object' THEN
        RAISE EXCEPTION 'MCP_CONNECTOR_AUDIT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.governance_audit_log(
        id, org_id, actor_id, authority, action, target_kind,
        target_key, request_id, metadata
    ) VALUES (
        v_audit_id, p_org_id, public.mcp_application_actor_user_id(), v_authority,
        p_action, p_target_kind, p_target_key,
        NULLIF(current_setting('app.request_id', TRUE), ''), p_metadata
    );
    RETURN v_audit_id;
END;
$$;

REVOKE ALL ON FUNCTION public.mcp_record_connector_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, JSONB
)
FROM PUBLIC, everydayai, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION public.mcp_record_connector_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, JSONB
)
TO everydayai_owner;

CREATE OR REPLACE FUNCTION public.api_set_org_mcp_connector_enabled(
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
    v_actor UUID := public.mcp_application_actor_user_id();
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
    PERFORM public.mcp_record_connector_audit(
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

CREATE OR REPLACE FUNCTION public.api_set_org_mcp_connector_credential(
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
        p_value_json, p_secret_envelope, p_expected_version,
        public.mcp_application_actor_user_id()
    );
    PERFORM public.mcp_record_connector_audit(
        p_org_id, v_authority, 'mcp_connector.set_credential',
        'mcp_connector', 'test-readonly',
        jsonb_build_object('configured', TRUE, 'version', v_result->'version')
    );
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION public.api_delete_org_mcp_connector_credential(
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
        p_expected_version, public.mcp_application_actor_user_id()
    );
    IF (v_result->>'deleted')::BOOLEAN THEN
        PERFORM public.mcp_record_connector_audit(
            p_org_id, v_authority, 'mcp_connector.delete_credential',
            'mcp_connector', 'test-readonly',
            jsonb_build_object('configured', FALSE, 'version', v_result->'version')
        );
    END IF;
    RETURN v_result;
END;
$$;

RESET ROLE;
