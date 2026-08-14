-- 227.09: Fix claim fence SQL variable/column ambiguity without changing 227.07.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION claim_ready_agent_action_snapshots_v2(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r JSONB; a RECORD; v_gate agent_runtime_tenant_gate_controls%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    FOR a IN SELECT DISTINCT action_row.org_id AS org_id
      FROM agent_actions AS action_row
      JOIN agent_runs AS run_row ON run_row.id=action_row.run_id
      WHERE action_row.status='queued' AND run_row.status IN ('running','waiting_actions')
        AND action_row.org_id IS NOT NULL
    LOOP
      PERFORM pg_advisory_xact_lock(hashtextextended(
          'agent-runtime-kill-gate:'||a.org_id::TEXT||':tenant:tenant',0));
    END LOOP;
    IF EXISTS (SELECT 1 FROM agent_actions AS action_row
      JOIN agent_runs AS run_row ON run_row.id=action_row.run_id
      JOIN agent_runtime_tenant_gate_controls AS gate_control
        ON gate_control.org_id=action_row.org_id
       AND gate_control.gate_scope='tenant' AND gate_control.scope_key='tenant'
      WHERE action_row.status='queued' AND run_row.status IN ('running','waiting_actions')
        AND gate_control.claim_blocked) THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
    END IF;
    r:=claim_ready_agent_action_snapshots(p_worker_id,p_claim_request_id,p_batch_size,p_lease_seconds);
    IF r->>'outcome'='claimed' THEN
        FOR a IN SELECT attempt_row.id FROM agent_action_attempts AS attempt_row
          WHERE attempt_row.claim_request_id=p_claim_request_id LOOP
            PERFORM _agent_runtime_record_attempt_fence(a.id);
        END LOOP;
    END IF;
    RETURN r;
END $$;

CREATE OR REPLACE FUNCTION claim_ready_agent_actions_v2(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r JSONB; a RECORD; v_gate agent_runtime_tenant_gate_controls%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    FOR a IN SELECT DISTINCT action_row.org_id AS org_id
      FROM agent_actions AS action_row
      JOIN agent_runs AS run_row ON run_row.id=action_row.run_id
      WHERE action_row.status='queued' AND run_row.status IN ('running','waiting_actions')
        AND action_row.org_id IS NOT NULL
    LOOP
      PERFORM pg_advisory_xact_lock(hashtextextended(
          'agent-runtime-kill-gate:'||a.org_id::TEXT||':tenant:tenant',0));
    END LOOP;
    IF EXISTS (SELECT 1 FROM agent_actions AS action_row
      JOIN agent_runs AS run_row ON run_row.id=action_row.run_id
      JOIN agent_runtime_tenant_gate_controls AS gate_control
        ON gate_control.org_id=action_row.org_id
       AND gate_control.gate_scope='tenant' AND gate_control.scope_key='tenant'
      WHERE action_row.status='queued' AND run_row.status IN ('running','waiting_actions')
        AND gate_control.claim_blocked) THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
    END IF;
    r:=claim_ready_agent_actions(p_worker_id,p_claim_request_id,p_batch_size,p_lease_seconds);
    IF r->>'outcome'='claimed' THEN
      FOR a IN SELECT attempt_row.id FROM agent_action_attempts AS attempt_row
        WHERE attempt_row.claim_request_id=p_claim_request_id LOOP
          PERFORM _agent_runtime_record_attempt_fence(a.id);
      END LOOP;
    END IF;
    RETURN r;
END $$;

RESET ROLE;
