-- 220_04: Typed Action dispatch snapshots and reconciliation discovery.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_action_dispatch_snapshot(p_attempt agent_action_attempts)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT to_jsonb(p_attempt) || jsonb_build_object(
        'action', to_jsonb(action))
      FROM agent_actions action WHERE action.id = p_attempt.action_id
$$;

CREATE FUNCTION claim_ready_agent_action_snapshots(
    p_worker_id TEXT, p_claim_request_id TEXT,
    p_batch_size INTEGER DEFAULT 10, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_claim JSONB; v_attempt JSONB; v_rows JSONB := '[]'::JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NOT EXISTS (
        SELECT 1 FROM agent_actions action
        JOIN agent_runs run ON run.id = action.run_id
        WHERE action.status = 'queued'
          AND action.policy_decision = 'preauthorized'
          AND run.status IN ('running', 'waiting_actions')
          AND NOT EXISTS (
              SELECT 1 FROM unnest(action.dependency_ids) dependency_id
              LEFT JOIN agent_actions dependency
                     ON dependency.id = dependency_id
              LEFT JOIN agent_action_results result
                     ON result.action_id = dependency.id
              WHERE dependency.id IS NULL OR result.action_id IS NULL
          )
    ) THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    v_claim := claim_ready_agent_actions(
        p_worker_id, p_claim_request_id, p_batch_size, p_lease_seconds);
    IF v_claim->>'outcome' <> 'claimed' THEN RETURN v_claim; END IF;
    FOR v_attempt IN SELECT value FROM jsonb_array_elements(v_claim->'attempts')
    LOOP
        v_rows := v_rows || jsonb_build_array(
            _agent_action_dispatch_snapshot(
                (SELECT attempt FROM agent_action_attempts attempt
                  WHERE attempt.id = (v_attempt->>'id')::UUID)));
    END LOOP;
    RETURN jsonb_build_object('outcome', 'claimed', 'snapshots', v_rows);
END;
$$;

CREATE FUNCTION get_agent_action_snapshot_batch(
    p_worker_id TEXT, p_claim_request_id TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_claim JSONB; v_attempt JSONB; v_rows JSONB := '[]'::JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    v_claim := get_agent_action_claim_batch(p_worker_id, p_claim_request_id);
    IF v_claim->>'outcome' <> 'found' THEN RETURN v_claim; END IF;
    FOR v_attempt IN SELECT value FROM jsonb_array_elements(v_claim->'attempts')
    LOOP
        v_rows := v_rows || jsonb_build_array(
            _agent_action_dispatch_snapshot(
                (SELECT attempt FROM agent_action_attempts attempt
                  WHERE attempt.id = (v_attempt->>'id')::UUID)));
    END LOOP;
    RETURN jsonb_build_object('outcome', 'found', 'snapshots', v_rows);
END;
$$;

CREATE FUNCTION claim_next_agent_action_reconciliation(
    p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120,
    p_min_age_seconds INTEGER DEFAULT 30
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_candidate RECORD; v_claim JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_min_age_seconds NOT BETWEEN 0 AND 86400 THEN
        RAISE EXCEPTION 'AGENT_ACTION_RECONCILE_SCAN_INVALID'
            USING ERRCODE = '22023';
    END IF;
    FOR v_candidate IN
        SELECT attempt.id, attempt.state_version
          FROM agent_action_attempts attempt
         WHERE attempt.status IN ('accepted', 'unknown')
           AND (attempt.reconciliation_token IS NULL
                OR attempt.reconciliation_lease_expires_at <= clock_timestamp())
           AND attempt.updated_at <= clock_timestamp()
               - make_interval(secs => p_min_age_seconds)
         ORDER BY attempt.updated_at, attempt.id
         LIMIT 100
    LOOP
        v_claim := claim_agent_action_reconciliation(
            v_candidate.id, v_candidate.state_version,
            p_worker_id, p_lease_seconds);
        IF v_claim->>'outcome' = 'claimed' THEN
            RETURN v_claim || jsonb_build_object(
                'snapshot', _agent_action_dispatch_snapshot(
                    (SELECT attempt FROM agent_action_attempts attempt
                      WHERE attempt.id = v_candidate.id)));
        END IF;
    END LOOP;
    RETURN jsonb_build_object('outcome', 'not_found');
END;
$$;

CREATE FUNCTION get_claimed_agent_action_reconciliation(p_worker_id TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE worker_id = BTRIM(p_worker_id)
       AND status IN ('accepted', 'unknown')
       AND reconciliation_token IS NOT NULL
       AND reconciliation_lease_expires_at > clock_timestamp()
     ORDER BY updated_at DESC, id LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'attempt_id', v_attempt.id,
        'execution_token', v_attempt.reconciliation_token,
        'lease_expires_at', v_attempt.reconciliation_lease_expires_at,
        'state_version', v_attempt.state_version,
        'snapshot', _agent_action_dispatch_snapshot(v_attempt));
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_action_dispatch_snapshot(agent_action_attempts),
    claim_ready_agent_action_snapshots(TEXT, TEXT, INTEGER, INTEGER),
    get_agent_action_snapshot_batch(TEXT, TEXT),
    claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER),
    get_claimed_agent_action_reconciliation(TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    claim_ready_agent_action_snapshots(TEXT, TEXT, INTEGER, INTEGER),
    get_agent_action_snapshot_batch(TEXT, TEXT),
    claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER),
    get_claimed_agent_action_reconciliation(TEXT)
TO everydayai_worker;

RESET ROLE;
