-- AR-18-A1.2-B4: fence Model Gateway work when its parent Run is cancelled.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_model_gateway_parent_active_v1(p_operation_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE;
 ss agent_runtime_sessions%ROWTYPE; r agent_runs%ROWTYPE;
 s agent_model_steps%ROWTYPE; a agent_model_attempts%ROWTYPE;
BEGIN
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE id=p_operation_id;
 IF NOT FOUND THEN RETURN FALSE; END IF;
 SELECT * INTO ss FROM agent_runtime_sessions WHERE id=o.session_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=o.run_id FOR UPDATE;
 SELECT * INTO s FROM agent_model_steps WHERE id=o.model_step_id FOR UPDATE;
 SELECT * INTO a FROM agent_model_attempts WHERE id=o.model_attempt_id FOR UPDATE;
 SELECT * INTO o FROM agent_runtime_model_gateway_operations
  WHERE id=p_operation_id FOR UPDATE;
 RETURN ss.id IS NOT NULL AND r.id IS NOT NULL AND s.id IS NOT NULL AND a.id IS NOT NULL
  AND o.id IS NOT NULL AND r.session_id=ss.id AND s.session_id=ss.id AND a.session_id=ss.id
  AND s.run_id=r.id AND a.run_id=r.id AND a.model_step_id=s.id
  AND r.status='running' AND r.execution_token=o.execution_token
  AND r.lease_expires_at>clock_timestamp() AND s.status='running'
  AND a.status='dispatching' AND a.execution_token=o.execution_token
  AND a.request_hash=o.request_hash AND a.worker_id=o.runtime_worker_id
  AND a.lease_expires_at>clock_timestamp()
  AND EXISTS(SELECT 1 FROM agent_run_attempts ra WHERE ra.run_id=r.id
   AND ra.execution_token=o.execution_token AND ra.worker_id=o.runtime_worker_id
   AND ra.ended_at IS NULL AND ra.lease_expires_at>clock_timestamp());
END $$;

CREATE FUNCTION _lock_agent_model_gateway_cancel_scope_v1(p_run_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE s agent_model_steps%ROWTYPE; a agent_model_attempts%ROWTYPE;
BEGIN
 SELECT * INTO s FROM agent_model_steps WHERE run_id=p_run_id
  AND status IN('pending','running') ORDER BY step_number DESC LIMIT 1 FOR UPDATE;
 IF s.id IS NOT NULL THEN
  SELECT * INTO a FROM agent_model_attempts WHERE model_step_id=s.id
   AND status IN('prepared','dispatching','unknown') ORDER BY id FOR UPDATE;
 END IF;
 IF a.id IS NOT NULL THEN
  PERFORM 1 FROM agent_runtime_model_gateway_operations
   WHERE model_attempt_id=a.id FOR UPDATE;
 END IF;
END $$;

CREATE OR REPLACE FUNCTION _cancel_agent_run_action_work(p_run_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE v_step agent_model_steps%ROWTYPE; v_attempt agent_model_attempts%ROWTYPE;
 v_operation agent_runtime_model_gateway_operations%ROWTYPE;
BEGIN
 SELECT * INTO v_step FROM agent_model_steps
  WHERE run_id=p_run_id AND status IN('pending','running')
  ORDER BY step_number DESC LIMIT 1 FOR UPDATE;
 IF FOUND THEN
  SELECT * INTO v_attempt FROM agent_model_attempts
   WHERE model_step_id=v_step.id AND status IN('prepared','dispatching','unknown')
   ORDER BY id FOR UPDATE;
 END IF;
 IF v_attempt.id IS NOT NULL THEN
  SELECT * INTO v_operation FROM agent_runtime_model_gateway_operations
   WHERE model_attempt_id=v_attempt.id FOR UPDATE;
  IF v_operation.status IN('submitted','claimed') THEN
   UPDATE agent_runtime_model_gateway_operations SET status='failed',
    finalize_token=lease_token,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
    response_started=FALSE,provider_request_id=NULL,response_hash=NULL,
    usage_summary='{}'::JSONB,ambiguity_code=NULL,
    terminal_error_code='GATEWAY_PARENT_RUN_CANCELLED_BEFORE_DISPATCH',
    finalized_at=clock_timestamp(),state_version=state_version+1,
    updated_at=clock_timestamp() WHERE id=v_operation.id;
  ELSIF v_operation.status='dispatching' THEN
   UPDATE agent_runtime_model_gateway_operations SET status='unknown',
    finalize_token=lease_token,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
    response_hash=NULL,usage_summary='{}'::JSONB,terminal_error_code=NULL,
    ambiguity_code='GATEWAY_PARENT_RUN_CANCELLED_AFTER_DISPATCH',
    finalized_at=clock_timestamp(),state_version=state_version+1,
    updated_at=clock_timestamp() WHERE id=v_operation.id;
  END IF;
 END IF;
 PERFORM 1 FROM agent_actions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts attempt
  JOIN agent_actions action ON action.id=attempt.action_id
  WHERE action.run_id=p_run_id AND attempt.ended_at IS NULL
  ORDER BY attempt.id FOR UPDATE OF attempt;
 IF v_step.id IS NOT NULL THEN
  PERFORM _release_agent_model_credits(v_step.id);
  IF v_attempt.id IS NOT NULL THEN
   UPDATE agent_model_attempts SET status='cancelled',retry_disposition='forbidden',
    state_version=state_version+1,completed_at=clock_timestamp(),
    updated_at=clock_timestamp() WHERE id=v_attempt.id;
  END IF;
  UPDATE agent_model_steps SET status='cancelled',stop_reason='cancelled',
   terminal_reason='run_cancelled',state_version=state_version+1,
   completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=v_step.id;
 END IF;
END $$;

CREATE OR REPLACE FUNCTION cancel_agent_run(
 p_run_id UUID,p_expected_state_version BIGINT,p_reason TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_session_id UUID;
 v_interaction agent_interactions%ROWTYPE; v_result JSONB;
BEGIN
 IF session_user='everydayai_worker' THEN PERFORM _assert_agent_runtime_actor(TRUE);
 ELSE PERFORM _assert_agent_runtime_actor(FALSE); END IF;
 SELECT session_id INTO v_session_id FROM agent_runs WHERE id=p_run_id;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=v_session_id FOR UPDATE;
 SELECT * INTO v_run FROM agent_runs WHERE id=p_run_id FOR UPDATE;
 PERFORM _lock_agent_model_gateway_cancel_scope_v1(p_run_id);
 PERFORM 1 FROM agent_actions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts attempt JOIN agent_actions action
  ON action.id=attempt.action_id WHERE action.run_id=p_run_id
  ORDER BY attempt.id FOR UPDATE OF attempt;
 PERFORM 1 FROM agent_interactions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_authorization_grants WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 IF v_run.status NOT IN('completed','failed','cancelled')
 AND v_run.state_version=p_expected_state_version THEN
  FOR v_interaction IN UPDATE agent_interactions SET status='cancelled',
   resolved_at=clock_timestamp(),recovery_worker_id=NULL,recovery_token=NULL,
   recovery_lease_expires_at=NULL,state_version=state_version+1,
   updated_at=clock_timestamp() WHERE run_id=p_run_id AND status='open' RETURNING * LOOP
   PERFORM append_agent_runtime_event(v_interaction.session_id,'interaction.cancelled',
    v_interaction.run_id,NULL,v_interaction.id,'system',session_user,
    jsonb_build_object('interaction_id',v_interaction.id,'action_id',v_interaction.action_id,
     'reason',p_reason),ARRAY['web_runtime','audit']::TEXT[]);
  END LOOP;
  UPDATE agent_authorization_grants SET status='revoked',revoked_at=clock_timestamp()
   WHERE run_id=p_run_id AND status='active';
 END IF;
 v_result:=_cancel_agent_run_220_23(p_run_id,p_expected_state_version,p_reason);
 RETURN v_result;
END $$;

CREATE OR REPLACE FUNCTION mark_agent_runtime_model_gateway_dispatched(
 p_operation_id UUID,p_claim_token UUID,p_expected_operation_version BIGINT,
 p_execution_token UUID,p_request_hash TEXT,p_provider_revision TEXT,
 p_tenant_kill_epoch BIGINT,p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE; parent_active BOOLEAN;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway');
 parent_active:=_agent_model_gateway_parent_active_v1(p_operation_id);
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE id=p_operation_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.execution_token IS DISTINCT FROM p_execution_token OR o.request_hash IS DISTINCT FROM p_request_hash
 OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
 OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch
 OR o.capability_kill_epoch<>p_capability_kill_epoch THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF o.status IN('completed','failed','unknown') THEN
  IF o.finalize_token IS DISTINCT FROM p_claim_token THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
  RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o));
 END IF;
 IF parent_active AND o.status='dispatching' AND o.lease_token IS NOT DISTINCT FROM p_claim_token
 AND o.state_version=p_expected_operation_version+1 AND o.lease_expires_at>clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o));
 END IF;
 IF NOT parent_active OR o.lease_token IS DISTINCT FROM p_claim_token
 OR o.state_version<>p_expected_operation_version OR o.lease_expires_at<=clock_timestamp()
 OR NOT _agent_model_gateway_fences(o.org_id,o.provider,o.purpose,p_tenant_kill_epoch,
  p_provider_kill_epoch,p_capability_kill_epoch,'dispatch') THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF o.status<>'claimed' THEN RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o)); END IF;
 UPDATE agent_runtime_model_gateway_operations SET status='dispatching',
  dispatching_at=clock_timestamp(),state_version=state_version+1,
  updated_at=clock_timestamp() WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','dispatching','operation',_agent_model_gateway_public(o));
