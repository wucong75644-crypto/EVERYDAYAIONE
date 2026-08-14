-- 227.07: Runtime ingress/claim/dispatch/lease kill-epoch fences.
-- Additive wrappers only. 227.02 through 227.06 remain immutable.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_kill_epoch_context(
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

CREATE FUNCTION _agent_runtime_record_attempt_fence(p_attempt_id UUID)
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

CREATE FUNCTION runtime_submit_ingress_v4(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,
 p_created_by_user_id UUID,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_agent_definition_hash TEXT,p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,
 p_through_message_id UUID,p_base_context_revision TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_config_snapshot JSONB,p_capability_snapshot JSONB,
 p_release_revision TEXT,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g agent_runtime_tenant_gate_controls%ROWTYPE; r JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_org_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'agent-runtime-kill-gate:'||p_org_id::TEXT||':tenant:tenant',0));
    END IF;
    SELECT * INTO g FROM agent_runtime_tenant_gate_controls
     WHERE org_id=p_org_id AND gate_scope='tenant' AND scope_key='tenant';
    IF FOUND AND g.ingress_blocked THEN
        RETURN jsonb_build_object('outcome','ingress_disabled','error_code','RUNTIME_KILL_EPOCH_FENCED','ingress_version',4);
    END IF;
    r:=runtime_submit_ingress_v3(p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
      p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision,p_agent_definition_hash,
      p_command_type,p_idempotency_key,p_channel,p_through_message_id,p_base_context_revision,
      p_effective_toolset_revision,p_effective_toolset_hash,p_config_snapshot,p_capability_snapshot,
      p_release_revision,p_payload);
    RETURN r||jsonb_build_object('ingress_version',4);
END $$;

ALTER FUNCTION enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
    RENAME TO _enqueue_wecom_runtime_turn_v5_227_01;
