-- 227_50: Existing per-organization WeCom App configuration facade.

SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE
    v_bundle_rows BIGINT;
    v_reapplicable_bundle_rows BIGINT;
    v_definition_rows BIGINT;
BEGIN
    IF to_regprocedure(
        'public._assert_agent_runtime_scheduled_wecom_actor()'
    ) IS NULL
       OR to_regprocedure(
           'public._resolve_configuration_bundle(text,text,uuid,uuid)'
       ) IS NULL THEN
        RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_PREREQUISITE_MISSING'
            USING ERRCODE = '55000';
    END IF;
    BEGIN
        SELECT COUNT(*) INTO v_definition_rows
          FROM public.configuration_definitions
         WHERE definition_version = 'v1'
           AND active
           AND (
               (config_key = 'wecom.corp_id' AND contract_hash =
                '3ab214a20f2b8e096b2b19bed390b37f050b517fd63b37817e0c8760a66b351a')
               OR (config_key = 'wecom.oauth_agent_id' AND contract_hash =
                   '29c6e8bec9211b29aa69b94cafabac2a0f95fd1f921eee12b8ab343cdb5f2476')
               OR (config_key = 'wecom.oauth_agent_secret' AND contract_hash =
                   '0bcf0c906451d7f85ae319c165ab543ab0e6132e20f7b3fece2c9263ab7bf1bd')
           );
        IF v_definition_rows <> 3 THEN
            RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_DEFINITION_DRIFT'
                USING ERRCODE = '55000';
        END IF;
        SELECT COUNT(*), COUNT(*) FILTER (
                   WHERE definition_version = 'v1'
                     AND NOT active
                     AND contract_json =
                         '{"allowed_consumers":["wecom_runtime"],"name":"wecom.app","optional_keys":[],"required_keys":["wecom.corp_id","wecom.oauth_agent_id","wecom.oauth_agent_secret"]}'::JSONB
                     AND contract_hash =
                         'ec1a0cf6eb72811d5ae6762228184a63cb825603a63e8084bb916045d3465c7e'
               )
          INTO v_bundle_rows, v_reapplicable_bundle_rows
          FROM public.configuration_bundle_definitions
         WHERE bundle_name = 'wecom.app';
    EXCEPTION
        WHEN undefined_table THEN
            RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_PREREQUISITE_MISSING'
                USING ERRCODE = '55000';
    END;
    IF v_bundle_rows <> 0
       AND (v_bundle_rows <> 1 OR v_reapplicable_bundle_rows <> 1) THEN
        RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_BUNDLE_DRIFT'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DO $$
DECLARE
    v_row_count BIGINT;
BEGIN
    UPDATE public.configuration_definitions
       SET contract_json =
           '{"allowed_scopes":["organization"],"bundles":["wecom.bot","wecom.contact","wecom.callback","wecom.oauth.public","wecom.oauth.exchange","wecom.app"],"fallback_policy":"none","key":"wecom.corp_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
           contract_hash =
           'e0b444140a894c31e4c9d1440e9546bb11906b737df8dae3a54025eb1790f474'
     WHERE definition_version = 'v1'
       AND config_key = 'wecom.corp_id'
       AND active
       AND contract_hash =
           '3ab214a20f2b8e096b2b19bed390b37f050b517fd63b37817e0c8760a66b351a';
    GET DIAGNOSTICS v_row_count = ROW_COUNT;
    IF v_row_count <> 1 THEN
        RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_WRITE_CONFLICT'
            USING ERRCODE = '55000';
    END IF;

    UPDATE public.configuration_definitions
       SET contract_json =
           '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.oauth.public","wecom.app"],"fallback_policy":"none","key":"wecom.oauth_agent_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
           contract_hash =
           '8a1ac5c4a4ca6c7b8865896622697c3209819516774b8a9aa1fe716171f85066'
     WHERE definition_version = 'v1'
       AND config_key = 'wecom.oauth_agent_id'
       AND active
       AND contract_hash =
           '29c6e8bec9211b29aa69b94cafabac2a0f95fd1f921eee12b8ab343cdb5f2476';
    GET DIAGNOSTICS v_row_count = ROW_COUNT;
    IF v_row_count <> 1 THEN
        RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_WRITE_CONFLICT'
            USING ERRCODE = '55000';
    END IF;

    UPDATE public.configuration_definitions
       SET contract_json =
           '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.contact","wecom.oauth.exchange","wecom.app"],"fallback_policy":"none","key":"wecom.oauth_agent_secret","secret_name":"wecom.oauth_agent_secret","user_override":"deny","validation":{"payload_fields":["agent_secret"],"required":["agent_secret"]},"value_kind":"secret"}'::JSONB,
           contract_hash =
           '4857c5267ec0ed23391d6b1171f3bc7fc2736bbeaa4f3941125ada3d460d556f'
     WHERE definition_version = 'v1'
       AND config_key = 'wecom.oauth_agent_secret'
       AND active
       AND contract_hash =
           '0bcf0c906451d7f85ae319c165ab543ab0e6132e20f7b3fece2c9263ab7bf1bd';
    GET DIAGNOSTICS v_row_count = ROW_COUNT;
    IF v_row_count <> 1 THEN
        RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_WRITE_CONFLICT'
            USING ERRCODE = '55000';
    END IF;

    INSERT INTO public.configuration_bundle_definitions(
        definition_version, bundle_name, contract_json, contract_hash, active
    ) VALUES (
        'v1', 'wecom.app',
        '{"allowed_consumers":["wecom_runtime"],"name":"wecom.app","optional_keys":[],"required_keys":["wecom.corp_id","wecom.oauth_agent_id","wecom.oauth_agent_secret"]}'::JSONB,
        'ec1a0cf6eb72811d5ae6762228184a63cb825603a63e8084bb916045d3465c7e',
        TRUE
    ) ON CONFLICT (definition_version, bundle_name) DO UPDATE
    SET active = TRUE
    WHERE NOT configuration_bundle_definitions.active
      AND configuration_bundle_definitions.contract_json = EXCLUDED.contract_json
      AND configuration_bundle_definitions.contract_hash = EXCLUDED.contract_hash;
    GET DIAGNOSTICS v_row_count = ROW_COUNT;
    IF v_row_count <> 1 THEN
        RAISE EXCEPTION 'WECOM_APP_CONFIG_FACADE_WRITE_CONFLICT'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

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
