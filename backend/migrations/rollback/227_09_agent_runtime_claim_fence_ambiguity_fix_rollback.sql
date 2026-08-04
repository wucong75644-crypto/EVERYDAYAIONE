-- Restore the 227.07 claim wrappers exactly; this rollback does not remove facts.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION claim_ready_agent_action_snapshots_v2(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r JSONB; a RECORD; g agent_runtime_tenant_gate_controls%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    FOR a IN SELECT DISTINCT x.org_id AS org_id
      FROM agent_actions x JOIN agent_runs run ON run.id=x.run_id
      WHERE x.status='queued' AND run.status IN ('running','waiting_actions') AND x.org_id IS NOT NULL
    LOOP
      PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||a.org_id::TEXT||':tenant:tenant',0));
    END LOOP;
    IF EXISTS (SELECT 1 FROM agent_actions x JOIN agent_runs run ON run.id=x.run_id
      JOIN agent_runtime_tenant_gate_controls g ON g.org_id=x.org_id
       AND g.gate_scope='tenant' AND g.scope_key='tenant'
      WHERE x.status='queued' AND run.status IN ('running','waiting_actions') AND g.claim_blocked) THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
    END IF;
    r:=claim_ready_agent_action_snapshots(p_worker_id,p_claim_request_id,p_batch_size,p_lease_seconds);
    IF r->>'outcome'='claimed' THEN
        FOR a IN SELECT id FROM agent_action_attempts WHERE claim_request_id=p_claim_request_id LOOP
            PERFORM _agent_runtime_record_attempt_fence(a.id);
        END LOOP;
    END IF;
    RETURN r;
END $$;

CREATE OR REPLACE FUNCTION claim_ready_agent_actions_v2(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r JSONB; a RECORD;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    FOR a IN SELECT DISTINCT x.org_id AS org_id
      FROM agent_actions x JOIN agent_runs run ON run.id=x.run_id
      WHERE x.status='queued' AND run.status IN ('running','waiting_actions') AND x.org_id IS NOT NULL
    LOOP
      PERFORM pg_advisory_xact_lock(hashtextextended('agent-runtime-kill-gate:'||a.org_id::TEXT||':tenant:tenant',0));
    END LOOP;
    IF EXISTS (SELECT 1 FROM agent_actions x JOIN agent_runs run ON run.id=x.run_id
      JOIN agent_runtime_tenant_gate_controls g ON g.org_id=x.org_id
       AND g.gate_scope='tenant' AND g.scope_key='tenant'
      WHERE x.status='queued' AND run.status IN ('running','waiting_actions') AND g.claim_blocked) THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
    END IF;
    r:=claim_ready_agent_actions(p_worker_id,p_claim_request_id,p_batch_size,p_lease_seconds);
    IF r->>'outcome'='claimed' THEN
      FOR a IN SELECT id FROM agent_action_attempts WHERE claim_request_id=p_claim_request_id LOOP PERFORM _agent_runtime_record_attempt_fence(a.id); END LOOP;
    END IF;
    RETURN r;
END $$;

RESET ROLE;
