-- 227.14: additive owner transition for Runtime ingress compatibility.
-- 227.01 through 227.13 remain immutable.  This lane does not enable a
-- worker, provider binding, catalog entry, or production cutover.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_validate_task_binding(
    p_task_id UUID, p_conversation_id UUID, p_user_id UUID, p_org_id UUID,
    p_input_message_id UUID, p_output_message_id UUID, p_turn_id UUID,
    p_through_message_id UUID, p_base_context_revision TEXT,
    p_idempotency_key TEXT, p_client_task_id TEXT
) RETURNS tasks LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_task tasks%ROWTYPE; v_message messages%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_task_id IS NULL OR p_conversation_id IS NULL OR p_user_id IS NULL
       OR p_input_message_id IS NULL OR p_output_message_id IS NULL
       OR p_turn_id IS NULL OR p_through_message_id IS NULL
       OR NULLIF(BTRIM(p_base_context_revision), '') IS NULL
       OR NULLIF(BTRIM(p_idempotency_key), '') IS NULL
       OR NULLIF(BTRIM(p_client_task_id), '') IS NULL
       OR p_base_context_revision IS DISTINCT FROM
          'message:' || p_through_message_id::TEXT THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_BINDING_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.user_id IS DISTINCT FROM p_user_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_task.input_message_id IS DISTINCT FROM p_input_message_id
       OR v_task.assistant_message_id IS DISTINCT FROM p_output_message_id
       OR v_task.turn_id IS DISTINCT FROM p_turn_id
       OR v_task.client_task_id IS DISTINCT FROM p_client_task_id
       OR v_task.context_through_message_id IS DISTINCT FROM p_through_message_id THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_TASK_BINDING_MISMATCH' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_message FROM messages
     WHERE id = p_through_message_id AND conversation_id = p_conversation_id
       AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_THROUGH_ANCHOR_MISMATCH' USING ERRCODE = '42501';
    END IF;
    RETURN v_task;
END $$;

