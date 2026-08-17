-- Roll back only the media-anchor extension while retaining 227.67's catalog fix.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION submit_agent_runtime_chat_action_v1(
    p_conversation_id UUID, p_org_id UUID, p_user_id UUID,
    p_task_id TEXT, p_message_id TEXT, p_tool_call_id TEXT, p_turn INTEGER,
    p_tool_name TEXT, p_arguments JSONB, p_model_id TEXT,
    p_model_provider TEXT, p_model_revision TEXT, p_catalog_revision TEXT,
    p_policy_revision TEXT, p_executor_type TEXT, p_executor_revision INTEGER,
    p_policy_snapshot JSONB, p_context_receipt JSONB, p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    s agent_runtime_sessions%ROWTYPE;
    c agent_session_commands%ROWTYPE;
    r agent_runs%ROWTYPE;
    m agent_model_steps%ROWTYPE;
    a agent_actions%ROWTYPE;
    pr agent_policy_receipts%ROWTYPE;
    h TEXT;
    request_hash TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF NULLIF(BTRIM(p_tool_name), '') IS NULL
       OR p_tool_name <> LOWER(BTRIM(p_tool_name))
       OR p_tool_name !~ '^[a-z][a-z0-9_.:-]{0,199}$'
       OR jsonb_typeof(p_arguments) IS DISTINCT FROM 'object'
       OR NOT _agent_action_json_is_safe(p_arguments)
       OR jsonb_typeof(p_policy_snapshot) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_context_receipt) IS DISTINCT FROM 'object'
       OR p_turn < 1 OR p_executor_revision < 1
       OR NULLIF(BTRIM(p_idempotency_key), '') IS NULL
       OR length(BTRIM(p_idempotency_key)) > 200 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CHAT_ACTION_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO s FROM agent_runtime_sessions
      WHERE conversation_id = p_conversation_id FOR UPDATE;
    IF NOT FOUND OR s.org_id IS DISTINCT FROM p_org_id
       OR s.user_id IS DISTINCT FROM p_user_id
       OR tenant_org_id() IS DISTINCT FROM s.org_id
       OR tenant_actor_user_id() IS DISTINCT FROM s.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CHAT_ACTION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    request_hash := md5(jsonb_build_object(
        'tool_name', p_tool_name, 'arguments', p_arguments,
        'task_id', p_task_id, 'tool_call_id', p_tool_call_id,
        'turn', p_turn, 'message_id', p_message_id
    )::TEXT);
    SELECT * INTO c FROM agent_session_commands
      WHERE session_id = s.id AND idempotency_key = BTRIM(p_idempotency_key)
      FOR UPDATE;
    IF FOUND THEN
        SELECT * INTO r FROM agent_runs WHERE command_id = c.id FOR UPDATE;
        SELECT * INTO a FROM agent_actions WHERE run_id = r.id ORDER BY action_index LIMIT 1;
        IF a.id IS NULL THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_CHAT_ACTION_IDEMPOTENCY_INCOMPLETE';
        END IF;
        RETURN jsonb_build_object('outcome', 'already_exists',
            'action_id', a.id, 'run_id', r.id, 'model_step_id', a.model_step_id);
    END IF;
    INSERT INTO agent_session_commands(
        session_id, org_id, user_id, command_type, idempotency_key,
        payload, request_hash
    ) VALUES (
        s.id, s.org_id, s.user_id, 'submit_input', BTRIM(p_idempotency_key),
        jsonb_build_object('source', 'chat_action', 'task_id', p_task_id,
                           'message_id', p_message_id, 'turn', p_turn),
        request_hash
    ) RETURNING * INTO c;
    INSERT INTO agent_runs(
        session_id, command_id, org_id, user_id, run_kind, status,
        idempotency_key, request_hash, context_receipt, config_snapshot,
        capability_snapshot, blocking_action_count
    ) VALUES (
        s.id, c.id, s.org_id, s.user_id, 'user', 'waiting_actions',
        'chat-action-run:' || BTRIM(p_idempotency_key), request_hash,
        p_context_receipt,
        jsonb_build_object('model_id', p_model_id, 'provider', p_model_provider,
                           'model_revision', p_model_revision),
        jsonb_build_object('catalog_revision', p_catalog_revision), 1
    ) RETURNING * INTO r;
    UPDATE agent_session_commands SET result_entity_id = r.id WHERE id = c.id;
    INSERT INTO agent_model_steps(
        run_id, session_id, org_id, user_id, step_number, status,
        model_id, provider, model_revision, prompt_revision,
        tool_catalog_revision, request_receipt, response_receipt,
        stop_reason, completed_at
    ) VALUES (
        r.id, s.id, s.org_id, s.user_id, 1, 'completed',
        p_model_id, p_model_provider, p_model_revision, 'chat-bridge-v1',
        p_catalog_revision, jsonb_build_object('message_id', p_message_id, 'turn', p_turn),
        jsonb_build_object('tool_call_id', p_tool_call_id), 'tool_calls',
        clock_timestamp()
    ) RETURNING * INTO m;
    h := encode(sha256(convert_to(p_arguments::TEXT, 'UTF8')), 'hex');
    INSERT INTO agent_actions(
        id, session_id, run_id, model_step_id, org_id, user_id,
        action_index, stable_tool_call_id, tool_name, arguments,
        arguments_hash, request_hash, batch_hash, policy_decision,
        policy_snapshot, policy_revision, retry_disposition, status,
        blocking
    ) VALUES (
        gen_random_uuid(), s.id, r.id, m.id, s.org_id, s.user_id,
        0, COALESCE(NULLIF(BTRIM(p_tool_call_id), ''), p_message_id),
        p_tool_name, p_arguments, h, encode(sha256(convert_to(
            jsonb_build_object('run_id', r.id, 'step_id', m.id,
                               'tool_name', p_tool_name, 'arguments_hash', h)::TEXT,
            'UTF8')), 'hex'), h, 'preauthorized', p_policy_snapshot,
        p_policy_revision, 'non_retryable', 'queued', TRUE
    ) RETURNING * INTO a;
    INSERT INTO agent_policy_receipts(
        action_id, session_id, run_id, org_id, user_id, decision,
        arguments_hash, executor_type, executor_revision, policy_revision,
        effective_scope, reason_codes, receipt_hash, expires_at
    ) VALUES (
        a.id, s.id, r.id, s.org_id, s.user_id, 'allow', h,
        p_executor_type, p_executor_revision, p_policy_revision,
        jsonb_build_object('org_id', s.org_id, 'user_id', s.user_id),
        ARRAY['chat_runtime_submission']::TEXT[],
        encode(sha256(convert_to(jsonb_build_object(
            'action_id', a.id, 'arguments_hash', h,
            'executor_type', p_executor_type,
            'executor_revision', p_executor_revision,
            'policy_revision', p_policy_revision)::TEXT, 'UTF8')), 'hex'),
        clock_timestamp() + interval '5 minutes'
    ) RETURNING * INTO pr;
    UPDATE agent_actions SET policy_snapshot = policy_snapshot ||
        jsonb_build_object('dispatch_policy_receipt_id', pr.id)
      WHERE id = a.id;
    PERFORM append_agent_runtime_event(
        s.id, 'action.requested', r.id, m.id, a.id, 'user', p_user_id::TEXT,
        jsonb_build_object('action_id', a.id, 'source', 'chat'),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object('outcome', 'created', 'action_id', a.id,
        'run_id', r.id, 'model_step_id', m.id, 'state_version', a.state_version);
END;
$$;

RESET ROLE;
