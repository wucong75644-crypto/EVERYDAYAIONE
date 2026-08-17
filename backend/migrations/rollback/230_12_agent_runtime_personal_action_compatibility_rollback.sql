-- Restore the 230.11 organization-only Runtime claim compatibility.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _agent_runtime_kill_epoch_context(
    p_attempt_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_expected_state_version BIGINT, p_mode TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    a agent_action_attempts%ROWTYPE;
    x agent_actions%ROWTYPE;
    f agent_runtime_owner_fences%ROWTYPE;
    g agent_runtime_tenant_gate_controls%ROWTYPE;
    v_provider TEXT;
    v_capability TEXT;
    v_provider_epoch BIGINT := 0;
    v_capability_epoch BIGINT := 0;
    v_provider_revision TEXT;
    v_capability_revision TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_mode NOT IN ('dispatch','lease','cleanup','receipt','reconcile','recovery') THEN
        RAISE EXCEPTION 'RUNTIME_KILL_FENCE_MODE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    SELECT * INTO x FROM agent_actions WHERE id=a.action_id;
    IF x.id IS NULL OR x.org_id IS NULL THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_TENANT_SCOPE_REQUIRED');
    END IF;
    IF p_mode NOT IN ('reconcile','recovery') AND a.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_EXECUTION_TOKEN_FENCED');
    END IF;
    IF p_mode NOT IN ('reconcile','recovery') AND a.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_REQUEST_HASH_FENCED');
    END IF;
    IF p_mode NOT IN ('reconcile','receipt','cleanup')
       AND a.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_STATE_VERSION_FENCED');
    END IF;
    SELECT * INTO f FROM agent_runtime_owner_fences
     WHERE owner_kind='attempt' AND owner_id=a.id
       AND execution_token=a.execution_token FOR UPDATE;
    IF p_mode <> 'reconcile' AND NOT FOUND THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_OWNER_FENCE_MISSING');
    END IF;
    IF p_mode='reconcile' THEN
        RETURN jsonb_build_object('outcome','reconcile_only');
    END IF;
    v_provider := NULLIF(btrim(COALESCE(x.policy_snapshot->>'provider',x.policy_snapshot->>'provider_name')),'');
    v_capability := NULLIF(btrim(COALESCE(x.policy_snapshot->>'capability',x.policy_snapshot->>'capability_name')),'');
    v_provider_revision := NULLIF(btrim(x.policy_snapshot->>'provider_revision'),'');
    v_capability_revision := NULLIF(btrim(x.policy_snapshot->>'capability_revision'),'');
    IF v_capability IS NULL AND jsonb_typeof(x.policy_snapshot->'capability_requirements')='array' THEN
        v_capability := NULLIF(btrim(x.policy_snapshot->'capability_requirements'->>0),'');
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'agent-runtime-kill-gate:'||x.org_id::TEXT||':tenant:tenant',0));
    SELECT * INTO g FROM agent_runtime_tenant_gate_controls
     WHERE org_id=x.org_id AND gate_scope='tenant' AND scope_key='tenant';
    IF FOUND AND g.dispatch_blocked AND p_mode NOT IN ('cleanup','receipt') THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
    END IF;
    IF FOUND AND f.tenant_kill_epoch <> g.kill_epoch
       AND p_mode NOT IN ('receipt','cleanup') THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_KILL_EPOCH_FENCED');
    END IF;
    IF v_provider IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'agent-runtime-kill-gate:'||x.org_id::TEXT||':provider:'||v_provider,0));
        SELECT * INTO g FROM agent_runtime_tenant_gate_controls
         WHERE org_id=x.org_id AND gate_scope='provider' AND scope_key=v_provider;
        IF FOUND THEN
            v_provider_epoch := g.kill_epoch;
            IF g.dispatch_blocked AND p_mode NOT IN ('cleanup','receipt') THEN
                RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_PROVIDER_KILL_FENCED');
            END IF;
        END IF;
    END IF;
    IF v_capability IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'agent-runtime-kill-gate:'||x.org_id::TEXT||':capability:'||v_capability,0));
        SELECT * INTO g FROM agent_runtime_tenant_gate_controls
         WHERE org_id=x.org_id AND gate_scope='capability' AND scope_key=v_capability;
        IF FOUND THEN
            v_capability_epoch := g.kill_epoch;
            IF g.dispatch_blocked AND p_mode NOT IN ('cleanup','receipt') THEN
                RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_CAPABILITY_KILL_FENCED');
            END IF;
        END IF;
    END IF;
    IF p_mode NOT IN ('receipt','cleanup')
       AND (f.provider_revision IS DISTINCT FROM v_provider_revision
       OR f.capability_revision IS DISTINCT FROM v_capability_revision) THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_REVISION_FENCED');
    END IF;
    IF v_provider IS NOT NULL AND f.provider_kill_epoch <> v_provider_epoch
       AND p_mode NOT IN ('receipt','cleanup') THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_PROVIDER_KILL_FENCED');
    END IF;
    IF v_capability IS NOT NULL AND f.capability_kill_epoch <> v_capability_epoch
       AND p_mode NOT IN ('receipt','cleanup') THEN
        RETURN jsonb_build_object('outcome','fenced','error_code','RUNTIME_CAPABILITY_KILL_FENCED');
    END IF;
    RETURN jsonb_build_object('outcome','allowed');
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_record_attempt_fence(p_attempt_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; x agent_actions%ROWTYPE; g agent_runtime_tenant_gate_controls%ROWTYPE;
    v_provider TEXT; v_capability TEXT; v_tenant_epoch BIGINT:=0; v_provider_epoch BIGINT:=0; v_capability_epoch BIGINT:=0;
    v_provider_revision TEXT; v_capability_revision TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id;
    SELECT * INTO x FROM agent_actions WHERE id=a.action_id;
    IF a.id IS NULL OR x.id IS NULL OR x.org_id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_TENANT_SCOPE_REQUIRED' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'agent-runtime-kill-gate:'||x.org_id::TEXT||':tenant:tenant',0));
    SELECT * INTO g FROM agent_runtime_tenant_gate_controls
     WHERE org_id=x.org_id AND gate_scope='tenant' AND scope_key='tenant';
    v_tenant_epoch := COALESCE(g.kill_epoch,0);
    v_provider := NULLIF(btrim(COALESCE(x.policy_snapshot->>'provider',x.policy_snapshot->>'provider_name')),'');
    v_capability := NULLIF(btrim(COALESCE(x.policy_snapshot->>'capability',x.policy_snapshot->>'capability_name')),'');
    v_provider_revision := NULLIF(btrim(x.policy_snapshot->>'provider_revision'),'');
    v_capability_revision := NULLIF(btrim(x.policy_snapshot->>'capability_revision'),'');
    IF v_capability IS NULL AND jsonb_typeof(x.policy_snapshot->'capability_requirements')='array' THEN
        v_capability := NULLIF(btrim(x.policy_snapshot->'capability_requirements'->>0),'');
    END IF;
    IF v_provider IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'agent-runtime-kill-gate:'||x.org_id::TEXT||':provider:'||v_provider,0));
        SELECT * INTO g FROM agent_runtime_tenant_gate_controls
         WHERE org_id=x.org_id AND gate_scope='provider' AND scope_key=v_provider;
        IF FOUND THEN v_provider_epoch:=g.kill_epoch; END IF;
    END IF;
    IF v_capability IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'agent-runtime-kill-gate:'||x.org_id::TEXT||':capability:'||v_capability,0));
        SELECT * INTO g FROM agent_runtime_tenant_gate_controls
         WHERE org_id=x.org_id AND gate_scope='capability' AND scope_key=v_capability;
        IF FOUND THEN v_capability_epoch:=g.kill_epoch; END IF;
    END IF;
    INSERT INTO agent_runtime_owner_fences(
        owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,
        provider_kill_epoch,capability_kill_epoch,provider_revision,
        capability_revision,state_version,lease_expires_at,status)
    SELECT 'attempt',a.id,x.org_id,a.execution_token,v_tenant_epoch,
       v_provider_epoch,v_capability_epoch,v_provider_revision,v_capability_revision,
       a.state_version,a.lease_expires_at,'active'
    ON CONFLICT(owner_kind,owner_id,execution_token) DO UPDATE SET
       state_version=EXCLUDED.state_version,lease_expires_at=EXCLUDED.lease_expires_at,
       updated_at=clock_timestamp();
END $$;

CREATE OR REPLACE FUNCTION claim_agent_action_dispatch_final_v1(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE existing JSONB; result JSONB; org RECORD;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 PERFORM set_config('app.agent_runtime_claim_scope','tenant',TRUE);
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
