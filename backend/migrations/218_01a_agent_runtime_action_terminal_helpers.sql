-- 218_01a: Owner-only helpers used by the Tool terminal transaction.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _replay_agent_action_batch(
    p_attempt agent_model_attempts, p_step agent_model_steps, p_run agent_runs,
    p_request_hash TEXT, p_response_receipt JSONB, p_response_hash TEXT,
    p_provider_stop_reason TEXT, p_usage JSONB, p_actual_credits INTEGER,
    p_batch_hash TEXT, p_actions JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_settlement agent_model_credit_settlements%ROWTYPE;
    v_canonical JSONB;
    v_existing JSONB;
    v_hash TEXT;
BEGIN
    v_canonical := _canonical_agent_action_batch(p_step, p_actions);
    v_hash := _agent_action_batch_hash(v_canonical);
    PERFORM 1 FROM agent_actions WHERE model_step_id = p_step.id
     ORDER BY id FOR UPDATE;
    SELECT * INTO v_settlement FROM agent_model_credit_settlements
     WHERE model_step_id = p_step.id FOR UPDATE;
    SELECT jsonb_agg(jsonb_build_object(
        'action_id', action.id::TEXT, 'index', action.action_index,
        'stable_tool_call_id', action.stable_tool_call_id,
        'provider_call_id', action.provider_call_id,
        'tool_name', action.tool_name, 'arguments_hash', action.arguments_hash,
        'request_hash', action.request_hash, 'wave', action.wave,
        'dependencies', to_jsonb(action.dependency_ids),
        'blocking', action.blocking, 'policy_decision', action.policy_decision,
        'policy_snapshot', action.policy_snapshot,
        'policy_revision', action.policy_revision,
        'retry_disposition', action.retry_disposition,
        'session_id', action.session_id, 'run_id', action.run_id,
        'model_step_id', action.model_step_id, 'org_id', action.org_id,
        'user_id', action.user_id
    ) ORDER BY action.action_index, action.stable_tool_call_id, action.id)
      INTO v_existing FROM agent_actions action
     WHERE action.model_step_id = p_step.id;
    IF p_batch_hash IS DISTINCT FROM v_hash
       OR v_existing IS DISTINCT FROM v_canonical
       OR p_attempt.request_hash IS DISTINCT FROM p_request_hash
       OR p_attempt.response_hash IS DISTINCT FROM p_response_hash
       OR p_attempt.response_receipt IS DISTINCT FROM p_response_receipt
       OR p_attempt.usage IS DISTINCT FROM p_usage
       OR p_step.status IS DISTINCT FROM 'completed'
       OR p_step.stop_reason IS DISTINCT FROM 'tool_calls'
       OR p_step.provider_stop_reason IS DISTINCT FROM p_provider_stop_reason
       OR v_settlement.status IS DISTINCT FROM 'settled'
       OR v_settlement.effective_attempt_id IS DISTINCT FROM p_attempt.id
       OR v_settlement.settled_credits IS DISTINCT FROM p_actual_credits
       OR v_settlement.response_hash IS DISTINCT FROM p_response_hash
       OR EXISTS (SELECT 1 FROM agent_actions action
           WHERE action.model_step_id = p_step.id
             AND action.batch_hash IS DISTINCT FROM v_hash)
       OR (SELECT count(*) FROM agent_actions action
           WHERE action.model_step_id = p_step.id)
          <> jsonb_array_length(p_actions) THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'already_completed', 'attempt_id', p_attempt.id,
        'model_step_id', p_step.id, 'run_id', p_run.id,
        'run_status', p_run.status,
        'blocking_action_count', p_run.blocking_action_count,
        'batch_hash', v_hash,
        'action_ids', (SELECT jsonb_agg(id ORDER BY action_index, id)
            FROM agent_actions WHERE model_step_id = p_step.id));
END;
$$;