END $$;

CREATE OR REPLACE FUNCTION renew_agent_runtime_model_gateway_operation(
 p_operation_id UUID,p_claim_token UUID,p_expected_operation_version BIGINT,
 p_execution_token UUID,p_request_hash TEXT,p_provider_revision TEXT,
 p_tenant_kill_epoch BIGINT,p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT,
 p_lease_seconds INTEGER DEFAULT 120)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE; parent_active BOOLEAN;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway');
 parent_active:=_agent_model_gateway_parent_active_v1(p_operation_id);
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE id=p_operation_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.execution_token IS DISTINCT FROM p_execution_token OR o.request_hash IS DISTINCT FROM p_request_hash
 OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
 OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch
 OR o.capability_kill_epoch<>p_capability_kill_epoch THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF o.status IN('completed','failed','unknown') THEN
  IF o.finalize_token IS DISTINCT FROM p_claim_token THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
  RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o));
 END IF;
 IF NOT parent_active OR p_lease_seconds NOT BETWEEN 15 AND 600
 OR o.status NOT IN('claimed','dispatching') OR o.lease_token IS DISTINCT FROM p_claim_token
 OR o.state_version<>p_expected_operation_version OR o.lease_expires_at<=clock_timestamp()
 OR NOT _agent_model_gateway_fences(o.org_id,o.provider,o.purpose,p_tenant_kill_epoch,
  p_provider_kill_epoch,p_capability_kill_epoch,'claim') THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 UPDATE agent_runtime_model_gateway_operations SET
  lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
  state_version=state_version+1,updated_at=clock_timestamp()
  WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome','renewed','operation',_agent_model_gateway_public(o));
