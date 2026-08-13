-- Revert the 227.67 function-body correction; migration 227.63 remains untouched.
SET LOCAL ROLE everydayai_owner;

DO $rollback$
DECLARE
    definition TEXT;
BEGIN
    SELECT pg_get_functiondef(
        'submit_agent_runtime_chat_action_v1(uuid,uuid,uuid,text,text,text,integer,text,jsonb,text,text,text,text,text,text,integer,jsonb,jsonb,text)'::regprocedure
    ) INTO definition;
    IF strpos(definition, 'p_catalog_revision, jsonb_build_object(''message_id'', p_message_id, ''turn'', p_turn),') = 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_227_67_ROLLBACK_UNEXPECTED_CHAT_ACTION_DEFINITION';
    END IF;
    definition := replace(
        definition,
        'p_catalog_revision, jsonb_build_object(''message_id'', p_message_id, ''turn'', p_turn),',
        'jsonb_build_object(''message_id'', p_message_id, ''turn'', p_turn),'
    );
    EXECUTE definition;
END
$rollback$;

RESET ROLE;
