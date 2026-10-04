SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE
    v_signature TEXT;
    v_definition TEXT;
BEGIN
    FOREACH v_signature IN ARRAY ARRAY[
        '_assert_mcp_application_actor(uuid,boolean)',
        'api_get_mcp_test_readonly_bundle()',
        'api_set_org_mcp_connector_enabled(uuid,text,boolean)',
        'api_record_org_mcp_connector_health(uuid,text,text,text)',
        'api_set_org_mcp_connector_credential(uuid,text,text,jsonb,jsonb,bigint)',
        'api_delete_org_mcp_connector_credential(uuid,text,bigint)'
    ] LOOP
        v_definition := pg_get_functiondef(
            to_regprocedure('public.' || v_signature)
        );
        v_definition := replace(
            v_definition,
            'public.mcp_application_actor_user_id()',
            'public.tenant_actor_user_id()'
        );
        v_definition := replace(
            v_definition,
            'public.mcp_application_org_id()',
            'public.tenant_org_id()'
        );
        EXECUTE v_definition;
    END LOOP;
END;
$$;

DROP FUNCTION IF EXISTS mcp_application_actor_user_id();
DROP FUNCTION IF EXISTS mcp_application_org_id();

RESET ROLE;
