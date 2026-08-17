-- Restore the pre-230.11 claim behavior.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION claim_ready_agent_actions(
    p_worker_id TEXT, p_claim_request_id TEXT,
    p_batch_size INTEGER DEFAULT 10,
    p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_rows JSONB;
    v_batch agent_action_claim_batches%ROWTYPE;
    v_created BOOLEAN := FALSE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NULLIF(btrim(p_worker_id), '') IS NULL
       OR NULLIF(btrim(p_claim_request_id), '') IS NULL
       OR length(btrim(p_claim_request_id)) > 200
       OR p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
        RAISE EXCEPTION 'AGENT_ACTION_CLAIM_INVALID' USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_action_claim_batches(
        claim_request_id, worker_id, batch_size, lease_seconds
    ) VALUES (
        btrim(p_claim_request_id), btrim(p_worker_id),
        p_batch_size, p_lease_seconds
    ) ON CONFLICT DO NOTHING RETURNING * INTO v_batch;
    v_created := FOUND;
    IF NOT v_created THEN
        SELECT * INTO v_batch FROM agent_action_claim_batches
         WHERE claim_request_id = btrim(p_claim_request_id) FOR UPDATE;
        IF v_batch.worker_id IS DISTINCT FROM btrim(p_worker_id)
           OR v_batch.batch_size IS DISTINCT FROM p_batch_size
           OR v_batch.lease_seconds IS DISTINCT FROM p_lease_seconds THEN
            RETURN jsonb_build_object('outcome', 'claim_request_conflict');
        END IF;
        SELECT COALESCE(jsonb_agg(to_jsonb(attempt) ORDER BY claimed_at, id), '[]')
          INTO v_rows FROM agent_action_attempts attempt
         WHERE attempt.claim_request_id = v_batch.claim_request_id;
        RETURN jsonb_build_object('outcome', 'claimed', 'attempts', v_rows);
    END IF;
    WITH candidates AS (
        SELECT action.id
          FROM agent_actions action
          JOIN agent_runs run ON run.id = action.run_id
         WHERE action.status = 'queued'
           AND action.policy_decision = 'preauthorized'
           AND run.status IN ('running', 'waiting_actions')
           AND NOT EXISTS (
               SELECT 1 FROM unnest(action.dependency_ids) dependency_id
               LEFT JOIN agent_actions dependency ON dependency.id = dependency_id
               LEFT JOIN agent_action_results result
                      ON result.action_id = dependency.id
                WHERE dependency.id IS NULL OR result.action_id IS NULL
           )
         ORDER BY action.created_at, action.id
         FOR UPDATE OF action SKIP LOCKED LIMIT p_batch_size
    ), updated AS (
        UPDATE agent_actions action SET status = 'running',
               started_at = COALESCE(started_at, clock_timestamp()),
               state_version = state_version + 1,
               updated_at = clock_timestamp()
          FROM candidates WHERE action.id = candidates.id
        RETURNING action.*
    ), attempts AS (
        INSERT INTO agent_action_attempts(
            action_id, session_id, run_id, org_id, user_id, attempt_number,
            status, dispatch_phase, worker_id, claim_request_id, execution_token,
            lease_expires_at, idempotency_key, request_hash, retry_disposition
        )
        SELECT action.id, action.session_id, action.run_id, action.org_id,
               action.user_id,
               COALESCE((SELECT max(old.attempt_number)
                           FROM agent_action_attempts old
                          WHERE old.action_id = action.id), 0) + 1,
               'claimed', 'claimed', btrim(p_worker_id),
               v_batch.claim_request_id, gen_random_uuid(),
               clock_timestamp() + make_interval(secs => p_lease_seconds),
               'action:' || action.id::TEXT || ':attempt:' ||
               (COALESCE((SELECT max(old.attempt_number)
                            FROM agent_action_attempts old
                           WHERE old.action_id = action.id), 0) + 1)::TEXT,
               action.request_hash, action.retry_disposition
          FROM updated action
        RETURNING *
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(attempts) ORDER BY claimed_at, id), '[]')
      INTO v_rows FROM attempts;
    RETURN jsonb_build_object('outcome', 'claimed', 'attempts', v_rows);
END;
$$;

CREATE OR REPLACE FUNCTION claim_agent_action_dispatch_final_v1(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE existing JSONB; result JSONB; org RECORD;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 existing:=get_agent_action_snapshot_batch(p_worker_id,p_claim_request_id);
 IF existing->>'outcome'='found' THEN
  RETURN jsonb_build_object('outcome','claimed','snapshots',existing->'snapshots');
 END IF;
 PERFORM _recover_expired_agent_action_claims_v1(p_worker_id,3);
 FOR org IN SELECT DISTINCT action.org_id
  FROM agent_actions action JOIN agent_runs run ON run.id=action.run_id
  WHERE action.status='queued' AND run.status IN('running','waiting_actions')
    AND action.org_id IS NOT NULL
 LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended(
   'agent-runtime-kill-gate:'||org.org_id::TEXT||':tenant:tenant',0));
 END LOOP;
 IF EXISTS(SELECT 1 FROM agent_actions action
  JOIN agent_runs run ON run.id=action.run_id
  JOIN agent_runtime_tenant_gate_controls gate
    ON gate.org_id=action.org_id AND gate.gate_scope='tenant'
   AND gate.scope_key='tenant'
  WHERE action.status='queued' AND run.status IN('running','waiting_actions')
    AND gate.claim_blocked) THEN
  RETURN jsonb_build_object(
   'outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
 END IF;
 result:=claim_ready_agent_action_snapshots(
  p_worker_id,p_claim_request_id,p_batch_size,p_lease_seconds);
 IF result->>'outcome'='claimed' THEN
  FOR org IN SELECT attempt.id FROM agent_action_attempts attempt
   WHERE attempt.claim_request_id=p_claim_request_id
  LOOP
   PERFORM _agent_runtime_record_attempt_fence(org.id);
  END LOOP;
 END IF;
 RETURN result;
END $$;

RESET ROLE;
