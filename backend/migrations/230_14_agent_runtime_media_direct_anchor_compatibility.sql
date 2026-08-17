-- 230.14: keep direct prepared media ingress compatible with the Chat Action
-- anchor guard introduced in 230.13.  The function body is patched from the
-- applied definition so 230.13 remains immutable in the migration ledger.

SET LOCAL ROLE everydayai_owner;

DO $migration$
DECLARE
    target CONSTANT regprocedure :=
        'submit_agent_runtime_chat_action_v1(uuid,uuid,uuid,text,text,text,integer,text,jsonb,text,text,text,text,text,text,integer,jsonb,jsonb,text)'::regprocedure;
    definition TEXT;
    old_fragment CONSTANT TEXT := $old$
IF p_tool_name = 'generate_image'
       AND COALESCE(pg_input_is_valid(p_task_id, 'uuid'), FALSE) THEN$old$;
    new_fragment CONSTANT TEXT := $new$
IF p_tool_name = 'generate_image'
       AND COALESCE(p_context_receipt->>'source', '') <> 'media_ingress'
       AND COALESCE(p_policy_snapshot->>'source', '') <> 'media_ingress'
       AND COALESCE(pg_input_is_valid(p_task_id, 'uuid'), FALSE) THEN$new$;
BEGIN
    SELECT pg_get_functiondef(target) INTO definition;
    IF definition IS NULL OR position(old_fragment IN definition) = 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CHAT_MEDIA_ANCHOR_FUNCTION_DRIFT'
            USING ERRCODE = '55000';
    END IF;
    EXECUTE replace(definition, old_fragment, new_fragment);
END
$migration$;

RESET ROLE;
