-- 227.65: create prepared media work as a Runtime-owned Action.
-- Additive only. Existing task and Runtime tables remain the source of truth.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION submit_agent_runtime_media_action_v1(
    p_conversation_id UUID, p_org_id UUID, p_user_id UUID,
    p_scope_kind TEXT, p_scope_id TEXT, p_created_by_user_id UUID,
    p_agent_definition_id TEXT, p_agent_definition_revision TEXT,
    p_task_id UUID, p_input_message_id UUID, p_output_message_id UUID,
    p_turn_id UUID, p_tool_name TEXT, p_arguments JSONB, p_model_id TEXT,
    p_model_provider TEXT, p_model_revision TEXT, p_catalog_revision TEXT,
    p_policy_revision TEXT, p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    t tasks%ROWTYPE;
    s JSONB;
    r JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_tool_name NOT IN ('generate_image', 'generate_video')
       OR jsonb_typeof(p_arguments) IS DISTINCT FROM 'object'
       OR NULLIF(BTRIM(p_idempotency_key), '') IS NULL
       OR NULLIF(BTRIM(p_task_id::TEXT), '') IS NULL
       OR p_input_message_id IS NULL OR p_output_message_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO t FROM tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND OR t.conversation_id IS DISTINCT FROM p_conversation_id
       OR t.user_id IS DISTINCT FROM p_user_id
       OR t.org_id IS DISTINCT FROM p_org_id
       OR t.delivery_context->>'runtime' = 'true' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TASK_SCOPE_MISMATCH' USING ERRCODE='42501';
    END IF;
    s := ensure_agent_runtime_session(
        p_conversation_id, p_org_id, p_user_id, p_scope_kind, p_scope_id,
        p_created_by_user_id, p_agent_definition_id, p_agent_definition_revision
    );
    IF s->>'outcome' NOT IN ('created', 'already_exists') THEN
        RETURN s || jsonb_build_object('runtime_owned', FALSE);
    END IF;
    r := submit_agent_runtime_chat_action_v1(
        p_conversation_id, p_org_id, p_user_id, p_task_id::TEXT,
        p_input_message_id::TEXT, p_task_id::TEXT, 1, p_tool_name,
        p_arguments, p_model_id, p_model_provider, p_model_revision,
        p_catalog_revision, p_policy_revision,
        'runtime_media_generation:' || p_tool_name, 1,
        jsonb_build_object('source','media_ingress','task_id',p_task_id,
                           'output_message_id',p_output_message_id,
                           'turn_id',p_turn_id),
        jsonb_build_object('source','media_ingress','task_id',p_task_id,
                           'input_message_id',p_input_message_id,
                           'output_message_id',p_output_message_id,
                           'turn_id',p_turn_id), p_idempotency_key);
    IF r->>'outcome' IN ('created','already_exists') THEN
        UPDATE tasks SET delivery_context = delivery_context || jsonb_build_object(
            'actor', FALSE, 'runtime', TRUE, 'runtime_action_id', r->>'action_id',
            'runtime_run_id', r->>'run_id') WHERE id=p_task_id;
        RETURN r || jsonb_build_object('runtime_owned', TRUE);
    END IF;
    RETURN r || jsonb_build_object('runtime_owned', FALSE);
END;
$$;

REVOKE ALL ON FUNCTION submit_agent_runtime_media_action_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID, UUID,
    TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT
) FROM PUBLIC, everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION submit_agent_runtime_media_action_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID, UUID,
    TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT
) TO everydayai_runtime, everydayai_wecom_runtime;

RESET ROLE;
