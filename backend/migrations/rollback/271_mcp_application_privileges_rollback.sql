SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE
    v_signature TEXT;
    v_definition TEXT;
BEGIN
    FOREACH v_signature IN ARRAY ARRAY[
        'api_set_org_mcp_connector_enabled(uuid,text,boolean)',
        'api_set_org_mcp_connector_credential(uuid,text,text,jsonb,jsonb,bigint)',
        'api_delete_org_mcp_connector_credential(uuid,text,bigint)'
    ] LOOP
        v_definition := pg_get_functiondef(
            to_regprocedure('public.' || v_signature)
        );
        v_definition := replace(
            v_definition,
            'public.mcp_record_connector_audit(',
            'public._record_governance_audit('
        );
        EXECUTE v_definition;
    END LOOP;
END;
$$;

DROP FUNCTION IF EXISTS public.mcp_record_connector_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, JSONB
);

RESET ROLE;
SET LOCAL ROLE everydayai;
REVOKE INSERT, UPDATE ON TABLE public.configuration_entries,
    public.secret_records
FROM everydayai_owner;
REVOKE SELECT ON TABLE public.users, public.organizations, public.org_members,
    public.configuration_definitions, public.configuration_bundle_definitions,
    public.configuration_entries, public.secret_records
FROM everydayai_owner;
REVOKE INSERT ON TABLE public.governance_audit_log FROM everydayai_owner;
RESET ROLE;