END $$;

CREATE OR REPLACE FUNCTION finalize_agent_runtime_model_gateway_operation(
 p_operation_id UUID,p_claim_token UUID,p_expected_operation_version BIGINT,
 p_execution_token UUID,p_request_hash TEXT,p_provider_revision TEXT,
 p_tenant_kill_epoch BIGINT,p_provider_kill_epoch BIGINT,p_capability_kill_epoch BIGINT,
 p_terminal_status TEXT,p_provider_request_id TEXT,p_response_started BOOLEAN,
 p_response_hash TEXT,p_usage_summary JSONB,p_terminal_error_code TEXT,p_ambiguity_code TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE o agent_runtime_model_gateway_operations%ROWTYPE; bad BOOLEAN; parent_active BOOLEAN;
BEGIN
 PERFORM _assert_agent_model_gateway_actor('gateway');
 parent_active:=_agent_model_gateway_parent_active_v1(p_operation_id);
 SELECT * INTO o FROM agent_runtime_model_gateway_operations WHERE id=p_operation_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF o.status IN('completed','failed','unknown') THEN
  IF o.finalize_token IS DISTINCT FROM p_claim_token OR o.execution_token IS DISTINCT FROM p_execution_token
   OR o.request_hash IS DISTINCT FROM p_request_hash OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
   OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch
   OR o.capability_kill_epoch<>p_capability_kill_epoch THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
  RETURN jsonb_build_object('outcome','readback','operation',_agent_model_gateway_public(o));
 END IF;
 IF NOT parent_active OR o.status<>'dispatching' OR o.lease_token IS DISTINCT FROM p_claim_token
 OR o.execution_token IS DISTINCT FROM p_execution_token OR o.request_hash IS DISTINCT FROM p_request_hash
 OR o.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
 OR o.tenant_kill_epoch<>p_tenant_kill_epoch OR o.provider_kill_epoch<>p_provider_kill_epoch
 OR o.capability_kill_epoch<>p_capability_kill_epoch OR o.state_version<>p_expected_operation_version
 OR o.lease_expires_at<=clock_timestamp() THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 bad:=jsonb_typeof(COALESCE(p_usage_summary,'{}'))<>'object'
  OR pg_column_size(COALESCE(p_usage_summary,'{}'))>4096
  OR COALESCE(p_usage_summary::text,'')~*'(secret|password|credential|api[_-]?key|authorization|cookie|prompt|payload|response)'
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(COALESCE(p_usage_summary,'{}')) k
   WHERE k NOT IN('input_tokens','output_tokens','reasoning_tokens','total_tokens','credits','unit'))
  OR EXISTS(SELECT 1 FROM jsonb_each(COALESCE(p_usage_summary,'{}')) e WHERE CASE
   WHEN e.key='unit' THEN jsonb_typeof(e.value)<>'string' OR e.value#>>'{}' NOT IN('tokens','credits')
   WHEN jsonb_typeof(e.value)<>'number' THEN TRUE
   ELSE (e.value::text)::numeric<0 OR trunc((e.value::text)::numeric)<>(e.value::text)::numeric END);
 IF p_terminal_status NOT IN('completed','failed','unknown') OR bad
 OR (p_terminal_status='completed' AND (p_response_hash IS NULL OR p_response_hash!~'^[0-9a-f]{64}$'))
 OR (p_terminal_status='failed' AND COALESCE(p_terminal_error_code,'')!~'^[A-Z0-9_]{1,200}$')
 OR (p_terminal_status='unknown' AND COALESCE(p_ambiguity_code,'')!~'^[A-Z0-9_]{1,200}$')
 THEN RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_FINALIZE_INVALID' USING ERRCODE='22023'; END IF;
 UPDATE agent_runtime_model_gateway_operations SET status=p_terminal_status,
  finalize_token=lease_token,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
  provider_request_id=NULLIF(btrim(p_provider_request_id),''),response_started=p_response_started,
  response_hash=p_response_hash,usage_summary=COALESCE(p_usage_summary,'{}'),
  terminal_error_code=CASE WHEN p_terminal_status='failed' THEN btrim(p_terminal_error_code) END,
  ambiguity_code=CASE WHEN p_terminal_status='unknown' THEN btrim(p_ambiguity_code) END,
  finalized_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp()
  WHERE id=o.id RETURNING * INTO o;
 RETURN jsonb_build_object('outcome',p_terminal_status,'operation',_agent_model_gateway_public(o));
END $$;

REVOKE ALL ON FUNCTION _agent_model_gateway_parent_active_v1(UUID)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_agent_model_gateway,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
REVOKE ALL ON FUNCTION _lock_agent_model_gateway_cancel_scope_v1(UUID)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_agent_model_gateway,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
REVOKE ALL ON FUNCTION _cancel_agent_run_action_work(UUID)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_agent_model_gateway,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

RESET ROLE;
