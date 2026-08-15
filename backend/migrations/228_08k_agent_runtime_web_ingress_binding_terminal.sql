-- 228.08k: validate Web Runtime ownership against the current input message
-- while preserving the legacy context snapshot anchor, and fail closed without
-- leaving a streaming assistant placeholder when ingress cannot take ownership.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_validate_web_task_binding(
    p_task_id UUID, p_conversation_id UUID, p_user_id UUID, p_org_id UUID,
    p_input_message_id UUID, p_output_message_id UUID, p_turn_id UUID,
    p_through_message_id UUID, p_base_context_revision TEXT,
    p_client_task_id TEXT
) RETURNS tasks LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    task tasks%ROWTYPE;
    conversation conversations%ROWTYPE;
    input_message messages%ROWTYPE;
    output_message messages%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF tenant_actor_user_id() IS DISTINCT FROM p_user_id
       OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'RUNTIME_WEB_OWNER_TENANT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_task_id IS NULL OR p_conversation_id IS NULL OR p_user_id IS NULL
       OR p_input_message_id IS NULL OR p_output_message_id IS NULL
       OR p_turn_id IS NULL OR p_through_message_id IS DISTINCT FROM p_input_message_id
       OR p_base_context_revision IS DISTINCT FROM
          'message:' || p_input_message_id::TEXT
       OR NULLIF(BTRIM(p_client_task_id), '') IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_WEB_OWNER_BINDING_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR task.conversation_id IS DISTINCT FROM p_conversation_id
       OR task.user_id IS DISTINCT FROM p_user_id
       OR task.org_id IS DISTINCT FROM p_org_id
       OR task.input_message_id IS DISTINCT FROM p_input_message_id
       OR task.assistant_message_id IS DISTINCT FROM p_output_message_id
       OR task.turn_id IS DISTINCT FROM p_turn_id
       OR task.client_task_id IS DISTINCT FROM p_client_task_id THEN
        RAISE EXCEPTION 'RUNTIME_WEB_OWNER_TASK_BINDING_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO conversation FROM conversations
     WHERE id = p_conversation_id AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND source = 'web' AND scope_type = 'user'
       AND scope_id = p_user_id::TEXT;
    SELECT * INTO input_message FROM messages
     WHERE id = p_input_message_id AND conversation_id = p_conversation_id
       AND org_id IS NOT DISTINCT FROM p_org_id AND role = 'user'
       AND turn_id = p_turn_id FOR UPDATE;
    SELECT * INTO output_message FROM messages
     WHERE id = p_output_message_id AND conversation_id = p_conversation_id
       AND org_id IS NOT DISTINCT FROM p_org_id AND role = 'assistant'
       AND turn_id = p_turn_id AND reply_to_message_id = p_input_message_id
       FOR UPDATE;
    IF conversation.id IS NULL OR input_message.id IS NULL
       OR output_message.id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_WEB_OWNER_MESSAGE_BINDING_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN task;
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
    session agent_runtime_sessions%ROWTYPE;
    command agent_session_commands%ROWTYPE;
    runtime_pending BOOLEAN;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_request_id IS DISTINCT FROM p_idempotency_key OR p_channel <> 'web'
       OR p_scope_kind <> 'user' OR p_scope_id <> p_user_id::TEXT
       OR p_created_by_user_id IS DISTINCT FROM p_user_id
       OR p_command_type <> 'submit_input'
       OR p_payload->>'channel' <> 'web'
       OR p_payload->>'task_id' IS DISTINCT FROM p_task_id::TEXT
       OR p_payload->>'client_task_id' IS DISTINCT FROM p_client_task_id
       OR p_payload->>'input_message_id' IS DISTINCT FROM p_input_message_id::TEXT
       OR p_payload->>'output_message_id' IS DISTINCT FROM p_output_message_id::TEXT
       OR p_payload->>'turn_id' IS DISTINCT FROM p_turn_id::TEXT
       OR p_payload->>'request_id' IS DISTINCT FROM p_request_id THEN
        RAISE EXCEPTION 'RUNTIME_WEB_OWNER_REQUEST_BINDING_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    task := _agent_runtime_validate_web_task_binding(
        p_task_id,p_conversation_id,p_user_id,p_org_id,p_input_message_id,
        p_output_message_id,p_turn_id,p_through_message_id,
        p_base_context_revision,p_client_task_id);

    result := runtime_submit_ingress_v5(
        p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
        p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision,
        p_agent_definition_hash,p_command_type,p_idempotency_key,p_channel,
        p_through_message_id,p_base_context_revision,p_effective_toolset_revision,
        p_effective_toolset_hash,p_config_snapshot,p_capability_snapshot,
        p_release_revision,p_payload);

    IF result->>'outcome' NOT IN ('created','already_exists') THEN
        runtime_pending := task.delivery_context @>
            '{"actor":false,"runtime":false,"runtime_pending":true}'::JSONB;
        IF NOT runtime_pending THEN
            RAISE EXCEPTION 'RUNTIME_REQUIRED_TASK_OWNER_STATE_MISMATCH'
                USING ERRCODE = '55000';
        END IF;
        UPDATE tasks SET delivery_context = task.delivery_context ||
            jsonb_build_object(
                'actor',FALSE,'runtime',TRUE,'runtime_pending',FALSE,
                'runtime_rejected',TRUE,'runtime_rejection_code',
                COALESCE(result->>'outcome','unknown'))
         WHERE id = p_task_id;
        RETURN result || jsonb_build_object(
            'outcome','runtime_required_unavailable','runtime_owned',FALSE);
    END IF;

    SELECT * INTO session FROM agent_runtime_sessions
     WHERE id = (result->>'session_id')::UUID
       AND conversation_id = p_conversation_id AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id;
    SELECT * INTO command FROM agent_session_commands
     WHERE id = (result->>'entity_id')::UUID
       AND session_id = (result->>'session_id')::UUID
       AND command_type = 'submit_input'
       AND idempotency_key = p_idempotency_key
       AND org_id IS NOT DISTINCT FROM p_org_id AND user_id = p_user_id;
    IF session.id IS NULL OR command.id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_WEB_OWNER_COMMAND_BINDING_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB THEN
        IF task.delivery_context->>'runtime_session_id'
               IS DISTINCT FROM session.id::TEXT
           OR task.delivery_context->>'runtime_command_id'
               IS DISTINCT FROM command.id::TEXT THEN
            RAISE EXCEPTION 'RUNTIME_WEB_OWNER_REPLAY_BINDING_MISMATCH'
                USING ERRCODE = '55000';
        END IF;
        RETURN result || jsonb_build_object(
            'outcome','already_runtime_owned','runtime_owned',TRUE);
    END IF;
    IF NOT (task.delivery_context @>
        '{"actor":false,"runtime":false,"runtime_pending":true}'::JSONB) THEN
        RAISE EXCEPTION 'RUNTIME_WEB_OWNER_MARK_STATE_MISMATCH'
            USING ERRCODE = '55000';
    END IF;
    UPDATE tasks SET delivery_context = task.delivery_context ||
        jsonb_build_object(
            'actor',FALSE,'runtime',TRUE,'runtime_pending',FALSE,
            'runtime_session_id',session.id,'runtime_command_id',command.id)
     WHERE id = p_task_id;
    RETURN result || jsonb_build_object(
        'outcome','marked','task_id',p_task_id,'runtime_owned',TRUE);
