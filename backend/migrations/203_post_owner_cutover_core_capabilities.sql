-- 203: Restore narrow Web/Actor capabilities after legacy owner inheritance removal.

SET LOCAL ROLE everydayai_owner;

DROP POLICY IF EXISTS tenant_conversations_runtime ON conversations;
CREATE POLICY tenant_conversations_runtime ON conversations
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (
    current_user = 'everydayai_owner'
    OR (
        tenant_database_role_matches_scope()
        AND (
            (
                scope_type = 'user'
                AND tenant_user_fact_visible(org_id, user_id)
            )
            OR (
                scope_type = 'channel'
                AND org_id = tenant_org_id()
                AND tenant_actor_is_active_member(org_id)
            )
        )
    )
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR (
        scope_type = 'user'
        AND tenant_user_fact_visible(org_id, user_id)
    )
);

REVOKE ALL ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) TO everydayai_runtime;

CREATE OR REPLACE FUNCTION _assert_configuration_generation_actor()
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_org UUID := public.tenant_org_id();
    v_role_allowed BOOLEAN := (
        (
            session_user = 'everydayai_runtime'
            AND current_setting('app.access_kind', TRUE) = 'runtime'
        )
        OR (
            session_user = 'everydayai_worker'
            AND current_setting('app.access_kind', TRUE) = 'worker'
        )
    );
BEGIN
    IF NOT v_role_allowed
       OR v_actor IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = v_actor AND status::TEXT = 'active'
       )
       OR (
           v_org IS NOT NULL
           AND (
               NOT EXISTS (
                   SELECT 1 FROM public.organizations
                    WHERE id = v_org AND status = 'active'
               )
               OR NOT EXISTS (
                   SELECT 1 FROM public.org_members
                    WHERE org_id = v_org
                      AND user_id = v_actor
                      AND status = 'active'
               )
           )
       ) THEN
        RAISE EXCEPTION 'CONFIG_BUNDLE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_actor;
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_dashscope_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_generation_actor();
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.dashscope',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_openrouter_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_generation_actor();
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.openrouter',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_kie_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_generation_actor();
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.kie',
        v_actor, public.tenant_org_id()
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_ai_google_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_generation_actor();
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.google',
        v_actor, public.tenant_org_id()
    );
END;
$$;

REVOKE ALL ON FUNCTION _assert_configuration_generation_actor()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION
    get_ai_dashscope_bundle(),
    get_ai_openrouter_bundle(),
    get_ai_kie_bundle(),
    get_ai_google_bundle()
FROM everydayai_worker;
GRANT EXECUTE ON FUNCTION
    get_ai_dashscope_bundle(),
    get_ai_openrouter_bundle(),
    get_ai_kie_bundle(),
    get_ai_google_bundle()
TO everydayai_worker;

DROP POLICY IF EXISTS runtime_knowledge_metrics_insert ON knowledge_metrics;
CREATE POLICY runtime_knowledge_metrics_insert ON knowledge_metrics
FOR INSERT TO everydayai_runtime, everydayai_worker
WITH CHECK (tenant_user_fact_visible(org_id, user_id));
GRANT INSERT ON knowledge_metrics TO everydayai_worker;

RESET ROLE;
