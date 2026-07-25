-- 166: Secret-free WeCom workload discovery for the actorless Worker.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_wecom_worker_discovery_scope()
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE)
           IS DISTINCT FROM 'worker'
       OR NULLIF(
           current_setting('app.actor_user_id', TRUE), ''
       ) IS NOT NULL
       OR NULLIF(
           current_setting('app.org_id', TRUE), ''
       ) IS NOT NULL
       OR NULLIF(
           current_setting('app.request_id', TRUE), ''
       ) IS NULL THEN
        RAISE EXCEPTION 'WECOM_WORKER_DISCOVERY_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION discover_wecom_bot_targets()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_targets JSONB;
BEGIN
    PERFORM public._assert_wecom_worker_discovery_scope();
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'org_id', organization.id,
                'credential_version', bot_entry.version
            )
            ORDER BY organization.id
        ),
        '[]'::JSONB
    )
      INTO v_targets
      FROM public.organizations organization
      JOIN public.configuration_entries corp_entry
        ON corp_entry.scope_kind = 'organization'
       AND corp_entry.org_id = organization.id
       AND corp_entry.user_id IS NULL
       AND corp_entry.definition_version = 'v1'
       AND corp_entry.config_key = 'wecom.corp_id'
       AND corp_entry.status = 'active'
       AND corp_entry.value_json IS NOT NULL
      JOIN public.configuration_entries bot_entry
        ON bot_entry.scope_kind = 'organization'
       AND bot_entry.org_id = organization.id
       AND bot_entry.user_id IS NULL
       AND bot_entry.definition_version = 'v1'
       AND bot_entry.config_key = 'wecom.bot_credentials'
       AND bot_entry.status = 'active'
      JOIN public.secret_records bot_secret
        ON bot_secret.id = bot_entry.secret_id
       AND bot_secret.scope_kind = bot_entry.scope_kind
       AND bot_secret.org_id = bot_entry.org_id
       AND bot_secret.user_id IS NOT DISTINCT FROM bot_entry.user_id
       AND bot_secret.secret_name = 'wecom.bot_credentials'
       AND bot_secret.payload_version = bot_entry.version
       AND bot_secret.status = 'active'
       AND (
           bot_secret.expires_at IS NULL
           OR bot_secret.expires_at > NOW()
       )
     WHERE organization.status = 'active';
    RETURN v_targets;
END;
$$;

REVOKE ALL ON FUNCTION _assert_wecom_worker_discovery_scope()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION discover_wecom_bot_targets()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION discover_wecom_bot_targets()
TO everydayai_worker;

RESET ROLE;
