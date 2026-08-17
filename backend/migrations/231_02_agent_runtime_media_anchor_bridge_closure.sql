-- 231.02: close the direct media -> Chat Action anchor bridge.
-- Direct media ingress must preserve both original message anchors before
-- prepared-media scope validation and KIE submission.

SET LOCAL ROLE everydayai_owner;

DO $migration$
DECLARE
    target CONSTANT regprocedure :=
        'submit_agent_runtime_chat_action_v1(uuid,uuid,uuid,text,text,text,integer,text,jsonb,text,text,text,text,text,text,integer,jsonb,jsonb,text)'::regprocedure;
    definition TEXT;
    old_fragment CONSTANT TEXT := $old$
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF NULLIF(BTRIM(p_tool_name), '') IS NULL$old$;
    new_fragment CONSTANT TEXT := $new$
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_tool_name = 'generate_image'
       AND p_context_receipt->>'source' = 'media_ingress'
       AND COALESCE(pg_input_is_valid(
           p_context_receipt->>'input_message_id', 'uuid'), FALSE) THEN
        input_message_id := (p_context_receipt->>'input_message_id')::UUID;
    END IF;
    IF NULLIF(BTRIM(p_tool_name), '') IS NULL$new$;
BEGIN
    SELECT pg_get_functiondef(target) INTO definition;
    IF definition IS NULL OR position(old_fragment IN definition) = 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CHAT_INPUT_BRIDGE_FUNCTION_DRIFT'
            USING ERRCODE = '55000';
    END IF;
    EXECUTE replace(definition, old_fragment, new_fragment);
END
$migration$;

DO $migration$
DECLARE
    target CONSTANT regprocedure :=
        'submit_agent_runtime_media_action_v1(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,uuid,text,jsonb,text,text,text,text,text,text)'::regprocedure;
    definition TEXT;
    old_fragment CONSTANT TEXT := $old$
        p_conversation_id,p_org_id,p_user_id,p_task_id::TEXT,
        p_input_message_id::TEXT,p_task_id::TEXT,1,p_tool_name,safe_arguments,$old$;
    new_fragment CONSTANT TEXT := $new$
        p_conversation_id,p_org_id,p_user_id,p_task_id::TEXT,
        p_output_message_id::TEXT,p_task_id::TEXT,1,p_tool_name,safe_arguments,$new$;
BEGIN
    SELECT pg_get_functiondef(target) INTO definition;
    IF definition IS NULL OR position(old_fragment IN definition) = 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_OUTPUT_BRIDGE_FUNCTION_DRIFT'
            USING ERRCODE = '55000';
    END IF;
    EXECUTE replace(definition, old_fragment, new_fragment);
END
$migration$;

RESET ROLE;
