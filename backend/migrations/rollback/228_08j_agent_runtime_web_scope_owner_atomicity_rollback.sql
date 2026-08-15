SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION mark_prepared_task_runtime_owned(
    p_task_id UUID, p_conversation_id UUID, p_user_id UUID, p_org_id UUID,
    p_input_message_id UUID, p_output_message_id UUID, p_turn_id UUID,
    p_through_message_id UUID, p_base_context_revision TEXT,
    p_idempotency_key TEXT, p_client_task_id TEXT,
    p_session_id UUID, p_command_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_task tasks%ROWTYPE; v_session agent_runtime_sessions%ROWTYPE;
    v_command agent_session_commands%ROWTYPE; v_context JSONB;
BEGIN
    v_task := _agent_runtime_validate_task_binding(
        p_task_id, p_conversation_id, p_user_id, p_org_id,
        p_input_message_id, p_output_message_id, p_turn_id,
        p_through_message_id, p_base_context_revision,
        p_idempotency_key, p_client_task_id);
    IF p_session_id IS NULL OR p_command_id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_COMMAND_BINDING_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id AND conversation_id = p_conversation_id
       AND user_id = p_user_id AND org_id IS NOT DISTINCT FROM p_org_id;
    SELECT * INTO v_command FROM agent_session_commands
     WHERE id = p_command_id AND session_id = p_session_id
       AND command_type = 'submit_input' AND idempotency_key = p_idempotency_key
       AND org_id IS NOT DISTINCT FROM p_org_id AND user_id = p_user_id;
    IF NOT FOUND OR v_session.id IS NULL OR v_command.id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_COMMAND_BINDING_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB THEN
        IF v_task.delivery_context->>'runtime_session_id'
               IS DISTINCT FROM p_session_id::TEXT
           OR v_task.delivery_context->>'runtime_command_id'
               IS DISTINCT FROM p_command_id::TEXT THEN
            RAISE EXCEPTION 'RUNTIME_OWNER_REPLAY_BINDING_MISMATCH'
                USING ERRCODE = '55000';
        END IF;
        RETURN jsonb_build_object('outcome', 'already_runtime_owned');
    END IF;
    IF NOT (v_task.delivery_context @> '{"actor":true}'::JSONB)
       OR COALESCE((v_task.delivery_context->>'runtime')::BOOLEAN, FALSE) THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_MARK_STATE_MISMATCH'
            USING ERRCODE = '55000';
    END IF;
    v_context := v_task.delivery_context || jsonb_build_object(
        'actor', FALSE, 'runtime', TRUE,
        'runtime_session_id', p_session_id,
        'runtime_command_id', p_command_id);
    UPDATE tasks SET delivery_context = v_context WHERE id = p_task_id;
    RETURN jsonb_build_object('outcome', 'marked', 'task_id', p_task_id);
END $$;

CREATE OR REPLACE FUNCTION runtime_submit_ingress_v6_required(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,
 p_created_by_user_id UUID,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_agent_definition_hash TEXT,p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,
 p_through_message_id UUID,p_base_context_revision TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_config_snapshot JSONB,p_capability_snapshot JSONB,
 p_release_revision TEXT,p_payload JSONB,p_task_id UUID,p_client_task_id TEXT,
 p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,p_request_id TEXT
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    result JSONB;
    task tasks%ROWTYPE;
    owner_result JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_request_id IS DISTINCT FROM p_idempotency_key THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_REQUEST_ID_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    result := runtime_submit_ingress_v5(
        p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
        p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision,
        p_agent_definition_hash,p_command_type,p_idempotency_key,p_channel,
        p_through_message_id,p_base_context_revision,p_effective_toolset_revision,
        p_effective_toolset_hash,p_config_snapshot,p_capability_snapshot,
        p_release_revision,p_payload);

    IF result->>'outcome' NOT IN ('created','already_exists') THEN
        SELECT * INTO task FROM tasks WHERE id = p_task_id FOR UPDATE;
        IF task.id IS NULL THEN
            RAISE EXCEPTION 'RUNTIME_REQUIRED_TASK_MISSING'
                USING ERRCODE = '42501';
        END IF;
        IF NOT (task.delivery_context @> '{"actor":true}'::JSONB)
           OR COALESCE((task.delivery_context->>'runtime')::BOOLEAN, FALSE) THEN
            RAISE EXCEPTION 'RUNTIME_REQUIRED_TASK_OWNER_STATE_MISMATCH'
                USING ERRCODE = '55000';
        END IF;
        UPDATE tasks
           SET delivery_context = task.delivery_context || jsonb_build_object(
               'actor', FALSE, 'runtime', TRUE,
               'runtime_rejected', TRUE,
               'runtime_rejection_code',
                   COALESCE(result->>'outcome', 'unknown'))
         WHERE id = p_task_id;
        RETURN result || jsonb_build_object(
            'outcome', 'runtime_required_unavailable',
            'runtime_owned', FALSE);
    END IF;

    owner_result := mark_prepared_task_runtime_owned(
        p_task_id,p_conversation_id,p_user_id,p_org_id,p_input_message_id,
        p_output_message_id,p_turn_id,p_through_message_id,p_base_context_revision,
        p_idempotency_key,p_client_task_id,(result->>'session_id')::UUID,
        (result->>'entity_id')::UUID);
    RETURN result || owner_result || jsonb_build_object('runtime_owned', TRUE);
END $$;

ALTER TABLE conversations
    DROP CONSTRAINT conversations_user_scope_id_matches_owner_check;

UPDATE conversations AS conversation
   SET scope_id = adoption.prior_scope_id
  FROM agent_runtime_conversation_scope_adoptions AS adoption
 WHERE conversation.id = adoption.conversation_id
   AND conversation.scope_type = 'user'
   AND conversation.scope_id = adoption.adopted_scope_id;

DROP TABLE agent_runtime_conversation_scope_adoptions;

REVOKE ALL ON FUNCTION mark_prepared_task_runtime_owned(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID),
 runtime_submit_ingress_v6_required(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
FROM PUBLIC, everydayai_worker, everydayai_runtime, everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v6_required(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
TO everydayai_runtime;

RESET ROLE;
