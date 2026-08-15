SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION fail_web_runtime_ingress_task(
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

REVOKE ALL ON FUNCTION fail_web_runtime_ingress_task(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT)
FROM PUBLIC, everydayai_worker, everydayai_runtime, everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION fail_web_runtime_ingress_task(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT)
TO everydayai_runtime;

RESET ROLE;
