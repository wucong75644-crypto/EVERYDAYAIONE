-- 215: Agent Runtime ModelStep, Event projection RPCs.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION create_model_step(
    p_run_id UUID, p_execution_token UUID, p_model_id TEXT, p_provider TEXT,
    p_model_revision TEXT, p_prompt_revision TEXT,
    p_tool_catalog_revision TEXT, p_request_receipt JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_run agent_runs%ROWTYPE;
    v_step agent_model_steps%ROWTYPE;
    v_event JSONB;
    v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token
       OR v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF jsonb_typeof(p_request_receipt) IS DISTINCT FROM 'object'
       OR pg_column_size(p_request_receipt) > 262144 THEN
        RAISE EXCEPTION 'AGENT_MODEL_STEP_INVALID' USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_model_steps(
        run_id, session_id, org_id, user_id, step_number, model_id,
        provider, model_revision, prompt_revision, tool_catalog_revision,
        request_receipt
    ) SELECT
        v_run.id, v_run.session_id, v_run.org_id, v_run.user_id,
        COALESCE(MAX(step_number), 0) + 1, p_model_id, p_provider,
        p_model_revision, p_prompt_revision, p_tool_catalog_revision,
        p_request_receipt
      FROM agent_model_steps WHERE run_id = p_run_id
    RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'model_step.created', v_run.id, v_step.id,
        p_execution_token, 'system', session_user,
        jsonb_build_object('step_number', v_step.step_number),
        ARRAY['audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'created', 'entity_id', v_step.id,
        'state_version', v_step.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION complete_model_step(
    p_step_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_response_receipt JSONB, p_stop_reason TEXT,
    p_provider_stop_reason TEXT, p_input_tokens BIGINT,
    p_output_tokens BIGINT, p_reasoning_tokens BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_event JSONB;
    v_run_id UUID;
    v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT run_id, session_id INTO v_run_id, v_session_id
      FROM agent_model_steps WHERE id = p_step_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = p_step_id FOR UPDATE;
    IF v_step.status = 'completed' THEN
        IF v_step.response_receipt IS DISTINCT FROM p_response_receipt
           OR v_step.stop_reason IS DISTINCT FROM p_stop_reason
           OR v_step.provider_stop_reason IS DISTINCT FROM p_provider_stop_reason
           OR v_step.input_tokens IS DISTINCT FROM p_input_tokens
           OR v_step.output_tokens IS DISTINCT FROM p_output_tokens
           OR v_step.reasoning_tokens IS DISTINCT FROM p_reasoning_tokens THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_completed', 'entity_id', v_step.id,
            'state_version', v_step.state_version
        );
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token
       OR v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_step.status <> 'running'
       OR v_step.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF jsonb_typeof(p_response_receipt) IS DISTINCT FROM 'object'
       OR pg_column_size(p_response_receipt) > 262144
       OR p_stop_reason NOT IN (
           'final', 'tool_calls', 'structured_final', 'length',
           'content_filter', 'model_refusal', 'budget_exhausted',
           'cancelled', 'provider_error', 'protocol_error'
       ) OR LEAST(p_input_tokens, p_output_tokens, p_reasoning_tokens) < 0 THEN
        RAISE EXCEPTION 'AGENT_MODEL_STEP_COMPLETE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE agent_model_steps SET status = 'completed',
           response_receipt = p_response_receipt,
           stop_reason = p_stop_reason,
           provider_stop_reason = p_provider_stop_reason,
           input_tokens = p_input_tokens, output_tokens = p_output_tokens,
           reasoning_tokens = p_reasoning_tokens,
           state_version = state_version + 1,
           completed_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = p_step_id RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_step.session_id, 'model_step.completed', v_step.run_id, v_step.id,
        p_execution_token, 'model', session_user,
        jsonb_build_object('stop_reason', p_stop_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'completed', 'entity_id', v_step.id,
        'state_version', v_step.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION fail_model_step(
    p_step_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_event JSONB;
    v_run_id UUID;
    v_session_id UUID;
    v_error_code TEXT := BTRIM(COALESCE(p_error_code, ''));
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF length(v_error_code) NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'AGENT_MODEL_STEP_ERROR_CODE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT run_id, session_id INTO v_run_id, v_session_id
      FROM agent_model_steps WHERE id = p_step_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = p_step_id FOR UPDATE;
    IF v_step.status = 'failed' THEN
        IF v_step.terminal_reason IS DISTINCT FROM v_error_code THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_failed', 'entity_id', v_step.id,
            'state_version', v_step.state_version
        );
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token
       OR v_step.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_step.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_model_steps SET status = 'failed',
           stop_reason = 'provider_error', terminal_reason = v_error_code,
           state_version = state_version + 1,
           completed_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = p_step_id RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_step.session_id, 'model_step.failed', v_step.run_id, v_step.id,
        p_execution_token, 'model', session_user,
        jsonb_build_object('error_code', v_error_code),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'failed', 'entity_id', v_step.id,
        'state_version', v_step.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION claim_agent_projection_outbox(
    p_batch_size INTEGER DEFAULT 50, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_rows JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_CLAIM_INVALID'
            USING ERRCODE = '22023';
    END IF;
    WITH candidates AS (
        SELECT id FROM agent_projection_outbox
         WHERE next_attempt_at <= clock_timestamp()
           AND (
               status = 'pending'
               OR (status = 'processing'
                   AND lease_expires_at <= clock_timestamp())
           )
         ORDER BY next_attempt_at, created_at, id
         FOR UPDATE SKIP LOCKED LIMIT p_batch_size
    ), claimed AS (
        UPDATE agent_projection_outbox outbox SET status = 'processing',
               attempt_count = attempt_count + 1,
               lease_token = gen_random_uuid(),
               lease_expires_at = clock_timestamp()
                   + make_interval(secs => p_lease_seconds),
               updated_at = clock_timestamp()
          FROM candidates WHERE outbox.id = candidates.id
        RETURNING outbox.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_rows FROM claimed;
    RETURN v_rows;
END;
$$;

CREATE FUNCTION complete_agent_projection_outbox(
    p_outbox_id UUID, p_lease_token UUID, p_checkpoint JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_row agent_projection_outbox%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF jsonb_typeof(p_checkpoint) IS DISTINCT FROM 'object'
       OR pg_column_size(p_checkpoint) > 262144 THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_CHECKPOINT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_row FROM agent_projection_outbox
     WHERE id = p_outbox_id FOR UPDATE;
    IF v_row.status = 'delivered' THEN
        IF v_row.checkpoint IS DISTINCT FROM p_checkpoint THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object('outcome', 'already_completed');
    END IF;
    IF v_row.status <> 'processing'
       OR v_row.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_row.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    UPDATE agent_projection_outbox SET status = 'delivered',
           checkpoint = p_checkpoint, lease_token = NULL,
           lease_expires_at = NULL, delivered_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE id = p_outbox_id;
    RETURN jsonb_build_object('outcome', 'completed');
END;
$$;

CREATE FUNCTION fail_agent_projection_outbox(
    p_outbox_id UUID, p_lease_token UUID, p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_row agent_projection_outbox%ROWTYPE;
    v_error_code TEXT := BTRIM(COALESCE(p_error_code, ''));
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF length(v_error_code) NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_ERROR_CODE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_row FROM agent_projection_outbox
     WHERE id = p_outbox_id FOR UPDATE;
    IF v_row.status = 'delivered' THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_row.status <> 'processing'
       OR v_row.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_row.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    UPDATE agent_projection_outbox SET
           status = CASE WHEN attempt_count >= 8 THEN 'dead' ELSE 'pending' END,
           next_attempt_at = clock_timestamp()
               + make_interval(secs => LEAST(300, 5 * (2 ^ attempt_count))),
           lease_token = NULL, lease_expires_at = NULL,
           last_error_code = v_error_code,
           updated_at = clock_timestamp()
     WHERE id = p_outbox_id;
    RETURN jsonb_build_object('outcome', 'failed');
END;
$$;

REVOKE ALL ON FUNCTION
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    fail_agent_projection_outbox(UUID, UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    fail_agent_projection_outbox(UUID, UUID, TEXT)
TO everydayai_worker;

RESET ROLE;
