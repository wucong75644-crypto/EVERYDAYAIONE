-- 160 core: Migration-pinned Bundle registry and owner-only resolution.
-- Prerequisites: migrations 158 and 159 core/facades.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE configuration_bundle_definitions (
    definition_version VARCHAR(32) NOT NULL,
    bundle_name VARCHAR(120) NOT NULL,
    contract_json JSONB NOT NULL,
    contract_hash VARCHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (definition_version, bundle_name),
    CONSTRAINT configuration_bundle_name_match CHECK (
        contract_json->>'name' = bundle_name
    ),
    CONSTRAINT configuration_bundle_contract_object CHECK (
        jsonb_typeof(contract_json) = 'object'
    ),
    CONSTRAINT configuration_bundle_hash_format CHECK (
        contract_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT configuration_bundle_required_array CHECK (
        jsonb_typeof(contract_json->'required_keys') = 'array'
        AND jsonb_array_length(contract_json->'required_keys') > 0
    ),
    CONSTRAINT configuration_bundle_optional_array CHECK (
        jsonb_typeof(contract_json->'optional_keys') = 'array'
    ),
    CONSTRAINT configuration_bundle_consumers_array CHECK (
        jsonb_typeof(contract_json->'allowed_consumers') = 'array'
        AND jsonb_array_length(contract_json->'allowed_consumers') > 0
    )
);

CREATE UNIQUE INDEX uq_configuration_bundle_active_name
    ON configuration_bundle_definitions(bundle_name)
    WHERE active;

COMMENT ON TABLE configuration_bundle_definitions IS
    'Migration-pinned projection of fixed code Bundle contracts';

INSERT INTO configuration_bundle_definitions(
    definition_version, bundle_name, contract_json, contract_hash, active
) VALUES
    (
        'v1', 'ai.provider.dashscope',
        '{"allowed_consumers":["runtime_actor"],"name":"ai.provider.dashscope","optional_keys":[],"required_keys":["ai.dashscope.api_key"]}'::JSONB,
        '97e91a593047468c5869b25aafa833bc710c1fb5015bb497ec995e5fd1b2ad07',
        TRUE
    ),
    (
        'v1', 'ai.provider.openrouter',
        '{"allowed_consumers":["runtime_actor"],"name":"ai.provider.openrouter","optional_keys":[],"required_keys":["ai.openrouter.api_key"]}'::JSONB,
        'f46f87c4811758c2505a567376c856dc3804e5394aa2a4b826f9a2974967f081',
        TRUE
    ),
    (
        'v1', 'ai.provider.kie',
        '{"allowed_consumers":["runtime_actor"],"name":"ai.provider.kie","optional_keys":[],"required_keys":["ai.kie.api_key"]}'::JSONB,
        '571fbf9c2ffb30bc4bcf38d007f0c6b609430659d269e7943806fb62e97175ff',
        TRUE
    ),
    (
        'v1', 'ai.provider.google',
        '{"allowed_consumers":["runtime_actor"],"name":"ai.provider.google","optional_keys":[],"required_keys":["ai.google.api_key"]}'::JSONB,
        '3e655a21356bf8cf85f559473c08d9d7d91ebfe215a802a70fe86bd5175bf998',
        TRUE
    ),
    (
        'v1', 'erp.runtime',
        '{"allowed_consumers":["runtime_org","worker_org"],"name":"erp.runtime","optional_keys":["erp.warehouse_ids"],"required_keys":["erp.app_credentials","erp.token_pair"]}'::JSONB,
        '337fa9f5965dc7c5fe6bc1b560be600c64770759911503f6b0afebfed068007b',
        TRUE
    ),
    (
        'v1', 'wecom.bot',
        '{"allowed_consumers":["worker_org"],"name":"wecom.bot","optional_keys":[],"required_keys":["wecom.corp_id","wecom.bot_credentials"]}'::JSONB,
        'df0d4e83e3707a12b089901c2dbdea9ff43e4e170e725aef699d754bb286fd75',
        TRUE
    ),
    (
        'v1', 'wecom.oauth.public',
        '{"allowed_consumers":["runtime_oauth"],"name":"wecom.oauth.public","optional_keys":[],"required_keys":["wecom.corp_id","wecom.oauth_agent_id"]}'::JSONB,
        '37e007c8f3d793040c1a110576ff4c09b320e3b3b09b76e3700b8aa56a8bf7ec',
        TRUE
    ),
    (
        'v1', 'wecom.oauth.exchange',
        '{"allowed_consumers":["runtime_oauth"],"name":"wecom.oauth.exchange","optional_keys":[],"required_keys":["wecom.corp_id","wecom.oauth_agent_secret"]}'::JSONB,
        '2ee58e73ba3b314deaedc1be44a5745d846d43cb2ce05e5ed9338c15ac592bc2',
        TRUE
    ),
    (
        'v1', 'wecom.contact',
        '{"allowed_consumers":["wecom_runtime"],"name":"wecom.contact","optional_keys":[],"required_keys":["wecom.corp_id","wecom.oauth_agent_secret"]}'::JSONB,
        'cd63de4b111ccc2124d698caf119076c4e82504b398b7fac45584dec74476168',
        TRUE
    ),
    (
        'v1', 'kuaimai_external.thinktank',
        '{"allowed_consumers":["runtime_org_admin","worker_org"],"name":"kuaimai_external.thinktank","optional_keys":[],"required_keys":["kuaimai_external.thinktank.cookie","kuaimai_external.thinktank.company_id"]}'::JSONB,
        '85be0cb61dc5dd51fbc64744763d230f79e0b836cfe9568e8dcfc3cce2f87798',
        TRUE
    ),
    (
        'v1', 'kuaimai_external.viperp',
        '{"allowed_consumers":["runtime_org_admin","worker_org"],"name":"kuaimai_external.viperp","optional_keys":[],"required_keys":["kuaimai_external.viperp.cookie","kuaimai_external.viperp.company_id"]}'::JSONB,
        'a46091d29ccbf80f50570cb5c4be7f403b31e1243af2feae23b6c563b31d1003',
        TRUE
    );

CREATE OR REPLACE FUNCTION get_configuration_bundle_registry_contract()
RETURNS JSONB
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'definition_version', definition_version,
        'bundle_name', bundle_name,
        'contract_hash', contract_hash
    ) ORDER BY bundle_name), '[]'::JSONB)
    FROM public.configuration_bundle_definitions
    WHERE active