END $$;

CREATE FUNCTION fail_web_runtime_ingress_task(
    p_task_id UUID, p_conversation_id UUID, p_user_id UUID, p_org_id UUID,
    p_input_message_id UUID, p_output_message_id UUID, p_turn_id UUID,
    p_client_task_id TEXT, p_failure_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    task tasks%ROWTYPE;
    output_message messages%ROWTYPE;
    failed_message TEXT := jsonb_build_array(jsonb_build_object(
        'type','text','text','生成服务暂未就绪，请稍后重试'))::TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF tenant_actor_user_id() IS DISTINCT FROM p_user_id
       OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'RUNTIME_WEB_FAILURE_TENANT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_task_id IS NULL OR p_conversation_id IS NULL OR p_user_id IS NULL
       OR p_input_message_id IS NULL OR p_output_message_id IS NULL
       OR p_turn_id IS NULL OR NULLIF(BTRIM(p_client_task_id), '') IS NULL
       OR p_failure_code !~ '^[A-Z][A-Z0-9_]{0,99}$' THEN
        RAISE EXCEPTION 'RUNTIME_WEB_FAILURE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO task FROM tasks WHERE id = p_task_id FOR UPDATE;
    SELECT * INTO output_message FROM messages
     WHERE id = p_output_message_id FOR UPDATE;
    IF task.id IS NULL OR output_message.id IS NULL
       OR task.conversation_id IS DISTINCT FROM p_conversation_id
       OR task.user_id IS DISTINCT FROM p_user_id
       OR task.org_id IS DISTINCT FROM p_org_id
       OR task.input_message_id IS DISTINCT FROM p_input_message_id
       OR task.assistant_message_id IS DISTINCT FROM p_output_message_id
       OR task.turn_id IS DISTINCT FROM p_turn_id
       OR task.client_task_id IS DISTINCT FROM p_client_task_id
       OR output_message.conversation_id IS DISTINCT FROM p_conversation_id
       OR output_message.org_id IS DISTINCT FROM p_org_id
       OR output_message.turn_id IS DISTINCT FROM p_turn_id
       OR output_message.reply_to_message_id IS DISTINCT FROM p_input_message_id
       OR output_message.role <> 'assistant' THEN
        RAISE EXCEPTION 'RUNTIME_WEB_FAILURE_BINDING_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF task.status = 'failed' AND output_message.status::TEXT = 'failed'
       AND task.terminal_reason = 'runtime_ingress_failed' THEN
        RETURN jsonb_build_object(
            'task_id',task.id,'already_failed',TRUE);
    END IF;
    IF task.status NOT IN ('pending','preparing')
       OR output_message.status::TEXT NOT IN ('pending','streaming','generating')
       OR NOT (
           task.delivery_context @>
             '{"actor":false,"runtime":false,"runtime_pending":true}'::JSONB
           OR task.delivery_context @>
             '{"actor":false,"runtime":true,"runtime_rejected":true}'::JSONB
       ) THEN
        RAISE EXCEPTION 'RUNTIME_WEB_FAILURE_STATE_MISMATCH'
            USING ERRCODE = '55000';
    END IF;
    UPDATE tasks SET status='failed',completed_at=clock_timestamp(),
        terminal_reason='runtime_ingress_failed',
        error_message='生成服务暂未就绪，请稍后重试',
        delivery_context=task.delivery_context || jsonb_build_object(
            'actor',FALSE,'runtime',FALSE,'runtime_pending',FALSE,
            'runtime_rejected',TRUE,'runtime_failure_code',p_failure_code)
     WHERE id = p_task_id;
    UPDATE messages SET status='failed',is_error=TRUE,content=failed_message
     WHERE id = p_output_message_id;
    RETURN jsonb_build_object('task_id',task.id,'already_failed',FALSE);
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_validate_web_task_binding(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT),
 runtime_submit_ingress_v6_required(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT),
 fail_web_runtime_ingress_task(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT)
FROM PUBLIC, everydayai_worker, everydayai_runtime, everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v6_required(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT),
 fail_web_runtime_ingress_task(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT)
TO everydayai_runtime;

RESET ROLE;