CREATE FUNCTION restore_prepared_task_to_legacy_actor(
    p_task_id UUID, p_conversation_id UUID, p_user_id UUID, p_org_id UUID,
    p_input_message_id UUID, p_output_message_id UUID, p_turn_id UUID,
    p_through_message_id UUID, p_base_context_revision TEXT,
    p_idempotency_key TEXT, p_client_task_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_task tasks%ROWTYPE; v_context JSONB;
BEGIN
    v_task := _agent_runtime_validate_task_binding(
        p_task_id, p_conversation_id, p_user_id, p_org_id,
        p_input_message_id, p_output_message_id, p_turn_id,
        p_through_message_id, p_base_context_revision,
        p_idempotency_key, p_client_task_id);
    IF v_task.delivery_context @> '{"actor":true,"runtime":false}'::JSONB THEN
        RETURN jsonb_build_object('outcome', 'already_actor_owned');
    END IF;
    IF NOT (v_task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB) THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_RESTORE_STATE_MISMATCH' USING ERRCODE = '55000';
    END IF;
    v_context := v_task.delivery_context || '{"actor":true,"runtime":false}'::JSONB;
    UPDATE tasks SET delivery_context = v_context WHERE id = p_task_id;
    RETURN jsonb_build_object('outcome', 'restored', 'task_id', p_task_id);
END $$;

CREATE FUNCTION mark_prepared_task_runtime_owned(
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
        RAISE EXCEPTION 'RUNTIME_OWNER_COMMAND_BINDING_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id AND conversation_id = p_conversation_id
       AND user_id = p_user_id AND org_id IS NOT DISTINCT FROM p_org_id;
    SELECT * INTO v_command FROM agent_session_commands
     WHERE id = p_command_id AND session_id = p_session_id
       AND command_type = 'submit_input' AND idempotency_key = p_idempotency_key
       AND org_id IS NOT DISTINCT FROM p_org_id AND user_id = p_user_id;
    IF NOT FOUND OR v_session.id IS NULL OR v_command.id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_COMMAND_BINDING_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB THEN
        IF v_task.delivery_context->>'runtime_session_id' IS DISTINCT FROM p_session_id::TEXT
           OR v_task.delivery_context->>'runtime_command_id' IS DISTINCT FROM p_command_id::TEXT THEN
            RAISE EXCEPTION 'RUNTIME_OWNER_REPLAY_BINDING_MISMATCH' USING ERRCODE = '55000';
        END IF;
        RETURN jsonb_build_object('outcome', 'already_runtime_owned');
    END IF;
    IF NOT (v_task.delivery_context @> '{"actor":true}'::JSONB)
       OR COALESCE((v_task.delivery_context->>'runtime')::BOOLEAN, FALSE) THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_MARK_STATE_MISMATCH' USING ERRCODE = '55000';
    END IF;
    v_context := v_task.delivery_context || jsonb_build_object(
        'actor', FALSE, 'runtime', TRUE, 'runtime_session_id', p_session_id,
        'runtime_command_id', p_command_id);
    UPDATE tasks SET delivery_context = v_context WHERE id = p_task_id;
    RETURN jsonb_build_object('outcome', 'marked', 'task_id', p_task_id);
END $$;

CREATE FUNCTION runtime_submit_ingress_v5_owner_transition(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,
 p_created_by_user_id UUID,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_agent_definition_hash TEXT,p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,
 p_through_message_id UUID,p_base_context_revision TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_config_snapshot JSONB,p_capability_snapshot JSONB,
 p_release_revision TEXT,p_payload JSONB,p_task_id UUID,p_client_task_id TEXT,
 p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,p_request_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE r JSONB; owner_result JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_request_id IS DISTINCT FROM p_idempotency_key THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_REQUEST_ID_MISMATCH' USING ERRCODE = '42501';
    END IF;
    r := runtime_submit_ingress_v5(
        p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
        p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision,
        p_agent_definition_hash,p_command_type,p_idempotency_key,p_channel,
        p_through_message_id,p_base_context_revision,p_effective_toolset_revision,
        p_effective_toolset_hash,p_config_snapshot,p_capability_snapshot,
        p_release_revision,p_payload);
    IF r->>'outcome' IN ('ingress_disabled','org_not_enabled','subject_not_enabled','fenced') THEN
        owner_result := restore_prepared_task_to_legacy_actor(
            p_task_id,p_conversation_id,p_user_id,p_org_id,p_input_message_id,
            p_output_message_id,p_turn_id,p_through_message_id,p_base_context_revision,
            p_idempotency_key,p_client_task_id);
        RETURN r || owner_result;
    END IF;
    IF r->>'outcome' NOT IN ('created','already_exists') THEN
        RETURN r;
    END IF;
    owner_result := mark_prepared_task_runtime_owned(
        p_task_id,p_conversation_id,p_user_id,p_org_id,p_input_message_id,
        p_output_message_id,p_turn_id,p_through_message_id,p_base_context_revision,
        p_idempotency_key,p_client_task_id,(r->>'session_id')::UUID,
        (r->>'entity_id')::UUID);
    RETURN r || owner_result;
END $$;

CREATE FUNCTION enqueue_wecom_runtime_turn_v6(
 p_task_data JSONB,p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,
 p_input_content JSONB,p_delivery_context JSONB,p_agent_definition_id TEXT,
 p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_release_revision TEXT,p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE e JSONB; r JSONB; v_task tasks%ROWTYPE; v_conversation conversations%ROWTYPE;
    v_owner JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    e := enqueue_wecom_generation_turn_v2(
        p_task_data,p_input_message_id,p_output_message_id,p_turn_id,
        p_input_content,p_delivery_context);
    SELECT * INTO v_task FROM tasks WHERE id = (e->>'task_id')::UUID FOR UPDATE;
    SELECT * INTO v_conversation FROM conversations WHERE id = v_task.conversation_id;
    IF v_task.id IS NULL OR v_conversation.id IS NULL THEN
        RAISE EXCEPTION 'WECOM_RUNTIME_TASK_BINDING_MISSING' USING ERRCODE = '42501';
    END IF;
    r := runtime_submit_ingress_v5(
        v_task.conversation_id,v_task.org_id,v_task.user_id,v_conversation.scope_type,
        v_conversation.scope_id,v_task.user_id,p_agent_definition_id,
        p_agent_definition_revision,p_agent_definition_hash,'submit_input',
        p_idempotency_key,'wecom',p_input_message_id,'message:'||p_input_message_id::TEXT,
        p_effective_toolset_revision,p_effective_toolset_hash,'{}'::JSONB,
        jsonb_build_object('requested_groups',jsonb_build_array('code')),p_release_revision,
        jsonb_build_object('schema_revision',3,'channel','wecom','task_id',v_task.id,
            'input_message_id',p_input_message_id,'output_message_id',p_output_message_id,
            'turn_id',p_turn_id,'content',p_input_content,
            'delivery_context',p_delivery_context||'{"actor":false,"runtime":true}'::JSONB));
    IF r->>'outcome' IN ('created','already_exists') THEN
        v_owner := mark_prepared_task_runtime_owned(
            v_task.id,v_task.conversation_id,v_task.user_id,v_task.org_id,
            p_input_message_id,p_output_message_id,p_turn_id,p_input_message_id,
            'message:'||p_input_message_id::TEXT,p_idempotency_key,
            COALESCE(p_task_data->>'client_task_id', 'wecom:' || split_part(p_idempotency_key, ':', 2)),
            (r->>'session_id')::UUID,(r->>'entity_id')::UUID);
        RETURN e || r || v_owner || jsonb_build_object('runtime_owned', TRUE);
    END IF;
    IF r->>'outcome' IN ('ingress_disabled','org_not_enabled','subject_not_enabled','fenced') THEN
        v_owner := restore_prepared_task_to_legacy_actor(
            v_task.id,v_task.conversation_id,v_task.user_id,v_task.org_id,
            p_input_message_id,p_output_message_id,p_turn_id,p_input_message_id,
            'message:'||p_input_message_id::TEXT,p_idempotency_key,
            COALESCE(p_task_data->>'client_task_id', 'wecom:' || split_part(p_idempotency_key, ':', 2)));
        RETURN e || r || v_owner || jsonb_build_object('runtime_owned', FALSE);
    END IF;
    RAISE EXCEPTION 'WECOM_RUNTIME_INGRESS_V5_FAILED: %', r->>'outcome'
        USING ERRCODE = '55000';
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_validate_task_binding(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT),
 restore_prepared_task_to_legacy_actor(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT),
 mark_prepared_task_runtime_owned(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID),
 runtime_submit_ingress_v5_owner_transition(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT),
 enqueue_wecom_runtime_turn_v6(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
 FROM PUBLIC;
GRANT EXECUTE ON FUNCTION restore_prepared_task_to_legacy_actor(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT),
 mark_prepared_task_runtime_owned(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID),
 runtime_submit_ingress_v5_owner_transition(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
 TO everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION enqueue_wecom_runtime_turn_v6(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
 TO everydayai_wecom_runtime;
RESET ROLE;
