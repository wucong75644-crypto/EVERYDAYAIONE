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
            'arguments_hash', item->>'arguments_hash',
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
RETURN md5(p_canonical::TEXT);

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
    v_existing_canonical JSONB;
    v_batch_hash TEXT;
    v_item JSONB;
    v_action agent_actions%ROWTYPE;
    v_event JSONB;
    v_sequences JSONB := '[]'::JSONB;
    v_action_ids JSONB := '[]'::JSONB;
    v_blockers INTEGER;
    v_distinct_count INTEGER;
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
        SELECT * INTO v_settlement FROM agent_model_credit_settlements
         WHERE model_step_id = v_step.id FOR UPDATE;
        v_canonical := _canonical_agent_action_batch(v_step, p_actions);
        v_batch_hash := _agent_action_batch_hash(v_canonical);
        SELECT jsonb_agg(
            jsonb_build_object(
                'action_id', action.id::TEXT,
                'index', action.action_index,
                'stable_tool_call_id', action.stable_tool_call_id,
                'provider_call_id', action.provider_call_id,
                'tool_name', action.tool_name,
                'arguments_hash', action.arguments_hash,
                'wave', action.wave,
                'dependencies', to_jsonb(action.dependency_ids),
                'blocking', action.blocking,
                'policy_decision', action.policy_decision,
                'policy_snapshot', action.policy_snapshot,
                'policy_revision', action.policy_revision,
                'retry_disposition', action.retry_disposition,
                'session_id', action.session_id,
                'run_id', action.run_id,
                'model_step_id', action.model_step_id,
                'org_id', action.org_id,
                'user_id', action.user_id
            ) ORDER BY action.action_index, action.stable_tool_call_id, action.id
        ) INTO v_existing_canonical
          FROM agent_actions action WHERE action.model_step_id = v_step.id;
        IF p_batch_hash IS DISTINCT FROM v_batch_hash
           OR v_existing_canonical IS DISTINCT FROM v_canonical
           OR v_attempt.request_hash IS DISTINCT FROM p_request_hash
           OR v_attempt.response_hash IS DISTINCT FROM p_response_hash
           OR v_attempt.response_receipt IS DISTINCT FROM p_response_receipt
           OR v_attempt.usage IS DISTINCT FROM p_usage
           OR v_step.status IS DISTINCT FROM 'completed'
           OR v_step.stop_reason IS DISTINCT FROM 'tool_calls'
           OR v_step.provider_stop_reason IS DISTINCT FROM p_provider_stop_reason
           OR v_settlement.status IS DISTINCT FROM 'settled'
           OR v_settlement.effective_attempt_id IS DISTINCT FROM v_attempt.id
           OR v_settlement.settled_credits IS DISTINCT FROM p_actual_credits
           OR v_settlement.response_hash IS DISTINCT FROM p_response_hash
           OR EXISTS (
               SELECT 1 FROM agent_actions action
                WHERE action.model_step_id = v_step.id
                  AND action.batch_hash IS DISTINCT FROM v_batch_hash
           )
           OR (SELECT count(*) FROM agent_actions action
                WHERE action.model_step_id = v_step.id)
              <> jsonb_array_length(p_actions) THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_completed', 'attempt_id', v_attempt.id,
            'model_step_id', v_step.id, 'run_id', v_run.id,
            'run_status', v_run.status,
            'blocking_action_count', v_run.blocking_action_count,
            'batch_hash', v_batch_hash,
            'action_ids', (SELECT jsonb_agg(id ORDER BY action_index, id)
               FROM agent_actions WHERE model_step_id = v_step.id)
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

    SELECT count(DISTINCT item->>'action_id'),
           count(*) FILTER (
               WHERE item->>'action_id' IS NULL
                  OR item->>'stable_tool_call_id' IS NULL
                  OR item->>'arguments_hash' !~ '^[0-9a-f]{32}$'
                  OR item->>'arguments_hash' IS DISTINCT FROM
                     md5((item->'arguments')::TEXT)
                  OR item->>'request_hash' !~ '^[0-9a-f]{32}$'
                  OR lower(btrim(item->>'tool_name'))
                     !~ '^[a-z][a-z0-9_.:-]{0,199}$'
                  OR jsonb_typeof(item->'arguments') IS DISTINCT FROM 'object'
                  OR jsonb_typeof(item->'policy_snapshot') IS DISTINCT FROM 'object'
                  OR NOT _agent_action_json_is_safe(item->'arguments')
                  OR NOT _agent_action_json_is_safe(item->'policy_snapshot')
                  OR item->>'policy_decision' NOT IN (
                      'preauthorized', 'requires_authorization', 'rejected')
                  OR item->>'retry_disposition' NOT IN (
                      'retry_safe', 'retry_after_reconcile',
                      'retry_requires_user', 'non_retryable', 'compensate')
           )
      INTO v_distinct_count, v_blockers
      FROM jsonb_array_elements(p_actions) item;
    IF v_distinct_count <> jsonb_array_length(p_actions) OR v_blockers <> 0
       OR (SELECT count(DISTINCT (item->>'index')::INTEGER)
             FROM jsonb_array_elements(p_actions) item)
          <> jsonb_array_length(p_actions)
       OR (SELECT count(DISTINCT btrim(item->>'stable_tool_call_id'))
             FROM jsonb_array_elements(p_actions) item)
          <> jsonb_array_length(p_actions) THEN
        RAISE EXCEPTION 'AGENT_ACTION_BATCH_INVALID' USING ERRCODE = '22023';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(p_actions) item
          CROSS JOIN LATERAL jsonb_array_elements_text(
              COALESCE(item->'dependencies', '[]'::JSONB)
          ) dependency
         WHERE dependency = item->>'action_id'
            OR NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(p_actions) candidate
                 WHERE candidate->>'action_id' = dependency
            )
    ) THEN
        RAISE EXCEPTION 'AGENT_ACTION_DEPENDENCY_INVALID' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        WITH RECURSIVE edges(parent_id, child_id) AS (
            SELECT dependency::UUID, (item->>'action_id')::UUID
              FROM jsonb_array_elements(p_actions) item
              CROSS JOIN LATERAL jsonb_array_elements_text(
                  COALESCE(item->'dependencies', '[]'::JSONB)
              ) dependency
        ), paths(origin, node) AS (
            SELECT parent_id, child_id FROM edges
            UNION
            SELECT paths.origin, edges.child_id
              FROM paths JOIN edges ON edges.parent_id = paths.node
        )
        SELECT 1 FROM paths WHERE origin = node
    ) THEN
        RAISE EXCEPTION 'AGENT_ACTION_DEPENDENCY_CYCLE' USING ERRCODE = '22023';
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

    UPDATE agent_model_attempts SET status = 'completed',
           response_receipt = p_response_receipt, response_hash = p_response_hash,
           usage = p_usage, retry_disposition = 'forbidden',
           state_version = state_version + 1, completed_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE id = v_attempt.id RETURNING * INTO v_attempt;
    UPDATE agent_model_steps SET status = 'completed',
           response_receipt = p_response_receipt, stop_reason = 'tool_calls',
           provider_stop_reason = p_provider_stop_reason,
           input_tokens = COALESCE((p_usage->>'input_tokens')::BIGINT, 0),
           output_tokens = COALESCE((p_usage->>'output_tokens')::BIGINT, 0),
           reasoning_tokens = COALESCE((p_usage->>'reasoning_tokens')::BIGINT, 0),
           state_version = state_version + 1, completed_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE id = v_step.id RETURNING * INTO v_step;

    FOR v_item IN SELECT item FROM jsonb_array_elements(p_actions) item
                   ORDER BY (item->>'index')::INTEGER,
                            btrim(item->>'stable_tool_call_id'),
                            (item->>'action_id')::UUID
    LOOP
        INSERT INTO agent_actions(
            id, session_id, run_id, model_step_id, org_id, user_id,
            action_index, stable_tool_call_id, provider_call_id, tool_name,
            arguments, arguments_hash, request_hash, batch_hash, wave,
            dependency_ids, blocking, policy_decision, policy_snapshot,
            policy_revision, retry_disposition, status
        ) VALUES (
            (v_item->>'action_id')::UUID, v_step.session_id, v_step.run_id,
            v_step.id, v_step.org_id, v_step.user_id,
            (v_item->>'index')::INTEGER, btrim(v_item->>'stable_tool_call_id'),
            NULLIF(btrim(v_item->>'provider_call_id'), ''),
            lower(btrim(v_item->>'tool_name')), v_item->'arguments',
            v_item->>'arguments_hash', v_item->>'request_hash', v_batch_hash,
            COALESCE((v_item->>'wave')::INTEGER, 0),
            ARRAY(SELECT value::UUID FROM jsonb_array_elements_text(
                COALESCE(v_item->'dependencies', '[]'::JSONB)) value ORDER BY value),
            COALESCE((v_item->>'blocking')::BOOLEAN, TRUE),
            v_item->>'policy_decision', v_item->'policy_snapshot',
            v_item->>'policy_revision', v_item->>'retry_disposition', 'requested'
        ) RETURNING * INTO v_action;
        UPDATE agent_actions SET
               status = CASE policy_decision
                   WHEN 'preauthorized' THEN 'queued'
                   WHEN 'requires_authorization' THEN 'awaiting_authorization'
                   ELSE 'rejected' END,
               state_version = state_version + 1,
               completed_at = CASE WHEN policy_decision = 'rejected'
                   THEN clock_timestamp() ELSE NULL END,
               updated_at = clock_timestamp()
         WHERE id = v_action.id RETURNING * INTO v_action;
        v_action_ids := v_action_ids || to_jsonb(v_action.id);
    END LOOP;

    SELECT count(*) INTO v_blockers FROM agent_actions
     WHERE model_step_id = v_step.id AND blocking
       AND status NOT IN ('completed', 'failed', 'rejected', 'cancelled');
    UPDATE agent_runs SET
           blocking_action_count = blocking_action_count + v_blockers,
           status = CASE WHEN v_blockers > 0 THEN 'waiting_actions' ELSE status END,
           execution_token = CASE WHEN v_blockers > 0 THEN NULL ELSE execution_token END,
           lease_expires_at = CASE WHEN v_blockers > 0 THEN NULL ELSE lease_expires_at END,
           state_version = state_version + CASE WHEN v_blockers > 0 THEN 1 ELSE 0 END,
           updated_at = clock_timestamp()
     WHERE id = v_run.id RETURNING * INTO v_run;
    IF v_blockers > 0 THEN
        UPDATE agent_run_attempts SET ended_at = clock_timestamp(), outcome = 'completed'
         WHERE run_id = v_run.id AND execution_token = p_run_execution_token
           AND ended_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'AGENT_RUN_ATTEMPT_CLOSE_MISSING' USING ERRCODE = '55000';
        END IF;
        v_event := append_agent_runtime_event(
            v_run.session_id, 'run.waiting', v_run.id, v_step.id,
            p_run_execution_token, 'system', session_user,
            jsonb_build_object('status', 'waiting_actions',
                               'blocking_action_count', v_run.blocking_action_count),
            ARRAY['web_runtime', 'audit']::TEXT[]);
        v_sequences := v_sequences || (v_event->'event_sequence');
    END IF;
    v_event := append_agent_runtime_event(
        v_step.session_id, 'model_step.completed', v_step.run_id, v_step.id,
        p_run_execution_token, 'model', session_user,
        jsonb_build_object('stop_reason', 'tool_calls',
                           'attempt_id', v_attempt.id, 'batch_hash', v_batch_hash),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    v_sequences := v_sequences || (v_event->'event_sequence');
    FOR v_action IN SELECT * FROM agent_actions
                     WHERE model_step_id = v_step.id
                     ORDER BY action_index, stable_tool_call_id, id
    LOOP
        v_event := append_agent_runtime_event(
            v_action.session_id, 'action.requested', v_action.run_id,
            v_action.model_step_id, v_action.id, 'model', session_user,
            jsonb_build_object(
                'action_id', v_action.id, 'tool_name', v_action.tool_name,
                'status', v_action.status, 'blocking', v_action.blocking),
            ARRAY['web_runtime', 'audit']::TEXT[]);
        v_sequences := v_sequences || (v_event->'event_sequence');
    END LOOP;
    RETURN jsonb_build_object(
        'outcome', 'completed', 'attempt_id', v_attempt.id,
        'model_step_id', v_step.id, 'run_id', v_run.id,
        'run_status', v_run.status,
        'blocking_action_count', v_run.blocking_action_count,
        'batch_hash', v_batch_hash, 'action_ids', v_action_ids,
        'event_sequences', v_sequences,
        'settlement_outcome', v_settlement_result->>'outcome'
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
