-- 227.67: repair the frozen 227.63 function without changing its identity.
-- The original function omitted p_catalog_revision from agent_model_steps.
SET LOCAL ROLE everydayai_owner;

DO $fix$
DECLARE
    definition TEXT;
BEGIN
    SELECT pg_get_functiondef(
        'submit_agent_runtime_chat_action_v1(uuid,uuid,uuid,text,text,text,integer,text,jsonb,text,text,text,text,text,text,integer,jsonb,jsonb,text)'::regprocedure
    ) INTO definition;
    IF strpos(definition, 'jsonb_build_object(''message_id'', p_message_id, ''turn'', p_turn),') = 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_227_67_UNEXPECTED_CHAT_ACTION_DEFINITION';
    END IF;
    definition := replace(
        definition,
        'jsonb_build_object(''message_id'', p_message_id, ''turn'', p_turn),',
        'p_catalog_revision, jsonb_build_object(''message_id'', p_message_id, ''turn'', p_turn),'
    );
    EXECUTE definition;
END
$fix$;

RESET ROLE;