CREATE FUNCTION _validate_agent_action_batch(
    p_actions JSONB, p_canonical JSONB
) RETURNS TEXT LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_distinct INTEGER; v_invalid INTEGER;
BEGIN
    SELECT count(DISTINCT item->>'action_id'), count(*) FILTER (WHERE
        item->>'action_id' IS NULL OR item->>'stable_tool_call_id' IS NULL
        OR lower(btrim(item->>'tool_name')) !~ '^[a-z][a-z0-9_.:-]{0,199}$'
        OR jsonb_typeof(item->'arguments') IS DISTINCT FROM 'object'
        OR jsonb_typeof(item->'policy_snapshot') IS DISTINCT FROM 'object'
        OR NOT _agent_action_json_is_safe(item->'arguments')
        OR NOT _agent_action_json_is_safe(item->'policy_snapshot')
        OR item->>'policy_decision' NOT IN (
            'preauthorized', 'requires_authorization', 'rejected')
        OR item->>'retry_disposition' NOT IN (
            'retry_safe', 'retry_after_reconcile', 'retry_requires_user',
            'non_retryable', 'compensate'))
      INTO v_distinct, v_invalid FROM jsonb_array_elements(p_actions) item;
    IF v_distinct <> jsonb_array_length(p_actions) OR v_invalid <> 0
       OR (SELECT count(DISTINCT (item->>'index')::INTEGER)
           FROM jsonb_array_elements(p_actions) item)
          <> jsonb_array_length(p_actions)
       OR (SELECT count(DISTINCT btrim(item->>'stable_tool_call_id'))
           FROM jsonb_array_elements(p_actions) item)
          <> jsonb_array_length(p_actions) THEN
        RAISE EXCEPTION 'AGENT_ACTION_BATCH_INVALID' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_actions) supplied
        JOIN jsonb_array_elements(p_canonical) canonical
          ON canonical->>'action_id' = supplied->>'action_id'
        WHERE supplied->>'arguments_hash' !~ '^[0-9a-f]{64}$'
           OR supplied->>'request_hash' !~ '^[0-9a-f]{64}$'
           OR supplied->>'arguments_hash'
              IS DISTINCT FROM canonical->>'arguments_hash'
           OR supplied->>'request_hash'
              IS DISTINCT FROM canonical->>'request_hash'
           OR (supplied ? 'session_id' AND supplied->>'session_id'
               IS DISTINCT FROM canonical->>'session_id')
           OR (supplied ? 'run_id' AND supplied->>'run_id'
               IS DISTINCT FROM canonical->>'run_id')
           OR (supplied ? 'model_step_id' AND supplied->>'model_step_id'
               IS DISTINCT FROM canonical->>'model_step_id')
           OR (supplied ? 'org_id' AND supplied->>'org_id'
               IS DISTINCT FROM canonical->>'org_id')
           OR (supplied ? 'user_id' AND supplied->>'user_id'
               IS DISTINCT FROM canonical->>'user_id')) THEN
        RETURN 'request_hash_conflict';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_actions) item
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(item->'dependencies', '[]')) dependency
        WHERE dependency = item->>'action_id' OR NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_actions) candidate
            WHERE candidate->>'action_id' = dependency)) THEN
        RAISE EXCEPTION 'AGENT_ACTION_DEPENDENCY_INVALID' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_actions) item
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(item->'dependencies', '[]')) dependency
        JOIN jsonb_array_elements(p_actions) depended
          ON depended->>'action_id' = dependency
        WHERE item->>'policy_decision' <> 'rejected'
          AND depended->>'policy_decision' = 'rejected') THEN
        RAISE EXCEPTION 'AGENT_ACTION_REJECTED_DEPENDENCY' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (WITH RECURSIVE edges(parent_id, child_id) AS (
        SELECT dependency::UUID, (item->>'action_id')::UUID
        FROM jsonb_array_elements(p_actions) item
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(item->'dependencies', '[]')) dependency
    ), paths(origin, node) AS (
        SELECT parent_id, child_id FROM edges UNION
        SELECT paths.origin, edges.child_id FROM paths
        JOIN edges ON edges.parent_id = paths.node
    ) SELECT 1 FROM paths WHERE origin = node) THEN
        RAISE EXCEPTION 'AGENT_ACTION_DEPENDENCY_CYCLE' USING ERRCODE = '22023';
    END IF;
    RETURN 'valid';
END;
$$;

REVOKE ALL ON FUNCTION
    _replay_agent_action_batch(
        agent_model_attempts, agent_model_steps, agent_runs,
        TEXT, JSONB, TEXT, TEXT, JSONB, INTEGER, TEXT, JSONB),
    _validate_agent_action_batch(JSONB, JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