$$;

CREATE OR REPLACE FUNCTION _configuration_scope_id(
    p_scope_kind TEXT,
    p_org_id UUID,
    p_user_id UUID
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public
AS $$
    SELECT CASE p_scope_kind
        WHEN 'organization' THEN p_org_id::TEXT
        WHEN 'user' THEN p_user_id::TEXT
        ELSE NULL
    END
$$;

CREATE OR REPLACE FUNCTION _resolve_effective_configuration_item(
    p_definition_version TEXT,
    p_config_key TEXT,
    p_required BOOLEAN,
    p_actor_user_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_definition public.configuration_definitions%ROWTYPE;
    v_policy public.configuration_policies%ROWTYPE;
    v_user_allowed BOOLEAN := FALSE;
    v_entry_id UUID;
BEGIN
    SELECT * INTO STRICT v_definition
      FROM public.configuration_definitions
     WHERE definition_version = p_definition_version
       AND config_key = p_config_key
       AND active;
    IF p_org_id IS NOT NULL THEN
        SELECT * INTO v_policy
          FROM public.configuration_policies
         WHERE org_id = p_org_id
           AND config_key = p_config_key;
    END IF;
    v_user_allowed := p_actor_user_id IS NOT NULL
        AND v_definition.contract_json->'allowed_scopes' ? 'user'
        AND (
            (p_org_id IS NULL
             AND v_definition.contract_json->>'user_override'
                 IN ('allow', 'org_policy'))
            OR
            (p_org_id IS NOT NULL
             AND COALESCE(v_policy.locked, FALSE) = FALSE
             AND (
                 v_definition.contract_json->>'user_override' = 'allow'
                 OR (
                     v_definition.contract_json->>'user_override' = 'org_policy'
                     AND COALESCE(v_policy.allow_user_override, FALSE)
                 )
             ))
        );

    SELECT candidate.id INTO v_entry_id
      FROM (
        SELECT entry.id, 1 AS priority
          FROM public.configuration_entries entry
         WHERE v_user_allowed
           AND entry.scope_kind = 'user'
           AND entry.user_id = p_actor_user_id
           AND entry.org_id IS NULL
           AND entry.config_key = p_config_key
           AND entry.definition_version = p_definition_version
           AND entry.status = 'active'
        UNION ALL
        SELECT entry.id, 2 AS priority
          FROM public.configuration_entries entry
         WHERE p_org_id IS NOT NULL
           AND v_definition.contract_json->'allowed_scopes' ? 'organization'
           AND entry.scope_kind = 'organization'
           AND entry.org_id = p_org_id
           AND entry.user_id IS NULL
           AND entry.config_key = p_config_key
           AND entry.definition_version = p_definition_version
           AND entry.status = 'active'
        UNION ALL
        SELECT entry.id, 3 AS priority
          FROM public.configuration_entries entry
         WHERE v_definition.contract_json->'allowed_scopes' ? 'platform'
           AND v_definition.contract_json->>'fallback_policy'
               IN ('platform', 'org_then_platform')
           AND entry.scope_kind = 'platform'
           AND entry.org_id IS NULL
           AND entry.user_id IS NULL
           AND entry.config_key = p_config_key
           AND entry.definition_version = p_definition_version
           AND entry.status = 'active'
      ) candidate
     ORDER BY candidate.priority
     LIMIT 1;

    IF NOT FOUND THEN
        IF p_required THEN
            RAISE EXCEPTION 'CONFIG_BUNDLE_INCOMPLETE'
                USING ERRCODE = 'P0002';
        END IF;
        RETURN jsonb_build_object(
            'key', p_config_key, 'required', FALSE, 'configured', FALSE
        );
    END IF;
    RETURN public._project_configuration_entry(
        v_entry_id,
        p_required
    );
EXCEPTION
    WHEN no_data_found THEN
        RAISE EXCEPTION 'CONFIG_REGISTRY_DRIFT'
            USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION _project_configuration_entry(
    p_entry_id UUID,
    p_required BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_entry public.configuration_entries%ROWTYPE;
    v_definition public.configuration_definitions%ROWTYPE;
    v_secret public.secret_records%ROWTYPE;
BEGIN
    SELECT * INTO STRICT v_entry
      FROM public.configuration_entries
     WHERE id = p_entry_id;
    SELECT * INTO STRICT v_definition
      FROM public.configuration_definitions
     WHERE definition_version = v_entry.definition_version
       AND config_key = v_entry.config_key
       AND active;
    IF v_definition.contract_json->>'value_kind' <> 'secret' THEN
        RETURN jsonb_build_object(
            'key', v_entry.config_key, 'required', p_required,
            'configured', TRUE, 'source', v_entry.scope_kind,
            'scope_id', public._configuration_scope_id(
                v_entry.scope_kind, v_entry.org_id, v_entry.user_id
            ),
            'version', v_entry.version, 'value_kind',
            v_definition.contract_json->>'value_kind',
            'value_json', v_entry.value_json
        );
    END IF;
    SELECT * INTO v_secret
      FROM public.secret_records
     WHERE id = v_entry.secret_id;
    IF NOT FOUND
       OR v_secret.status <> 'active'
       OR (v_secret.expires_at IS NOT NULL AND v_secret.expires_at <= NOW())
       OR v_secret.scope_kind <> v_entry.scope_kind
       OR v_secret.org_id IS DISTINCT FROM v_entry.org_id
       OR v_secret.user_id IS DISTINCT FROM v_entry.user_id
       OR v_secret.payload_version <> v_entry.version
       OR v_secret.secret_name IS DISTINCT FROM
          v_definition.contract_json->>'secret_name' THEN
        RAISE EXCEPTION 'CONFIG_SECRET_UNAVAILABLE'
            USING ERRCODE = '55000';
    END IF;
    RETURN jsonb_build_object(
        'key', v_entry.config_key, 'required', p_required,
        'configured', TRUE, 'source', v_entry.scope_kind,
        'scope_id', public._configuration_scope_id(
            v_entry.scope_kind, v_entry.org_id, v_entry.user_id
        ),
        'version', v_entry.version, 'value_kind', 'secret',
        'secret_ref', jsonb_build_object(
            'secret_name', v_secret.secret_name,
            'payload_ciphertext', v_secret.payload_ciphertext,
            'wrapped_dek', v_secret.wrapped_dek,
            'kek_version', v_secret.kek_version,
            'payload_version', v_secret.payload_version
        )
    );
EXCEPTION
    WHEN no_data_found THEN
        RAISE EXCEPTION 'CONFIG_REGISTRY_DRIFT'
            USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION _resolve_configuration_bundle(
    p_definition_version TEXT,
    p_bundle_name TEXT,
    p_actor_user_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_contract JSONB;
    v_key TEXT;
    v_items JSONB := '[]'::JSONB;
BEGIN
    SELECT contract_json INTO STRICT v_contract
      FROM public.configuration_bundle_definitions
     WHERE definition_version = p_definition_version
       AND bundle_name = p_bundle_name
       AND active;
    FOR v_key IN
        SELECT jsonb_array_elements_text(v_contract->'required_keys')
    LOOP
        v_items := v_items || jsonb_build_array(
            public._resolve_effective_configuration_item(
                p_definition_version, v_key, TRUE,
                p_actor_user_id, p_org_id
            )
        );
    END LOOP;
    FOR v_key IN
        SELECT jsonb_array_elements_text(v_contract->'optional_keys')
    LOOP
        v_items := v_items || jsonb_build_array(
            public._resolve_effective_configuration_item(
                p_definition_version, v_key, FALSE,
                p_actor_user_id, p_org_id
            )
        );
    END LOOP;
    RETURN jsonb_build_object(
        'bundle', p_bundle_name,
        'definition_version', p_definition_version,
        'items', v_items
    );
EXCEPTION
    WHEN no_data_found THEN
        RAISE EXCEPTION 'CONFIG_BUNDLE_UNKNOWN'
            USING ERRCODE = '22023';
END;
$$;

REVOKE ALL ON TABLE configuration_bundle_definitions
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION get_configuration_bundle_registry_contract()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION get_configuration_bundle_registry_contract()
TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION _configuration_scope_id(TEXT, UUID, UUID),
    _project_configuration_entry(UUID, BOOLEAN),
    _resolve_effective_configuration_item(
        TEXT, TEXT, BOOLEAN, UUID, UUID
    ),
    _resolve_configuration_bundle(TEXT, TEXT, UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

RESET ROLE;
