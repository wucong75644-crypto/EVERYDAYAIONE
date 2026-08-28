-- 159 facades: Narrow platform, organization, and personal configuration APIs.
-- Prerequisites: 159_configuration_management_core.sql.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION set_platform_configuration(
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
    v_actor UUID := public._assert_platform_configuration_actor();
    v_result JSONB;
BEGIN
    v_result := public._write_configuration_entry(
        'platform', NULL, NULL, p_definition_version, p_config_key,
        p_value_json, p_secret_envelope, p_expected_version, v_actor
    );
    PERFORM public._record_governance_audit(
        NULL, 'super_admin', 'platform_config.set', 'configuration',
        p_config_key, jsonb_build_object(
            'scope', 'platform', 'version', v_result->'version'
        )
    );
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION set_org_configuration(
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
    v_actor UUID := public.tenant_actor_user_id();
    v_result JSONB;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    v_result := public._write_configuration_entry(
        'organization', p_org_id, NULL, p_definition_version, p_config_key,
        p_value_json, p_secret_envelope, p_expected_version, v_actor
    );
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'organization_config.set', 'configuration',
        p_config_key, jsonb_build_object(
            'scope', 'organization', 'version', v_result->'version'
        )
    );
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION set_user_configuration(
    p_user_id UUID,
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
    v_actor UUID := public._assert_user_configuration_actor();
    v_result JSONB;
BEGIN
    IF p_user_id IS DISTINCT FROM v_actor THEN
        RAISE EXCEPTION 'CONFIG_USER_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    v_result := public._write_configuration_entry(
        'user', NULL, v_actor, p_definition_version, p_config_key,
        p_value_json, p_secret_envelope, p_expected_version, v_actor
    );
    PERFORM public._record_governance_audit(
        NULL, 'self', 'user_config.set', 'configuration',
        p_config_key, jsonb_build_object(
            'scope', 'user', 'version', v_result->'version'
        )
    );
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION delete_platform_configuration(
    p_config_key TEXT,
    p_expected_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_platform_configuration_actor();
    v_result JSONB;
BEGIN
    v_result := public._disable_configuration_entry(
        'platform', NULL, NULL, p_config_key, p_expected_version, v_actor
    );
    IF (v_result->>'deleted')::BOOLEAN THEN
        PERFORM public._record_governance_audit(
            NULL, 'super_admin', 'platform_config.delete', 'configuration',
            p_config_key, jsonb_build_object(
                'scope', 'platform', 'version', v_result->'version'
            )
        );
    END IF;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION delete_org_configuration(
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
    v_actor UUID := public.tenant_actor_user_id();
    v_result JSONB;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    v_result := public._disable_configuration_entry(
        'organization', p_org_id, NULL, p_config_key,
        p_expected_version, v_actor
    );
    IF (v_result->>'deleted')::BOOLEAN THEN
        PERFORM public._record_governance_audit(
            p_org_id, v_authority, 'organization_config.delete',
            'configuration', p_config_key, jsonb_build_object(
                'scope', 'organization', 'version', v_result->'version'
            )
        );
    END IF;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION delete_user_configuration(
    p_config_key TEXT,
    p_expected_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_user_configuration_actor();
    v_result JSONB;
BEGIN
    v_result := public._disable_configuration_entry(
        'user', NULL, v_actor, p_config_key, p_expected_version, v_actor
    );
    IF (v_result->>'deleted')::BOOLEAN THEN
        PERFORM public._record_governance_audit(
            NULL, 'self', 'user_config.delete', 'configuration',
            p_config_key, jsonb_build_object(
                'scope', 'user', 'version', v_result->'version'
            )
        );
    END IF;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_platform_configuration_status()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_platform_configuration_actor();
    RETURN public._list_configuration_status('platform', NULL, NULL);
END;
$$;

CREATE OR REPLACE FUNCTION list_org_configuration_status(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    RETURN public._list_configuration_status(
        'organization', p_org_id, NULL
    );
END;
$$;

CREATE OR REPLACE FUNCTION list_user_configuration_status()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_user_configuration_actor();
BEGIN
    RETURN public._list_configuration_status('user', NULL, v_actor);
END;
$$;

REVOKE ALL ON FUNCTION set_platform_configuration(
    TEXT, TEXT, JSONB, JSONB, BIGINT
), set_org_configuration(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
), set_user_configuration(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
), delete_platform_configuration(
    TEXT, BIGINT
), delete_org_configuration(
    UUID, TEXT, BIGINT
), delete_user_configuration(
    TEXT, BIGINT
), list_platform_configuration_status(),
    list_org_configuration_status(UUID),
    list_user_configuration_status()
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION set_platform_configuration(
    TEXT, TEXT, JSONB, JSONB, BIGINT
), set_org_configuration(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
), set_user_configuration(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
), delete_platform_configuration(
    TEXT, BIGINT
), delete_org_configuration(
    UUID, TEXT, BIGINT
), delete_user_configuration(
    TEXT, BIGINT
), list_platform_configuration_status(),
    list_org_configuration_status(UUID),
    list_user_configuration_status()
TO everydayai_runtime;

RESET ROLE;
