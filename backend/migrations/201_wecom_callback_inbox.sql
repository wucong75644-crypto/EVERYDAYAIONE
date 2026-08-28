-- 201: Per-organization WeCom callback credentials and durable inbox.

SET LOCAL ROLE everydayai_owner;

UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.bot","wecom.contact","wecom.callback","wecom.oauth.public","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.corp_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       '3ab214a20f2b8e096b2b19bed390b37f050b517fd63b37817e0c8760a66b351a'
 WHERE definition_version = 'v1' AND config_key = 'wecom.corp_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.oauth.public"],"fallback_policy":"none","key":"wecom.oauth_agent_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       '29c6e8bec9211b29aa69b94cafabac2a0f95fd1f921eee12b8ab343cdb5f2476'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.contact","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.oauth_agent_secret","secret_name":"wecom.oauth_agent_secret","user_override":"deny","validation":{"payload_fields":["agent_secret"],"required":["agent_secret"]},"value_kind":"secret"}'::JSONB,
       contract_hash =
       '0bcf0c906451d7f85ae319c165ab543ab0e6132e20f7b3fece2c9263ab7bf1bd'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_secret';
INSERT INTO configuration_definitions(
    definition_version, config_key, contract_json, contract_hash, active
) VALUES (
    'v1', 'wecom.callback_credentials',
    '{"allowed_scopes":["organization"],"bundles":["wecom.callback"],"fallback_policy":"none","key":"wecom.callback_credentials","secret_name":"wecom.callback_credentials","user_override":"deny","validation":{"payload_fields":["token","encoding_aes_key"],"required":["token","encoding_aes_key"]},"value_kind":"secret"}'::JSONB,
    'f2d061d496f359f4d959436494f91f3a4b450631eb96b92d363bd9850169ac22',
    TRUE
) ON CONFLICT (definition_version, config_key) DO UPDATE
SET contract_json = EXCLUDED.contract_json,
    contract_hash = EXCLUDED.contract_hash,
    active = TRUE;
INSERT INTO configuration_bundle_definitions(
    definition_version, bundle_name, contract_json, contract_hash, active
) VALUES (
    'v1', 'wecom.callback',
    '{"allowed_consumers":["worker_org"],"name":"wecom.callback","optional_keys":[],"required_keys":["wecom.corp_id","wecom.callback_credentials","wecom.oauth_agent_id","wecom.oauth_agent_secret"]}'::JSONB,
    '65a8ef6cfc975299c0d6255b028b8fa771c92e35e3ec8762296d3a79c4c8fa1c',
    TRUE
) ON CONFLICT (definition_version, bundle_name) DO UPDATE
SET contract_json = EXCLUDED.contract_json,
    contract_hash = EXCLUDED.contract_hash,
    active = TRUE;

CREATE OR REPLACE FUNCTION get_wecom_callback_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public._assert_configuration_worker_org();
    RETURN public._resolve_configuration_bundle(
        'v1', 'wecom.callback', NULL, public.tenant_org_id()
    );
END;
$$;

CREATE TABLE wecom_callback_inbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    corp_id VARCHAR(100) NOT NULL,
    message_key VARCHAR(160) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (org_id, message_key),
    CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX idx_wecom_callback_inbox_claim
    ON wecom_callback_inbox(available_at, created_at)
    WHERE status IN ('pending', 'processing');
