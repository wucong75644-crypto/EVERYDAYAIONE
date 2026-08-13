-- Revert the 227.68 media idempotency correction.
SET LOCAL ROLE everydayai_owner;

DO $rollback$
DECLARE
    definition TEXT;
BEGIN
    SELECT pg_get_functiondef(
        'submit_agent_runtime_media_action_v1(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,uuid,text,jsonb,text,text,text,text,text,text)'::regprocedure
    ) INTO definition;
    IF strpos(definition, 't.delivery_context->>''runtime'' = ''true''') > 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_227_68_ALREADY_ROLLED_BACK';
    END IF;
    definition := replace(
        definition,
        'OR p_org_id IS DISTINCT FROM p_org_id',
        'OR p_org_id IS DISTINCT FROM p_org_id OR t.delivery_context->>''runtime'' = ''true'''
    );
    EXECUTE definition;
END
$rollback$;

RESET ROLE;
