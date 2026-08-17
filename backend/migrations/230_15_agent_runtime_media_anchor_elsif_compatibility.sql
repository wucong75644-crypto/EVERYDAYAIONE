-- 230.15: complete the 230.14 media-ingress exception guard.
-- 230.14 skipped the Chat Action anchor lookup for direct media ingress, but
-- its fallback ELSIF still rejected every generate_image request.

SET LOCAL ROLE everydayai_owner;

DO $migration$
DECLARE
    target CONSTANT regprocedure :=
        'submit_agent_runtime_chat_action_v1(uuid,uuid,uuid,text,text,text,integer,text,jsonb,text,text,text,text,text,text,integer,jsonb,jsonb,text)'::regprocedure;
    definition TEXT;
    old_fragment CONSTANT TEXT := $old$
    ELSIF p_tool_name = 'generate_image' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CHAT_MEDIA_INPUT_ANCHOR_INVALID'
            USING ERRCODE = '42501';$old$;
    new_fragment CONSTANT TEXT := $new$
    ELSIF p_tool_name = 'generate_image'
       AND COALESCE(p_context_receipt->>'source', '') <> 'media_ingress'
       AND COALESCE(p_policy_snapshot->>'source', '') <> 'media_ingress' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CHAT_MEDIA_INPUT_ANCHOR_INVALID'
            USING ERRCODE = '42501';$new$;
BEGIN
    SELECT pg_get_functiondef(target) INTO definition;
    IF definition IS NULL OR position(old_fragment IN definition) = 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CHAT_MEDIA_ELSIF_FUNCTION_DRIFT'
            USING ERRCODE = '55000';
    END IF;
    EXECUTE replace(definition, old_fragment, new_fragment);
END
$migration$;

RESET ROLE;