ALTER TABLE wecom_callback_inbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY wecom_callback_inbox_owner_all ON wecom_callback_inbox
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE wecom_callback_inbox FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION enqueue_wecom_callback(
    p_org_id UUID,
    p_corp_id TEXT,
    p_message_key TEXT,
    p_payload JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_id UUID;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS NOT NULL
       OR public.tenant_org_id() IS NOT NULL THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_RUNTIME_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_org_id IS NULL OR NULLIF(BTRIM(p_corp_id), '') IS NULL
       OR NULLIF(BTRIM(p_message_key), '') IS NULL
       OR length(BTRIM(p_message_key)) > 160
       OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.organizations organization
         WHERE organization.id = p_org_id
           AND organization.status = 'active'
           AND BTRIM(organization.wecom_corp_id) = BTRIM(p_corp_id)
    ) THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_ORG_CORP_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.wecom_callback_inbox(
        org_id, corp_id, message_key, payload
    ) VALUES (
        p_org_id, BTRIM(p_corp_id), BTRIM(p_message_key), p_payload
    )
    ON CONFLICT (org_id, message_key) DO NOTHING
    RETURNING id INTO v_id;
    RETURN jsonb_build_object(
        'outcome', CASE WHEN v_id IS NULL THEN 'duplicate' ELSE 'enqueued' END,
        'id', v_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION claim_wecom_callback(p_lease_seconds INTEGER)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_item public.wecom_callback_inbox%ROWTYPE;
    v_token UUID := gen_random_uuid();
BEGIN
    IF session_user <> 'everydayai_wecom_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS NOT NULL
       OR public.tenant_org_id() IS NOT NULL
       OR p_lease_seconds NOT BETWEEN 10 AND 300 THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_CLAIM_DENIED'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_item
      FROM public.wecom_callback_inbox
     WHERE available_at <= NOW()
       AND (
           status = 'pending'
           OR (status = 'processing' AND lease_expires_at <= NOW())
       )
       AND attempts < 8
     ORDER BY available_at, created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    UPDATE public.wecom_callback_inbox
       SET status = 'processing',
           attempts = attempts + 1,
           lease_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           updated_at = NOW()
     WHERE id = v_item.id;
    RETURN (to_jsonb(v_item) - 'lease_token') || jsonb_build_object(
        'lease_token', v_token,
        'attempts', v_item.attempts + 1
    );
END;
$$;

CREATE OR REPLACE FUNCTION complete_wecom_callback(
    p_id UUID,
    p_lease_token UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_wecom_runtime' THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_COMPLETE_DENIED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.wecom_callback_inbox
       SET status = 'completed', completed_at = NOW(), updated_at = NOW(),
           lease_token = NULL, lease_expires_at = NULL, last_error = NULL
     WHERE id = p_id AND status = 'processing'
       AND lease_token = p_lease_token AND lease_expires_at > NOW();
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION fail_wecom_callback(
    p_id UUID,
    p_lease_token UUID,
    p_error TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_wecom_runtime' THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_FAIL_DENIED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.wecom_callback_inbox
       SET status = CASE WHEN attempts >= 8 THEN 'failed' ELSE 'pending' END,
           available_at = NOW() + make_interval(
               secs => LEAST(300, 5 * (2 ^ LEAST(attempts, 6))::INTEGER)
           ),
           updated_at = NOW(), lease_token = NULL, lease_expires_at = NULL,
           last_error = LEFT(COALESCE(p_error, 'unknown'), 1000)
     WHERE id = p_id AND status = 'processing'
       AND lease_token = p_lease_token;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION cleanup_wecom_callback_inbox(
    p_retention_days INTEGER
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted INTEGER;
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) <> 'worker'
       OR public.tenant_actor_user_id() IS NOT NULL
       OR public.tenant_org_id() IS NOT NULL
       OR p_retention_days NOT BETWEEN 7 AND 365 THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_CLEANUP_DENIED'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM public.wecom_callback_inbox
     WHERE status IN ('completed', 'failed')
       AND updated_at < NOW() - make_interval(days => p_retention_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

REVOKE ALL ON TABLE wecom_callback_inbox
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
REVOKE ALL ON FUNCTION get_wecom_callback_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION get_wecom_callback_bundle()
TO everydayai_worker;
REVOKE ALL ON FUNCTION
    enqueue_wecom_callback(UUID, TEXT, TEXT, JSONB),
    claim_wecom_callback(INTEGER),
    complete_wecom_callback(UUID, UUID),
    fail_wecom_callback(UUID, UUID, TEXT),
    cleanup_wecom_callback_inbox(INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION enqueue_wecom_callback(UUID, TEXT, TEXT, JSONB)
TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION
    claim_wecom_callback(INTEGER),
    complete_wecom_callback(UUID, UUID),
    fail_wecom_callback(UUID, UUID, TEXT)
TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION cleanup_wecom_callback_inbox(INTEGER)
TO everydayai_worker;

RESET ROLE;
