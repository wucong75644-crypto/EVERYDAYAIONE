-- 220_12: Atomic ordered compatibility Projection RPCs.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_compat_projection_action(p_event agent_runtime_events)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_event.event_version <> 1 THEN
        RAISE EXCEPTION 'AGENT_COMPAT_EVENT_VERSION_UNSUPPORTED'
            USING ERRCODE = '22023';
    END IF;
    RETURN CASE
        WHEN p_event.event_type = 'command.accepted' THEN 'user_message'
        WHEN p_event.event_type = 'run.created' THEN 'run_pending'
        WHEN p_event.event_type IN ('run.claimed', 'run.resumed')
            THEN 'run_running'
        WHEN p_event.event_type = 'run.waiting' THEN 'run_waiting'
        WHEN p_event.event_type = 'run.completed' THEN 'run_completed'
        WHEN p_event.event_type = 'run.failed' THEN 'run_failed'
        WHEN p_event.event_type = 'run.cancelled' THEN 'run_cancelled'
        WHEN p_event.event_type IN (
            'action.requested', 'action.accepted', 'action.retry_scheduled',
            'action.unknown', 'action.completed', 'action.failed',
            'action.cancelled'
        ) THEN 'action_progress'
        WHEN p_event.event_type IN (
            'session.created', 'command.attempts_exhausted',
            'model_step.created', 'model_step.completed', 'model_step.failed'
        ) THEN 'checkpoint_only'
        ELSE NULL
    END;
END;
$$;

