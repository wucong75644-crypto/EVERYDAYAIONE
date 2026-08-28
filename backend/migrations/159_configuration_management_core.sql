-- 159 core: Registry verification and owner-only configuration mutations.
-- Prerequisites: migration 158 and governance migrations 156-157.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION get_configuration_registry_contract()
RETURNS JSONB
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'definition_version', definition_version,
        'config_key', config_key,
        'contract_hash', contract_hash
    ) ORDER BY config_key), '[]'::JSONB)
    FROM public.configuration_definitions
    WHERE active
$$;

CREATE OR REPLACE FUNCTION _assert_platform_configuration_actor()
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR v_actor IS NULL
       OR public.tenant_org_id() IS NOT NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = v_actor
              AND status::TEXT = 'active'
              AND role::TEXT = 'super_admin'
       ) THEN
        RAISE EXCEPTION 'CONFIG_PLATFORM_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_actor;
END;
$$;

CREATE OR REPLACE FUNCTION _assert_user_configuration_actor()
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR v_actor IS NULL
       OR public.tenant_org_id() IS NOT NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = v_actor AND status::TEXT = 'active'
       ) THEN
        RAISE EXCEPTION 'CONFIG_USER_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_actor;
END;
$$;

CREATE OR REPLACE FUNCTION _validate_configuration_material(
    p_definition_version TEXT,
    p_config_key TEXT,
    p_scope_kind TEXT,
    p_value_json JSONB,
    p_secret_envelope JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_contract JSONB;
    v_value_kind TEXT;
BEGIN
    SELECT contract_json INTO v_contract
      FROM public.configuration_definitions
     WHERE definition_version = p_definition_version
       AND config_key = p_config_key
       AND active;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'CONFIG_KEY_UNKNOWN'
            USING ERRCODE = '22023';
    END IF;
    IF NOT (v_contract->'allowed_scopes' ? p_scope_kind) THEN
        RAISE EXCEPTION 'CONFIG_SCOPE_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;

    v_value_kind := v_contract->>'value_kind';
    IF v_value_kind = 'secret' THEN
        IF p_value_json IS NOT NULL
           OR jsonb_typeof(p_secret_envelope) <> 'object'
           OR NOT (p_secret_envelope ?& ARRAY[
               'payload_ciphertext', 'wrapped_dek', 'kek_version'
           ])
           OR (SELECT COUNT(*) FROM jsonb_object_keys(p_secret_envelope)) <> 3
           OR COALESCE(p_secret_envelope->>'payload_ciphertext', '') = ''
           OR LENGTH(p_secret_envelope->>'payload_ciphertext') > 1048576
           OR COALESCE(p_secret_envelope->>'wrapped_dek', '') = ''
           OR LENGTH(p_secret_envelope->>'wrapped_dek') > 16384
           OR COALESCE(p_secret_envelope->>'kek_version', '') = ''
           OR LENGTH(p_secret_envelope->>'kek_version') > 64 THEN
            RAISE EXCEPTION 'CONFIG_VALUE_INVALID'
                USING ERRCODE = '22023';
        END IF;
    ELSIF p_secret_envelope IS NOT NULL OR p_value_json IS NULL
       OR (v_value_kind = 'string' AND jsonb_typeof(p_value_json) <> 'string')
       OR (v_value_kind = 'integer' AND jsonb_typeof(p_value_json) <> 'number')
       OR (v_value_kind = 'boolean' AND jsonb_typeof(p_value_json) <> 'boolean')
       OR (v_value_kind = 'json'
           AND jsonb_typeof(p_value_json) NOT IN ('array', 'object')) THEN
        RAISE EXCEPTION 'CONFIG_VALUE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    RETURN v_contract;
END;
$$;

CREATE OR REPLACE FUNCTION _assert_configuration_key_scope(
    p_config_key TEXT,
    p_scope_kind TEXT
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.configuration_definitions
         WHERE config_key = p_config_key
           AND active
           AND contract_json->'allowed_scopes' ? p_scope_kind
    ) THEN
        RAISE EXCEPTION 'CONFIG_KEY_UNKNOWN_OR_SCOPE_FORBIDDEN'
            USING ERRCODE = '22023';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION _write_configuration_entry(
    p_scope_kind TEXT,
    p_org_id UUID,
    p_user_id UUID,
    p_definition_version TEXT,
    p_config_key TEXT,
    p_value_json JSONB,
    p_secret_envelope JSONB,
    p_expected_version BIGINT,
    p_actor UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_contract JSONB;
    v_entry public.configuration_entries%ROWTYPE;
    v_new_version BIGINT;
    v_secret_id UUID;
    v_secret_name TEXT;
BEGIN
    IF p_expected_version < 0 OR (
        (p_scope_kind = 'platform' AND (p_org_id IS NOT NULL OR p_user_id IS NOT NULL))
        OR (p_scope_kind = 'organization' AND (p_org_id IS NULL OR p_user_id IS NOT NULL))
        OR (p_scope_kind = 'user' AND (p_org_id IS NOT NULL OR p_user_id IS NULL))
        OR p_scope_kind NOT IN ('platform', 'organization', 'user')
    ) THEN
        RAISE EXCEPTION 'CONFIG_SCOPE_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    v_contract := public._validate_configuration_material(
        p_definition_version, p_config_key, p_scope_kind,
        p_value_json, p_secret_envelope
    );
    SELECT * INTO v_entry
      FROM public.configuration_entries
     WHERE scope_kind = p_scope_kind
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND user_id IS NOT DISTINCT FROM p_user_id
       AND config_key = p_config_key
     FOR UPDATE;
    IF NOT FOUND THEN
        IF p_expected_version <> 0 THEN
            RAISE EXCEPTION 'CONFIG_VERSION_CONFLICT'
                USING ERRCODE = '40001';
        END IF;
        v_new_version := 1;
    ELSE
        IF v_entry.version <> p_expected_version THEN
            RAISE EXCEPTION 'CONFIG_VERSION_CONFLICT'
                USING ERRCODE = '40001';
        END IF;
        v_new_version := v_entry.version + 1;
    END IF;

    IF v_contract->>'value_kind' = 'secret' THEN
        v_secret_name := v_contract->>'secret_name';
        UPDATE public.secret_records
           SET status = 'retired', updated_by = p_actor, updated_at = NOW()
         WHERE scope_kind = p_scope_kind
           AND org_id IS NOT DISTINCT FROM p_org_id
           AND user_id IS NOT DISTINCT FROM p_user_id
           AND secret_name = v_secret_name
           AND status = 'active';
        INSERT INTO public.secret_records(
            scope_kind, org_id, user_id, secret_name,
            payload_ciphertext, wrapped_dek, kek_version,
            payload_version, created_by, updated_by
        ) VALUES (
            p_scope_kind, p_org_id, p_user_id, v_secret_name,
            p_secret_envelope->>'payload_ciphertext',
            p_secret_envelope->>'wrapped_dek',
            p_secret_envelope->>'kek_version',
            v_new_version, p_actor, p_actor
        ) RETURNING id INTO v_secret_id;
    ELSIF v_entry.secret_id IS NOT NULL THEN
        UPDATE public.secret_records
           SET status = 'revoked', updated_by = p_actor, updated_at = NOW()
         WHERE id = v_entry.secret_id;
    END IF;

    INSERT INTO public.configuration_entries(
        scope_kind, org_id, user_id, definition_version, config_key,
        value_json, secret_id, status, version, updated_by, updated_at
    ) VALUES (
        p_scope_kind, p_org_id, p_user_id, p_definition_version, p_config_key,
        p_value_json, v_secret_id, 'active', v_new_version, p_actor, NOW()
    )
    ON CONFLICT (scope_kind, org_id, user_id, config_key) DO UPDATE SET
        definition_version = EXCLUDED.definition_version,
        value_json = EXCLUDED.value_json,
        secret_id = EXCLUDED.secret_id,
        status = 'active',
        version = EXCLUDED.version,
        updated_by = EXCLUDED.updated_by,
        updated_at = EXCLUDED.updated_at;
    RETURN jsonb_build_object(
        'key', p_config_key, 'configured', TRUE,
        'source', p_scope_kind, 'version', v_new_version
    );
EXCEPTION
    WHEN unique_violation THEN
        RAISE EXCEPTION 'CONFIG_VERSION_CONFLICT'
            USING ERRCODE = '40001';
END;
$$;

CREATE OR REPLACE FUNCTION _disable_configuration_entry(
    p_scope_kind TEXT,
    p_org_id UUID,
    p_user_id UUID,
    p_config_key TEXT,
    p_expected_version BIGINT,
    p_actor UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_entry public.configuration_entries%ROWTYPE;
    v_new_version BIGINT;
BEGIN
    PERFORM public._assert_configuration_key_scope(
        p_config_key, p_scope_kind
    );
    SELECT * INTO v_entry
      FROM public.configuration_entries
     WHERE scope_kind = p_scope_kind
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND user_id IS NOT DISTINCT FROM p_user_id
       AND config_key = p_config_key
     FOR UPDATE;
    IF NOT FOUND THEN
        IF p_expected_version <> 0 THEN
            RAISE EXCEPTION 'CONFIG_VERSION_CONFLICT'
                USING ERRCODE = '40001';
        END IF;
        RETURN jsonb_build_object(
            'key', p_config_key, 'configured', FALSE,
            'deleted', FALSE, 'version', 0
        );
    END IF;
    IF v_entry.version <> p_expected_version THEN
        RAISE EXCEPTION 'CONFIG_VERSION_CONFLICT'
            USING ERRCODE = '40001';
    END IF;
    IF v_entry.status = 'disabled' THEN
        RETURN jsonb_build_object(
            'key', p_config_key, 'configured', FALSE,
            'deleted', FALSE, 'version', v_entry.version
        );
    END IF;
    v_new_version := v_entry.version + 1;
    UPDATE public.configuration_entries
       SET status = 'disabled', version = v_new_version,
           updated_by = p_actor, updated_at = NOW()
     WHERE id = v_entry.id;
    IF v_entry.secret_id IS NOT NULL THEN
        UPDATE public.secret_records
           SET status = 'revoked', updated_by = p_actor, updated_at = NOW()
         WHERE id = v_entry.secret_id;
    END IF;
    RETURN jsonb_build_object(
        'key', p_config_key, 'configured', FALSE,
        'deleted', TRUE, 'version', v_new_version
    );
END;
$$;

CREATE OR REPLACE FUNCTION _list_configuration_status(
    p_scope_kind TEXT,
    p_org_id UUID,
    p_user_id UUID
)
RETURNS JSONB
LANGUAGE sql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'key', definition.config_key,
        'configured', entry.status = 'active',
        'source', CASE WHEN entry.status = 'active' THEN p_scope_kind END,
        'version', COALESCE(entry.version, 0),
        'updated_at', entry.updated_at
    ) ORDER BY definition.config_key), '[]'::JSONB)
    FROM public.configuration_definitions definition
    LEFT JOIN public.configuration_entries entry
      ON entry.definition_version = definition.definition_version
     AND entry.config_key = definition.config_key
     AND entry.scope_kind = p_scope_kind
     AND entry.org_id IS NOT DISTINCT FROM p_org_id
     AND entry.user_id IS NOT DISTINCT FROM p_user_id
    WHERE definition.active
      AND definition.contract_json->'allowed_scopes' ? p_scope_kind
$$;

REVOKE ALL ON FUNCTION get_configuration_registry_contract()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION get_configuration_registry_contract()
TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION _assert_platform_configuration_actor(),
    _assert_user_configuration_actor(),
    _assert_configuration_key_scope(TEXT, TEXT),
    _validate_configuration_material(TEXT, TEXT, TEXT, JSONB, JSONB),
    _write_configuration_entry(
        TEXT, UUID, UUID, TEXT, TEXT, JSONB, JSONB, BIGINT, UUID
    ),
    _disable_configuration_entry(TEXT, UUID, UUID, TEXT, BIGINT, UUID),
    _list_configuration_status(TEXT, UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

RESET ROLE;
