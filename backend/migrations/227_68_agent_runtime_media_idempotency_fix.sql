-- 227.68: allow Runtime media idempotency replay after ownership marking.
SET LOCAL ROLE everydayai_owner;

DO $fix$
DECLARE
    definition TEXT;
BEGIN
    SELECT pg_get_functiondef(
        'submit_agent_runtime_media_action_v1(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,uuid,text,jsonb,text,text,text,text,text,text)'::regprocedure
    ) INTO definition;
    IF strpos(definition, 't.delivery_context->>''runtime'' = ''true''') = 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_227_68_UNEXPECTED_MEDIA_DEFINITION';
    END IF;
    definition := replace(
        definition,
        ' OR t.delivery_context->>''runtime'' = ''true''',
        ''
    );
    EXECUTE definition;
END
$fix$;

RESET ROLE;