CREATE FUNCTION enqueue_wecom_runtime_turn_v5(
 p_task_data JSONB,p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,
 p_input_content JSONB,p_delivery_context JSONB,p_agent_definition_id TEXT,
 p_agent_definition_revision TEXT,p_agent_definition_hash TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_release_revision TEXT,p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e JSONB; r JSONB; conversation_id UUID; user_id UUID; org_id UUID; scope_kind TEXT; scope_id TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    e:=enqueue_wecom_generation_turn_v2(p_task_data,p_input_message_id,p_output_message_id,p_turn_id,p_input_content,p_delivery_context);
    SELECT t.conversation_id,t.user_id,t.org_id INTO conversation_id,user_id,org_id FROM tasks t WHERE t.id=(e->>'task_id')::UUID FOR UPDATE;
    SELECT c.scope_type,c.scope_id INTO scope_kind,scope_id FROM conversations c WHERE c.id=conversation_id;
    r:=runtime_submit_ingress_v4(conversation_id,org_id,user_id,scope_kind,scope_id,user_id,p_agent_definition_id,p_agent_definition_revision,
      p_agent_definition_hash,'submit_input',p_idempotency_key,'wecom',p_input_message_id,'message:'||p_input_message_id,
      p_effective_toolset_revision,p_effective_toolset_hash,'{}'::JSONB,jsonb_build_object('requested_groups',jsonb_build_array('code')),
      p_release_revision,jsonb_build_object('schema_revision',3,'channel','wecom','task_id',e->>'task_id','input_message_id',p_input_message_id,
      'output_message_id',p_output_message_id,'turn_id',p_turn_id,'content',p_input_content,'delivery_context',p_delivery_context||'{"actor":false,"runtime":true}'::JSONB));
    IF r->>'outcome' IN ('ingress_disabled','subject_not_enabled') THEN RETURN e||jsonb_build_object('runtime_owned',false); END IF;
    IF r->>'outcome' NOT IN ('created','already_exists') THEN RAISE EXCEPTION 'WECOM_RUNTIME_INGRESS_V5_FAILED' USING ERRCODE='55000'; END IF;
    UPDATE tasks SET delivery_context=p_delivery_context||'{"actor":false,"runtime":true}'::JSONB WHERE id=(e->>'task_id')::UUID;
    RETURN e||jsonb_build_object('runtime_owned',true,'runtime_session_id',r->>'session_id','runtime_command_id',r->>'entity_id',
      'effective_toolset_revision',r->>'effective_toolset_revision','effective_toolset_hash',r->>'effective_toolset_hash','gate_state',r->>'gate_state');
END $$;

CREATE FUNCTION claim_ready_agent_action_snapshots_v2(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r JSONB; a RECORD; g agent_runtime_tenant_gate_controls%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    FOR a IN SELECT DISTINCT x.org_id AS org_id
      FROM agent_actions x JOIN agent_runs run ON run.id=x.run_id
      WHERE x.status='queued' AND run.status IN ('running','waiting_actions')
        AND x.org_id IS NOT NULL
    LOOP
      PERFORM pg_advisory_xact_lock(hashtextextended(
          'agent-runtime-kill-gate:'||a.org_id::TEXT||':tenant:tenant',0));
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

CREATE FUNCTION claim_ready_agent_actions_v2(
 p_worker_id TEXT,p_claim_request_id TEXT,p_batch_size INTEGER DEFAULT 10,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r JSONB; a RECORD;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    FOR a IN SELECT DISTINCT x.org_id AS org_id
      FROM agent_actions x JOIN agent_runs run ON run.id=x.run_id
      WHERE x.status='queued' AND run.status IN ('running','waiting_actions')
        AND x.org_id IS NOT NULL
    LOOP
      PERFORM pg_advisory_xact_lock(hashtextextended(
          'agent-runtime-kill-gate:'||a.org_id::TEXT||':tenant:tenant',0));
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

CREATE FUNCTION recover_expired_agent_action_attempt_v2(
 p_attempt_id UUID,p_expected_state_version BIGINT,p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB;
BEGIN
 f:=_agent_runtime_kill_epoch_context(p_attempt_id,NULL,NULL,p_expected_state_version,'recovery');
 IF f->>'outcome'<>'allowed' THEN RETURN f; END IF;
 RETURN recover_expired_agent_action_attempt(p_attempt_id,p_expected_state_version,p_worker_id,p_lease_seconds);
END $$;

CREATE FUNCTION gate_agent_action_dispatch_v2(
 p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,p_request_hash TEXT,
 p_policy_receipt_id UUID,p_executor_type TEXT,p_executor_revision INTEGER,p_policy_revision TEXT,p_recovery_mode TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB; r JSONB;
BEGIN
    f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,p_request_hash,p_expected_attempt_version,'dispatch');
    IF f->>'outcome'<>'allowed' THEN RETURN f; END IF;
    r:=gate_agent_action_dispatch(p_attempt_id,p_execution_token,p_expected_attempt_version,p_request_hash,
      p_policy_receipt_id,p_executor_type,p_executor_revision,p_policy_revision,p_recovery_mode);
    RETURN r;
END $$;

CREATE FUNCTION renew_agent_action_attempt_v2(p_attempt_id UUID,p_execution_token UUID,p_expected_state_version BIGINT,p_lease_seconds INTEGER DEFAULT 120)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB; r JSONB;
BEGIN
 f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,NULL,p_expected_state_version,'lease');
 IF f->>'outcome'<>'allowed' THEN RETURN f; END IF;
 r:=renew_agent_action_attempt(p_attempt_id,p_execution_token,p_expected_state_version,p_lease_seconds);
 IF r->>'outcome'='renewed' THEN UPDATE agent_runtime_owner_fences SET state_version=(r->>'state_version')::BIGINT,lease_expires_at=(r->>'lease_expires_at')::TIMESTAMPTZ,updated_at=clock_timestamp() WHERE owner_kind='attempt' AND owner_id=p_attempt_id AND execution_token=p_execution_token; END IF;
 RETURN r;
END $$;

CREATE FUNCTION mark_agent_action_dispatching_v2(p_attempt_id UUID,p_execution_token UUID,p_expected_state_version BIGINT,p_request_hash TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB; r JSONB;
BEGIN
 f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,p_request_hash,p_expected_state_version,'dispatch');
 IF f->>'outcome'<>'allowed' THEN RETURN f; END IF;
 r:=mark_agent_action_dispatching(p_attempt_id,p_execution_token,p_expected_state_version,p_request_hash);
 RETURN r;
END $$;

CREATE FUNCTION complete_agent_action_v2(p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,p_request_hash TEXT,p_result JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB;
BEGIN f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,p_request_hash,p_expected_attempt_version,'dispatch'); IF f->>'outcome'<>'allowed' THEN RETURN f; END IF; RETURN complete_agent_action(p_attempt_id,p_execution_token,p_expected_attempt_version,p_request_hash,p_result); END $$;
CREATE FUNCTION fail_agent_action_v2(p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,p_request_hash TEXT,p_result JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB;
BEGIN f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,p_request_hash,p_expected_attempt_version,'dispatch'); IF f->>'outcome'<>'allowed' THEN RETURN f; END IF; RETURN fail_agent_action(p_attempt_id,p_execution_token,p_expected_attempt_version,p_request_hash,p_result); END $$;
CREATE FUNCTION fail_claimed_agent_action_v2(p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,p_request_hash TEXT,p_error_code TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB;
BEGIN f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,p_request_hash,p_expected_attempt_version,'cleanup'); IF f->>'outcome' NOT IN ('allowed','fenced') AND f->>'outcome'<>'not_found' THEN RETURN f; END IF; RETURN fail_claimed_agent_action(p_attempt_id,p_execution_token,p_expected_attempt_version,p_request_hash,p_error_code); END $$;

CREATE FUNCTION mark_agent_action_accepted_v2(p_attempt_id UUID,p_execution_token UUID,p_expected_state_version BIGINT,p_request_hash TEXT,p_external_receipt JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB;
BEGIN f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,p_request_hash,p_expected_state_version,'receipt'); IF f->>'outcome' NOT IN ('allowed','fenced') THEN RETURN f; END IF; RETURN mark_agent_action_accepted(p_attempt_id,p_execution_token,p_expected_state_version,p_request_hash,p_external_receipt); END $$;
CREATE FUNCTION record_agent_action_unknown_v2(p_attempt_id UUID,p_execution_token UUID,p_expected_state_version BIGINT,p_request_hash TEXT,p_ambiguity_evidence JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE f JSONB;
BEGIN f:=_agent_runtime_kill_epoch_context(p_attempt_id,p_execution_token,p_request_hash,p_expected_state_version,'receipt'); IF f->>'outcome' NOT IN ('allowed','fenced') THEN RETURN f; END IF; RETURN record_agent_action_unknown(p_attempt_id,p_execution_token,p_expected_state_version,p_request_hash,p_ambiguity_evidence); END $$;

CREATE FUNCTION claim_agent_action_reconciliation_v2(p_attempt_id UUID,p_expected_state_version BIGINT,p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 120)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN PERFORM _agent_runtime_kill_epoch_context(p_attempt_id,NULL,NULL,p_expected_state_version,'reconcile'); RETURN claim_agent_action_reconciliation(p_attempt_id,p_expected_state_version,p_worker_id,p_lease_seconds); END $$;
CREATE FUNCTION renew_agent_action_reconciliation_v2(p_attempt_id UUID,p_reconciliation_token UUID,p_expected_state_version BIGINT,p_lease_seconds INTEGER DEFAULT 120)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN PERFORM _agent_runtime_kill_epoch_context(p_attempt_id,NULL,NULL,p_expected_state_version,'reconcile'); RETURN renew_agent_action_reconciliation(p_attempt_id,p_reconciliation_token,p_expected_state_version,p_lease_seconds); END $$;
CREATE FUNCTION resolve_agent_action_reconciliation_v2(p_attempt_id UUID,p_reconciliation_token UUID,p_expected_state_version BIGINT,p_request_hash TEXT,p_resolution TEXT,p_result JSONB DEFAULT NULL,p_ambiguity_evidence JSONB DEFAULT '{}'::JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN PERFORM _agent_runtime_kill_epoch_context(p_attempt_id,NULL,p_request_hash,p_expected_state_version,'reconcile'); RETURN resolve_agent_action_reconciliation(p_attempt_id,p_reconciliation_token,p_expected_state_version,p_request_hash,p_resolution,p_result,p_ambiguity_evidence); END $$;

REVOKE ALL ON FUNCTION runtime_submit_ingress_v4(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB), claim_ready_agent_action_snapshots_v2(TEXT,TEXT,INTEGER,INTEGER), claim_ready_agent_actions_v2(TEXT,TEXT,INTEGER,INTEGER), recover_expired_agent_action_attempt_v2(UUID,BIGINT,TEXT,INTEGER), gate_agent_action_dispatch_v2(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT), renew_agent_action_attempt_v2(UUID,UUID,BIGINT,INTEGER), mark_agent_action_dispatching_v2(UUID,UUID,BIGINT,TEXT), complete_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB), fail_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB), fail_claimed_agent_action_v2(UUID,UUID,BIGINT,TEXT,TEXT), mark_agent_action_accepted_v2(UUID,UUID,BIGINT,TEXT,JSONB), record_agent_action_unknown_v2(UUID,UUID,BIGINT,TEXT,JSONB), claim_agent_action_reconciliation_v2(UUID,BIGINT,TEXT,INTEGER), renew_agent_action_reconciliation_v2(UUID,UUID,BIGINT,INTEGER), resolve_agent_action_reconciliation_v2(UUID,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION claim_ready_agent_actions(TEXT,TEXT,INTEGER,INTEGER), claim_ready_agent_action_snapshots(TEXT,TEXT,INTEGER,INTEGER), gate_agent_action_dispatch(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT), renew_agent_action_attempt(UUID,UUID,BIGINT,INTEGER), mark_agent_action_dispatching(UUID,UUID,BIGINT,TEXT), recover_expired_agent_action_attempt(UUID,BIGINT,TEXT,INTEGER), complete_agent_action(UUID,UUID,BIGINT,TEXT,JSONB), fail_agent_action(UUID,UUID,BIGINT,TEXT,JSONB), fail_claimed_agent_action(UUID,UUID,BIGINT,TEXT,TEXT), mark_agent_action_accepted(UUID,UUID,BIGINT,TEXT,JSONB), record_agent_action_unknown(UUID,UUID,BIGINT,TEXT,JSONB), claim_agent_action_reconciliation(UUID,BIGINT,TEXT,INTEGER), renew_agent_action_reconciliation(UUID,UUID,BIGINT,INTEGER), resolve_agent_action_reconciliation(UUID,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB) FROM everydayai_agent_runtime_worker;
REVOKE EXECUTE ON FUNCTION runtime_submit_ingress_v2(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB), runtime_submit_ingress_v3(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB) FROM everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION claim_ready_agent_action_snapshots_v2(TEXT,TEXT,INTEGER,INTEGER), claim_ready_agent_actions_v2(TEXT,TEXT,INTEGER,INTEGER), recover_expired_agent_action_attempt_v2(UUID,BIGINT,TEXT,INTEGER), gate_agent_action_dispatch_v2(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT), renew_agent_action_attempt_v2(UUID,UUID,BIGINT,INTEGER), mark_agent_action_dispatching_v2(UUID,UUID,BIGINT,TEXT), complete_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB), fail_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB), fail_claimed_agent_action_v2(UUID,UUID,BIGINT,TEXT,TEXT), mark_agent_action_accepted_v2(UUID,UUID,BIGINT,TEXT,JSONB), record_agent_action_unknown_v2(UUID,UUID,BIGINT,TEXT,JSONB), claim_agent_action_reconciliation_v2(UUID,BIGINT,TEXT,INTEGER), renew_agent_action_reconciliation_v2(UUID,UUID,BIGINT,INTEGER), resolve_agent_action_reconciliation_v2(UUID,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB) TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v4(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB) TO everydayai_runtime,everydayai_wecom_runtime;

RESET ROLE;
