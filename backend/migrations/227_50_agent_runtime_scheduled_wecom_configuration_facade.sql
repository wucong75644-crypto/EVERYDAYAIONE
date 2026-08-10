-- 227_50: Existing per-organization WeCom App configuration facade.

SET LOCAL ROLE everydayai_owner;

UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.bot","wecom.contact","wecom.callback","wecom.oauth.public","wecom.oauth.exchange","wecom.app"],"fallback_policy":"none","key":"wecom.corp_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       'e0b444140a894c31e4c9d1440e9546bb11906b737df8dae3a54025eb1790f474'
 WHERE definition_version = 'v1' AND config_key = 'wecom.corp_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.oauth.public","wecom.app"],"fallback_policy":"none","key":"wecom.oauth_agent_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       '8a1ac5c4a4ca6c7b8865896622697c3209819516774b8a9aa1fe716171f85066'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.contact","wecom.oauth.exchange","wecom.app"],"fallback_policy":"none","key":"wecom.oauth_agent_secret","secret_name":"wecom.oauth_agent_secret","user_override":"deny","validation":{"payload_fields":["agent_secret"],"required":["agent_secret"]},"value_kind":"secret"}'::JSONB,
       contract_hash =
       '4857c5267ec0ed23391d6b1171f3bc7fc2736bbeaa4f3941125ada3d460d556f'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_secret';

INSERT INTO configuration_bundle_definitions(
    definition_version, bundle_name, contract_json, contract_hash, active
) VALUES (
    'v1', 'wecom.app',
    '{"allowed_consumers":["wecom_runtime"],"name":"wecom.app","optional_keys":[],"required_keys":["wecom.corp_id","wecom.oauth_agent_id","wecom.oauth_agent_secret"]}'::JSONB,
    'ec1a0cf6eb72811d5ae6762228184a63cb825603a63e8084bb916045d3465c7e',
    TRUE
) ON CONFLICT (definition_version, bundle_name) DO UPDATE
SET contract_json = EXCLUDED.contract_json,
    contract_hash = EXCLUDED.contract_hash,
    active = TRUE;

CREATE OR REPLACE FUNCTION get_wecom_app_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org UUID := public.tenant_org_id();
BEGIN
    PERFORM public._assert_agent_runtime_scheduled_wecom_actor();
    IF public.tenant_actor_user_id() IS NOT NULL
       OR v_org IS NULL
       OR NOT EXISTS (
           SELECT 1
             FROM public.organizations organization
            WHERE organization.id = v_org
              AND organization.status = 'active'
       ) THEN
        RAISE EXCEPTION 'CONFIG_BUNDLE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN public._resolve_configuration_bundle(
        'v1', 'wecom.app', NULL, v_org
    );
END;
$$;

REVOKE ALL ON FUNCTION get_wecom_app_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION get_wecom_app_bundle()
TO everydayai_wecom_runtime;

RESET ROLE;
