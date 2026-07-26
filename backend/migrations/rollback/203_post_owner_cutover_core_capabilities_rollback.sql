SET LOCAL ROLE everydayai_owner;

DROP POLICY IF EXISTS tenant_conversations_runtime ON conversations;
CREATE POLICY tenant_conversations_runtime ON conversations
TO everydayai_owner, everydayai_runtime, everydayai_worker
USING (
    current_user = 'everydayai_owner'
    OR tenant_conversation_visible(id, org_id)
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR tenant_user_fact_visible(org_id, user_id)
);

REVOKE ALL ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) FROM everydayai_runtime;
GRANT EXECUTE ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) TO everydayai;

CREATE OR REPLACE FUNCTION get_ai_dashscope_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
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
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
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
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
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
    v_actor UUID := public._assert_configuration_runtime_actor(FALSE);
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'ai.provider.google',
        v_actor, public.tenant_org_id()
    );
END;
$$;

REVOKE ALL ON FUNCTION
    get_ai_dashscope_bundle(),
    get_ai_openrouter_bundle(),
    get_ai_kie_bundle(),
    get_ai_google_bundle()
FROM everydayai_worker;
DROP FUNCTION IF EXISTS _assert_configuration_generation_actor();

DROP POLICY IF EXISTS runtime_knowledge_metrics_insert ON knowledge_metrics;
CREATE POLICY runtime_knowledge_metrics_insert ON knowledge_metrics
FOR INSERT TO everydayai_runtime
WITH CHECK (
    session_user = 'everydayai_runtime'
    AND current_setting('app.access_kind', TRUE) = 'runtime'
    AND user_id = tenant_actor_user_id()
    AND (
        (
            org_id = tenant_org_id()
            AND org_id IS NOT NULL
            AND tenant_actor_is_active_member(org_id)
        )
        OR (org_id IS NULL AND tenant_org_id() IS NULL)
    )
);
REVOKE INSERT ON knowledge_metrics FROM everydayai_worker;

RESET ROLE;