CREATE FUNCTION claim_agent_compat_projection_outbox(
    p_batch_size INTEGER DEFAULT 50, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_rows JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_CLAIM_INVALID'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_compat_projection_checkpoints(
        session_id, projection_kind
    )
    SELECT DISTINCT outbox.session_id, outbox.projection_kind
      FROM agent_projection_outbox outbox
     WHERE outbox.projection_kind IN ('web_runtime', 'wecom')
    ON CONFLICT DO NOTHING;
    WITH eligible AS (
        SELECT outbox.id
          FROM agent_projection_outbox outbox
          JOIN agent_runtime_events event ON event.id = outbox.event_id
          JOIN agent_compat_projection_checkpoints checkpoint
            ON checkpoint.session_id = outbox.session_id
           AND checkpoint.projection_kind = outbox.projection_kind
         WHERE outbox.projection_kind IN ('web_runtime', 'wecom')
           AND outbox.next_attempt_at <= clock_timestamp()
           AND (
               outbox.status = 'pending'
               OR (outbox.status = 'processing'
                   AND outbox.lease_expires_at <= clock_timestamp())
           )
           AND event.sequence > checkpoint.through_sequence
           AND NOT EXISTS (
               SELECT 1
                 FROM agent_projection_outbox earlier
                 JOIN agent_runtime_events earlier_event
                   ON earlier_event.id = earlier.event_id
                WHERE earlier.session_id = outbox.session_id
                  AND earlier.projection_kind = outbox.projection_kind
                  AND earlier_event.sequence < event.sequence
                  AND earlier_event.sequence > checkpoint.through_sequence
                  AND earlier.status <> 'delivered'
           )
         ORDER BY outbox.next_attempt_at, event.occurred_at, outbox.id
         FOR UPDATE OF outbox SKIP LOCKED
         LIMIT p_batch_size
    ), claimed AS (
        UPDATE agent_projection_outbox outbox SET status = 'processing',
               attempt_count = attempt_count + 1,
               lease_token = gen_random_uuid(),
               lease_expires_at = clock_timestamp()
                   + make_interval(secs => p_lease_seconds),
               updated_at = clock_timestamp()
          FROM eligible WHERE outbox.id = eligible.id
        RETURNING outbox.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_rows FROM claimed;
    RETURN v_rows;
END;
$$;

CREATE FUNCTION _agent_compat_project_command(
    p_event agent_runtime_events
) RETURNS UUID LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE
    v_command agent_session_commands%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_message messages%ROWTYPE;
    v_text TEXT;
BEGIN
    SELECT * INTO v_command FROM agent_session_commands
     WHERE id = p_event.correlation_id;
    IF NOT FOUND OR v_command.session_id <> p_event.session_id THEN
        RAISE EXCEPTION 'AGENT_COMPAT_COMMAND_ASSOCIATION_INVALID'
            USING ERRCODE = '55000';
    END IF;
    IF v_command.command_type <> 'submit_input' THEN RETURN NULL; END IF;
    v_text := COALESCE(v_command.payload->>'text', '');
    IF v_text = '' THEN RETURN NULL; END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_event.session_id;
    INSERT INTO messages(
        id, conversation_id, org_id, role, content, status, credits_cost,
        client_request_id, created_at, turn_id
    ) VALUES (
        v_command.id, v_session.conversation_id, v_session.org_id, 'user',
        jsonb_build_array(jsonb_build_object(
            'type', 'text', 'text', v_text))::TEXT,
        'completed', 0, 'agent-runtime:command:' || v_command.id::TEXT,
        p_event.occurred_at, v_command.id
    ) ON CONFLICT (id) DO NOTHING;
    SELECT * INTO v_message FROM messages WHERE id = v_command.id FOR UPDATE;
    IF v_message.conversation_id <> v_session.conversation_id
       OR v_message.org_id IS DISTINCT FROM v_session.org_id
       OR v_message.role::TEXT <> 'user'
       OR v_message.client_request_id IS DISTINCT FROM
          'agent-runtime:command:' || v_command.id::TEXT THEN
        RAISE EXCEPTION 'AGENT_COMPAT_MESSAGE_CONFLICT' USING ERRCODE = '23505';
    END IF;
    RETURN v_message.id;
END;
$$;

CREATE FUNCTION _agent_compat_project_completed_run(
    p_run agent_runs, p_session agent_runtime_sessions,
    p_command agent_session_commands, p_task tasks
) RETURNS UUID LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE
    v_result agent_model_results%ROWTYPE;
    v_step agent_model_steps%ROWTYPE;
    v_message messages%ROWTYPE;
    v_content TEXT;
    v_final_count INTEGER;
BEGIN
    SELECT count(*) INTO v_final_count
      FROM agent_model_steps step
      JOIN agent_model_results result ON result.model_step_id = step.id
     WHERE step.run_id = p_run.id AND step.status = 'completed'
       AND step.stop_reason IN ('final', 'structured_final');
    SELECT * INTO v_step FROM agent_model_steps
     WHERE run_id = p_run.id ORDER BY step_number DESC LIMIT 1;
    SELECT * INTO v_result FROM agent_model_results
     WHERE model_step_id = v_step.id;
    IF p_run.status <> 'completed' OR v_final_count <> 1
       OR v_result.id IS NULL OR v_step.id IS NULL
       OR v_result.run_id <> p_run.id
       OR v_result.session_id <> p_run.session_id
       OR v_result.model_step_id <> v_step.id OR v_step.run_id <> p_run.id
       OR v_step.status <> 'completed'
       OR v_step.stop_reason NOT IN ('final', 'structured_final')
       OR v_result.content_hash IS DISTINCT FROM p_run.result_hash
       OR v_result.content_hash IS DISTINCT FROM encode(digest(
           convert_to(CASE WHEN v_result.output_kind = 'text'
               THEN v_result.text_content
               ELSE v_result.structured_content::TEXT END, 'UTF8'),
           'sha256'), 'hex') THEN
        RAISE EXCEPTION 'AGENT_COMPAT_MODEL_RESULT_INVALID'
            USING ERRCODE = '55000';
    END IF;
    v_content := CASE WHEN v_result.output_kind = 'text'
        THEN jsonb_build_array(jsonb_build_object(
            'type', 'text', 'text', v_result.text_content))::TEXT
        ELSE jsonb_build_array(jsonb_build_object(
            'type', 'data', 'data', v_result.structured_content))::TEXT END;
    INSERT INTO messages(
        id, conversation_id, org_id, role, content, status, credits_cost,
        client_request_id, created_at, turn_id, reply_to_message_id
    ) VALUES (
        v_result.id, p_session.conversation_id, p_run.org_id, 'assistant',
        v_content, 'completed', 0,
        'agent-runtime:result:' || v_result.id::TEXT,
        v_result.created_at, p_command.id,
        (SELECT id FROM messages WHERE id = p_command.id)
    ) ON CONFLICT (id) DO NOTHING;
    SELECT * INTO v_message FROM messages WHERE id = v_result.id FOR UPDATE;
    IF v_message.client_request_id IS DISTINCT FROM
          'agent-runtime:result:' || v_result.id::TEXT
       OR v_message.content IS DISTINCT FROM v_content THEN
        RAISE EXCEPTION 'AGENT_COMPAT_RESULT_MESSAGE_CONFLICT'
            USING ERRCODE = '23505';
    END IF;
    UPDATE tasks SET status = 'completed',
           assistant_message_id = v_message.id,
           result = jsonb_build_object(
               'runtime_run_id', p_run.id,
               'model_result_id', v_result.id,
               'content_hash', v_result.content_hash),
           completed_at = COALESCE(p_run.completed_at, clock_timestamp())
     WHERE id = p_task.id;
    RETURN v_message.id;
END;
$$;

CREATE FUNCTION _agent_compat_project_run(
    p_event agent_runtime_events, p_action TEXT,
    OUT projected_message_id UUID, OUT projected_task_id UUID,
    OUT projected_delivery_id UUID
) LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE
    v_run agent_runs%ROWTYPE; v_session agent_runtime_sessions%ROWTYPE;
    v_command agent_session_commands%ROWTYPE; v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE; v_status TEXT;
BEGIN
    SELECT * INTO v_run FROM agent_runs WHERE id = p_event.run_id FOR UPDATE;
    IF NOT FOUND OR v_run.session_id <> p_event.session_id THEN
        RAISE EXCEPTION 'AGENT_COMPAT_RUN_ASSOCIATION_INVALID'
            USING ERRCODE = '55000';
    END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = v_run.session_id;
    SELECT * INTO v_command FROM agent_session_commands
     WHERE id = v_run.command_id;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_session.conversation_id;
    IF v_command.session_id <> v_session.id OR v_conversation.id IS NULL
       OR COALESCE(
           v_run.user_id, v_conversation.user_id,
           v_session.created_by_user_id
       ) IS NULL THEN
        RAISE EXCEPTION 'AGENT_COMPAT_RUN_SCOPE_INVALID' USING ERRCODE = '55000';
    END IF;
    v_status := CASE p_action
        WHEN 'run_pending' THEN 'pending'
        WHEN 'run_running' THEN 'running'
        WHEN 'run_waiting' THEN 'running'
        WHEN 'run_completed' THEN 'completed'
        WHEN 'run_failed' THEN 'failed'
        WHEN 'run_cancelled' THEN 'cancelled'
    END;
    INSERT INTO tasks(
        id, external_task_id, user_id, org_id, conversation_id, type, status,
        credits_locked, credits_used, request_params, input_message_id,
        turn_id, execution_mode, delivery_context, created_at
    ) VALUES (
        v_run.id, 'agent-runtime:run:' || v_run.id::TEXT,
        COALESCE(
            v_run.user_id, v_conversation.user_id,
            v_session.created_by_user_id
        ), v_run.org_id,
        v_session.conversation_id, 'chat', v_status, 0, 0,
        jsonb_build_object('runtime_run_id', v_run.id),
        (SELECT id FROM messages WHERE id = v_command.id),
        v_command.id, 'serial',
        COALESCE(v_command.payload->'delivery_context', '{}'::JSONB),
        v_run.created_at
    ) ON CONFLICT (id) DO NOTHING;
    SELECT * INTO v_task FROM tasks WHERE id = v_run.id FOR UPDATE;
    IF v_task.external_task_id IS DISTINCT FROM
          'agent-runtime:run:' || v_run.id::TEXT
       OR v_task.conversation_id <> v_session.conversation_id
       OR v_task.org_id IS DISTINCT FROM v_run.org_id THEN
        RAISE EXCEPTION 'AGENT_COMPAT_TASK_CONFLICT' USING ERRCODE = '23505';
    END IF;
    IF p_action = 'run_completed' THEN
        projected_message_id := _agent_compat_project_completed_run(
            v_run, v_session, v_command, v_task);
    ELSE
        UPDATE tasks SET status = v_status,
               error_message = CASE WHEN v_status = 'failed'
                   THEN v_run.terminal_reason ELSE error_message END,
               completed_at = CASE WHEN v_status IN ('failed', 'cancelled')
                   THEN COALESCE(v_run.completed_at, clock_timestamp())
                   ELSE NULL END
         WHERE id = v_task.id;
    END IF;
    projected_task_id := v_task.id;
    IF v_status IN ('completed', 'failed')
       AND v_task.delivery_context @> '{"channel":"wecom"}'::JSONB THEN
        INSERT INTO conversation_deliveries(
            task_id, channel, delivery_kind, target_context
        ) VALUES (
            v_task.id, 'wecom', 'assistant_terminal', v_task.delivery_context
        ) ON CONFLICT (task_id, channel, delivery_kind) DO NOTHING
        RETURNING id INTO projected_delivery_id;
        IF projected_delivery_id IS NULL THEN
            SELECT id INTO projected_delivery_id FROM conversation_deliveries
             WHERE task_id = v_task.id AND channel = 'wecom'
               AND delivery_kind = 'assistant_terminal';
        END IF;
    END IF;
END;
$$;

CREATE FUNCTION apply_agent_compat_projection(
    p_outbox_id UUID, p_lease_token UUID, p_action TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_outbox agent_projection_outbox%ROWTYPE;
    v_event agent_runtime_events%ROWTYPE;
    v_checkpoint agent_compat_projection_checkpoints%ROWTYPE;
    v_result agent_compat_projection_results%ROWTYPE;
    v_expected TEXT; v_message_id UUID; v_task_id UUID; v_delivery_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_outbox FROM agent_projection_outbox
     WHERE id = p_outbox_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_result FROM agent_compat_projection_results
     WHERE outbox_id = p_outbox_id;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'outcome', 'already_applied', 'result', to_jsonb(v_result));
    END IF;
    IF v_outbox.status <> 'processing'
       OR v_outbox.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_outbox.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_outbox.session_id FOR UPDATE;
    SELECT * INTO v_event FROM agent_runtime_events
     WHERE id = v_outbox.event_id FOR SHARE;
    SELECT * INTO v_checkpoint FROM agent_compat_projection_checkpoints
     WHERE session_id = v_outbox.session_id
       AND projection_kind = v_outbox.projection_kind FOR UPDATE;
    IF v_event.session_id <> v_outbox.session_id
       OR v_checkpoint.session_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_ASSOCIATION_INVALID'
            USING ERRCODE = '55000';
    END IF;
    IF v_event.sequence <= v_checkpoint.through_sequence THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_REORDERED'
            USING ERRCODE = '55000';
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_projection_outbox earlier
        JOIN agent_runtime_events earlier_event ON earlier_event.id = earlier.event_id
         WHERE earlier.session_id = v_outbox.session_id
           AND earlier.projection_kind = v_outbox.projection_kind
           AND earlier_event.sequence < v_event.sequence
           AND earlier_event.sequence > v_checkpoint.through_sequence
           AND earlier.status <> 'delivered'
    ) THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_GAP' USING ERRCODE = '55000';
    END IF;
    v_expected := _agent_compat_projection_action(v_event);
    IF v_expected IS NULL OR v_expected IS DISTINCT FROM p_action THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_ACTION_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF v_expected = 'user_message' THEN
        v_message_id := _agent_compat_project_command(v_event);
    ELSIF v_expected LIKE 'run_%' THEN
        SELECT projected.projected_message_id, projected.projected_task_id,
               projected.projected_delivery_id
          INTO v_message_id, v_task_id, v_delivery_id
          FROM _agent_compat_project_run(v_event, v_expected) projected;
    END IF;
    INSERT INTO agent_compat_projection_results(
        outbox_id, event_id, session_id, projection_kind, event_sequence,
        projection_action, message_id, task_id, delivery_id
    ) VALUES (
        v_outbox.id, v_event.id, v_event.session_id, v_outbox.projection_kind,
        v_event.sequence, v_expected, v_message_id, v_task_id, v_delivery_id
    ) RETURNING * INTO v_result;
    UPDATE agent_compat_projection_checkpoints
       SET through_sequence = v_event.sequence, last_event_id = v_event.id,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE session_id = v_event.session_id
       AND projection_kind = v_outbox.projection_kind;
    UPDATE agent_projection_outbox SET status = 'delivered',
           checkpoint = jsonb_build_object(
               'through_sequence', v_event.sequence,
               'result_id', v_result.outbox_id),
           lease_token = NULL, lease_expires_at = NULL,
           delivered_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = v_outbox.id;
    RETURN jsonb_build_object(
        'outcome', 'applied', 'result', to_jsonb(v_result));
END;
$$;

CREATE FUNCTION get_agent_compat_projection_result(p_outbox_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_result agent_compat_projection_results%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_result FROM agent_compat_projection_results
     WHERE outbox_id = p_outbox_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object('outcome', 'found', 'result', to_jsonb(v_result));
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_compat_projection_action(agent_runtime_events),
    _agent_compat_project_command(agent_runtime_events),
    _agent_compat_project_completed_run(
        agent_runs, agent_runtime_sessions, agent_session_commands, tasks),
    _agent_compat_project_run(agent_runtime_events, TEXT),
    claim_agent_compat_projection_outbox(INTEGER, INTEGER),
    apply_agent_compat_projection(UUID, UUID, TEXT),
    get_agent_compat_projection_result(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    claim_agent_compat_projection_outbox(INTEGER, INTEGER),
    apply_agent_compat_projection(UUID, UUID, TEXT),
    get_agent_compat_projection_result(UUID)
TO everydayai_worker;

RESET ROLE;
