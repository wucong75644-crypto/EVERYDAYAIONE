-- 218_02: Unique Tool Calls terminal transaction.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _canonical_agent_action_batch(
    p_step agent_model_steps, p_actions JSONB
) RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_value JSONB;
BEGIN
    IF jsonb_typeof(p_actions) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_actions) = 0
       OR pg_column_size(p_actions) > 1048576 THEN
        RAISE EXCEPTION 'AGENT_ACTION_BATCH_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT jsonb_agg(
        jsonb_build_object(
            'action_id', item->>'action_id',
            'index', (item->>'index')::INTEGER,
            'stable_tool_call_id', btrim(item->>'stable_tool_call_id'),
            'provider_call_id', NULLIF(btrim(item->>'provider_call_id'), ''),
            'tool_name', lower(btrim(item->>'tool_name')),
            'arguments_hash', encode(sha256(convert_to(
                (item->'arguments')::TEXT, 'UTF8')), 'hex'),
            'wave', COALESCE((item->>'wave')::INTEGER, 0),
            'dependencies', COALESCE((
                SELECT jsonb_agg(dependency ORDER BY dependency)
                  FROM jsonb_array_elements_text(
                      COALESCE(item->'dependencies', '[]'::JSONB)
                  ) dependency
            ), '[]'::JSONB),
            'blocking', COALESCE((item->>'blocking')::BOOLEAN, TRUE),
            'policy_decision', item->>'policy_decision',
            'policy_snapshot', item->'policy_snapshot',
            'policy_revision', item->>'policy_revision',
            'retry_disposition', item->>'retry_disposition',
            'session_id', p_step.session_id,
            'run_id', p_step.run_id,
            'model_step_id', p_step.id,
            'org_id', p_step.org_id,
            'user_id', p_step.user_id
            ,'request_hash', encode(sha256(convert_to(jsonb_build_object(
                'session_id', p_step.session_id, 'run_id', p_step.run_id,
                'model_step_id', p_step.id, 'action_id', item->>'action_id',
                'index', (item->>'index')::INTEGER,
                'stable_tool_call_id', btrim(item->>'stable_tool_call_id'),
                'provider_call_id', NULLIF(btrim(item->>'provider_call_id'), ''),
                'tool_name', lower(btrim(item->>'tool_name')),
                'arguments_hash', encode(sha256(convert_to(
                    (item->'arguments')::TEXT, 'UTF8')), 'hex'),
                'wave', COALESCE((item->>'wave')::INTEGER, 0),
                'dependencies', COALESCE((
                    SELECT jsonb_agg(dependency ORDER BY dependency)
                    FROM jsonb_array_elements_text(
                        COALESCE(item->'dependencies', '[]'::JSONB)
                    ) dependency), '[]'::JSONB),
                'blocking', COALESCE((item->>'blocking')::BOOLEAN, TRUE),
                'policy_decision', item->>'policy_decision',
                'policy_snapshot', item->'policy_snapshot',
                'policy_revision', item->>'policy_revision',
                'retry_disposition', item->>'retry_disposition',
                'org_id', p_step.org_id, 'user_id', p_step.user_id
            )::TEXT, 'UTF8')), 'hex')
        ) ORDER BY
            (item->>'index')::INTEGER,
            btrim(item->>'stable_tool_call_id'),
            (item->>'action_id')::UUID
    ) INTO v_value
      FROM jsonb_array_elements(p_actions) item;
    RETURN v_value;
END;
$$;

CREATE FUNCTION _agent_action_batch_hash(p_canonical JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public
RETURN encode(sha256(convert_to(p_canonical::TEXT, 'UTF8')), 'hex');

CREATE FUNCTION complete_model_attempt_step_and_create_actions(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_expected_step_version BIGINT,
    p_request_hash TEXT, p_response_receipt JSONB, p_response_hash TEXT,
    p_provider_stop_reason TEXT, p_usage JSONB, p_actual_credits INTEGER,
    p_batch_hash TEXT, p_actions JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE;
    v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_settlement agent_model_credit_settlements%ROWTYPE;
    v_settlement_result JSONB;
    v_canonical JSONB;
    v_batch_hash TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;

    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs
     WHERE id = v_attempt.run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = v_attempt.model_step_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_model_attempts
     WHERE id = p_attempt_id FOR UPDATE;

    IF v_attempt.status = 'completed' THEN
        RETURN _replay_agent_action_batch(
            v_attempt, v_step, v_run, p_request_hash, p_response_receipt,
            p_response_hash, p_provider_stop_reason, p_usage,
            p_actual_credits, p_batch_hash, p_actions
        );
    END IF;

    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_run_execution_token
       OR v_attempt.execution_token IS DISTINCT FROM p_run_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp()
       OR v_attempt.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_attempt.status NOT IN ('dispatching', 'unknown')
       OR v_attempt.state_version <> p_expected_attempt_version
       OR v_step.status <> 'running'
       OR v_step.state_version <> p_expected_step_version
       OR p_response_hash !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(p_response_receipt) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_usage) IS DISTINCT FROM 'object' THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;

    v_canonical := _canonical_agent_action_batch(v_step, p_actions);
    v_batch_hash := _agent_action_batch_hash(v_canonical);
    IF p_batch_hash IS DISTINCT FROM v_batch_hash THEN
        RETURN jsonb_build_object('outcome', 'batch_hash_conflict');
    END IF;

    IF _validate_agent_action_batch(p_actions, v_canonical)
       = 'request_hash_conflict' THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;

    PERFORM 1 FROM agent_actions
     WHERE id IN (
         SELECT (item->>'action_id')::UUID FROM jsonb_array_elements(p_actions) item
     ) ORDER BY id FOR UPDATE;
    SELECT * INTO v_settlement FROM agent_model_credit_settlements
     WHERE model_step_id = v_step.id FOR UPDATE;

    v_settlement_result := _settle_agent_model_credits(
        v_step, v_attempt.id, p_response_hash, p_actual_credits
    );
    IF v_settlement_result->>'outcome' = 'terminal_conflict' THEN
        RETURN v_settlement_result;
    END IF;

    RETURN _apply_agent_tool_terminal(
        v_attempt, v_step, v_run, p_run_execution_token, p_response_receipt,
        p_response_hash, p_provider_stop_reason, p_usage, v_batch_hash,
        p_actions, v_canonical, v_settlement_result
    );
END;
$$;

REVOKE ALL ON FUNCTION
    _canonical_agent_action_batch(agent_model_steps, JSONB),
    _agent_action_batch_hash(JSONB),
    complete_model_attempt_step_and_create_actions(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT,
        JSONB, INTEGER, TEXT, JSONB
    )
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION complete_model_attempt_step_and_create_actions(
    UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT,
    JSONB, INTEGER, TEXT, JSONB
) TO everydayai_worker;

RESET ROLE;
