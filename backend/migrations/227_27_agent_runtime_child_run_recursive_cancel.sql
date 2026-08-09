-- AR-18-A1.2-B6: durable recursive Child Run cancellation.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_runtime_child_run_cancel_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    root_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    parent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    parent_action_id UUID NOT NULL UNIQUE REFERENCES agent_actions(id) ON DELETE RESTRICT,
    parent_attempt_id UUID NOT NULL REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
    request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
    child_ordinal INTEGER NOT NULL CHECK(child_ordinal>=0),
    child_run_id UUID UNIQUE REFERENCES agent_runs(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN('requested','applied','confirmed')),
    terminal_kind TEXT CHECK(terminal_kind IS NULL OR terminal_kind IN(
        'not_created','completed_before_cancel','failed_before_cancel','cancelled')),
    proof_hash TEXT CHECK(proof_hash IS NULL OR proof_hash~'^[0-9a-f]{64}$'),
    claim_worker_id TEXT CHECK(claim_worker_id IS NULL OR (claim_worker_id=btrim(claim_worker_id)
        AND length(claim_worker_id) BETWEEN 1 AND 200)), claim_token UUID UNIQUE,
    claim_lease_expires_at TIMESTAMPTZ,
    state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), applied_at TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK((claim_worker_id IS NULL AND claim_token IS NULL AND claim_lease_expires_at IS NULL)
       OR (claim_worker_id IS NOT NULL AND claim_token IS NOT NULL AND claim_lease_expires_at IS NOT NULL)),
    CHECK((status='requested' AND applied_at IS NULL AND confirmed_at IS NULL
           AND terminal_kind IS NULL AND proof_hash IS NULL)
       OR (status='applied' AND applied_at IS NOT NULL AND confirmed_at IS NULL
           AND terminal_kind IS NULL AND proof_hash IS NULL)
       OR (status='confirmed' AND applied_at IS NOT NULL AND confirmed_at IS NOT NULL
           AND terminal_kind IS NOT NULL AND proof_hash IS NOT NULL))
);
ALTER TABLE agent_runtime_child_run_cancel_intents ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_child_run_cancel_intents_owner_all ON agent_runtime_child_run_cancel_intents TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
ALTER TABLE agent_runtime_child_run_cancel_intents FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE agent_runtime_child_run_cancel_intents FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_agent_model_gateway,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
CREATE INDEX idx_agent_child_cancel_scan ON agent_runtime_child_run_cancel_intents(updated_at,id) WHERE status IN('requested','applied');
CREATE FUNCTION _agent_child_cancel_intent_immutable_v1() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF (NEW.session_id,NEW.org_id,NEW.root_run_id,NEW.parent_run_id,
     NEW.parent_action_id,NEW.parent_attempt_id,NEW.request_hash,NEW.child_ordinal)
    IS DISTINCT FROM
    (OLD.session_id,OLD.org_id,OLD.root_run_id,OLD.parent_run_id,
     OLD.parent_action_id,OLD.parent_attempt_id,OLD.request_hash,OLD.child_ordinal)
 OR (OLD.child_run_id IS NOT NULL AND NEW.child_run_id IS DISTINCT FROM OLD.child_run_id)
 OR (OLD.status='confirmed' AND NEW IS DISTINCT FROM OLD) THEN
  RAISE EXCEPTION 'AGENT_CHILD_CANCEL_INTENT_IMMUTABLE' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER agent_child_cancel_intent_immutable
BEFORE UPDATE ON agent_runtime_child_run_cancel_intents
FOR EACH ROW EXECUTE FUNCTION _agent_child_cancel_intent_immutable_v1();
CREATE FUNCTION _agent_child_cancel_proof_v1(p_intent agent_runtime_child_run_cancel_intents,p_child agent_runs,p_kind TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT encode(sha256(convert_to(jsonb_build_object(
  'intent_id',p_intent.id,'parent_action_id',p_intent.parent_action_id,
  'parent_attempt_id',p_intent.parent_attempt_id,'request_hash',p_intent.request_hash,
  'child_ordinal',p_intent.child_ordinal,'child_run_id',p_intent.child_run_id,
  'child_state_version',p_child.state_version,'terminal_kind',p_kind)::TEXT,'UTF8')),'hex')
$$;
CREATE FUNCTION _seed_agent_child_cancel_intents_v1(p_run_id UUID) RETURNS INTEGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE item RECORD; root_id UUID; inserted_count INTEGER:=0; affected INTEGER;
BEGIN
 WITH RECURSIVE lineage AS (
  SELECT id,parent_run_id FROM agent_runs WHERE id=p_run_id
  UNION ALL SELECT r.id,r.parent_run_id FROM agent_runs r
   JOIN lineage l ON l.parent_run_id=r.id)
 SELECT id INTO root_id FROM lineage WHERE parent_run_id IS NULL LIMIT 1;
 FOR item IN
  SELECT action.*,attempt.id AS attempt_id,attempt.status AS attempt_status,
   child.id AS existing_child_id,child.child_ordinal AS existing_ordinal
  FROM agent_actions action
  JOIN LATERAL(SELECT a.* FROM agent_action_attempts a
   WHERE a.action_id=action.id ORDER BY a.attempt_number DESC,a.id DESC LIMIT 1) attempt ON TRUE
  LEFT JOIN agent_runs child ON child.parent_action_id=action.id
  WHERE action.run_id=p_run_id
   AND action.tool_name IN('image_agent','erp_agent','erp_analyze')
   AND action.status IN('running','accepted','unknown')
   AND attempt.status IN('dispatching','accepted','unknown')
  ORDER BY action.id
 LOOP
  IF item.existing_ordinal IS NULL AND
     COALESCE(item.arguments->>'child_ordinal','')!~'^[0-9]+$' THEN
   RAISE EXCEPTION 'AGENT_CHILD_CANCEL_ORDINAL_UNPROVEN' USING ERRCODE='55000';
  END IF;
  INSERT INTO agent_runtime_child_run_cancel_intents(
   session_id,org_id,root_run_id,parent_run_id,parent_action_id,
   parent_attempt_id,request_hash,child_ordinal,child_run_id)
  VALUES(item.session_id,item.org_id,root_id,p_run_id,item.id,item.attempt_id,
   item.request_hash,COALESCE(item.existing_ordinal,(item.arguments->>'child_ordinal')::INTEGER),
   item.existing_child_id)
  ON CONFLICT(parent_action_id) DO NOTHING;
  GET DIAGNOSTICS affected=ROW_COUNT;
  inserted_count:=inserted_count+affected;
  IF item.attempt_status='dispatching' THEN
   UPDATE agent_action_attempts SET status='unknown',
    ambiguity_evidence=jsonb_build_object('kind','CHILD_CREATE_OUTCOME_UNPROVEN_AFTER_PARENT_CANCEL'),
    retry_disposition='retry_after_reconcile',state_version=state_version+1,
    updated_at=clock_timestamp() WHERE id=item.attempt_id;
   UPDATE agent_actions SET status='unknown',retry_disposition='retry_after_reconcile',
    state_version=state_version+1,updated_at=clock_timestamp() WHERE id=item.id;
  END IF;
 END LOOP;
 RETURN inserted_count;
END $$;
CREATE FUNCTION create_agent_child_run_strict_v2(p_parent_run_id UUID,p_parent_action_id UUID,p_parent_request_hash TEXT,p_parent_execution_token UUID,p_child_ordinal INTEGER,p_capability TEXT,p_context JSONB) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE parent_run agent_runs%ROWTYPE; action agent_actions%ROWTYPE;
 attempt agent_action_attempts%ROWTYPE; policy agent_policy_receipts%ROWTYPE;
 intent agent_runtime_child_run_cancel_intents%ROWTYPE; child agent_runs%ROWTYPE;
 child_command UUID;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO action FROM agent_actions WHERE id=p_parent_action_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=action.session_id FOR UPDATE;
 SELECT * INTO parent_run FROM agent_runs WHERE id=p_parent_run_id FOR UPDATE;
 SELECT * INTO action FROM agent_actions WHERE id=p_parent_action_id FOR UPDATE;
 SELECT * INTO attempt FROM agent_action_attempts WHERE action_id=action.id
  AND execution_token=p_parent_execution_token ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE;
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents WHERE parent_action_id=action.id FOR UPDATE;
 SELECT * INTO child FROM agent_runs WHERE parent_action_id=action.id
  AND child_ordinal=p_child_ordinal FOR UPDATE;
 IF jsonb_typeof(p_context)<>'object' OR jsonb_typeof(p_context->'scope')<>'object' THEN RAISE EXCEPTION 'AGENT_CHILD_CONTEXT_REQUIRED' USING ERRCODE='22023'; END IF;
 IF (p_context->'scope'->>'org_id') IS DISTINCT FROM parent_run.org_id::TEXT OR (p_context->'scope'->>'user_id') IS DISTINCT FROM parent_run.user_id::TEXT THEN RAISE EXCEPTION 'AGENT_CHILD_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
 IF intent.id IS NOT NULL THEN
  IF child.id IS NOT NULL AND intent.child_run_id IS NULL THEN
   UPDATE agent_runtime_child_run_cancel_intents SET child_run_id=child.id,
    state_version=state_version+1,updated_at=clock_timestamp()
    WHERE id=intent.id RETURNING * INTO intent;
  ELSIF child.id IS NULL AND intent.child_run_id IS NULL THEN
   UPDATE agent_runtime_child_run_cancel_intents SET status='confirmed',
    applied_at=clock_timestamp(),confirmed_at=clock_timestamp(),terminal_kind='not_created',
    proof_hash=_agent_child_cancel_proof_v1(intent,NULL,'not_created'),
    state_version=state_version+1,updated_at=clock_timestamp()
    WHERE id=intent.id RETURNING * INTO intent;
  END IF;
  RETURN jsonb_build_object('outcome','cancel_fenced','intent_id',intent.id,
   'status',intent.status,'child_run_id',intent.child_run_id);
 END IF;
 IF parent_run.status<>'running' OR parent_run.lease_expires_at<=clock_timestamp()
  OR action.run_id<>parent_run.id OR action.request_hash<>p_parent_request_hash
  OR action.tool_name NOT IN('image_agent','erp_agent','erp_analyze')
  OR attempt.id IS NULL OR attempt.status NOT IN('dispatching','accepted','unknown')
  OR p_child_ordinal<0 THEN
  RAISE EXCEPTION 'AGENT_CHILD_PARENT_FENCED' USING ERRCODE='42501';
 END IF;
 IF jsonb_typeof(p_context)<>'object' OR NOT p_context?'policy_receipt_id'
  OR NOT p_context?'capability' OR NOT p_context?'budget_remaining' OR NOT p_context?'scope' THEN
  RAISE EXCEPTION 'AGENT_CHILD_CONTEXT_REQUIRED' USING ERRCODE='22023';
 END IF;
 SELECT * INTO policy FROM agent_policy_receipts
  WHERE id=(p_context->>'policy_receipt_id')::UUID AND action_id=action.id
   AND decision='allow' AND expires_at>clock_timestamp();
 IF policy.id IS NULL OR p_context->>'capability'<>p_capability
  OR (p_context->>'budget_remaining')::NUMERIC<0 THEN
  RAISE EXCEPTION 'AGENT_CHILD_POLICY_CONTEXT_INVALID' USING ERRCODE='42501';
 END IF;
 IF child.id IS NOT NULL THEN
  RETURN jsonb_build_object('outcome','already_exists','child_run_id',child.id,
   'parent_run_id',parent_run.id,'parent_action_id',action.id,'child_ordinal',child.child_ordinal);
 END IF;
 INSERT INTO agent_session_commands(session_id,org_id,user_id,command_type,
  idempotency_key,payload,request_hash)
 VALUES(parent_run.session_id,parent_run.org_id,parent_run.user_id,'submit_input',
  'child:'||action.id::TEXT||':'||p_child_ordinal,
  jsonb_build_object('parent_run_id',parent_run.id,'parent_action_id',action.id,
   'request_hash',p_parent_request_hash,'capability',p_capability),
  left(encode(digest(convert_to(p_parent_request_hash,'UTF8'),'sha256'),'hex'),32))
 RETURNING id INTO child_command;
 INSERT INTO agent_runs(session_id,command_id,org_id,user_id,run_kind,status,
  idempotency_key,request_hash,context_receipt,config_snapshot,capability_snapshot,
  parent_run_id,parent_action_id,child_ordinal,parent_request_hash)
 VALUES(parent_run.session_id,child_command,parent_run.org_id,parent_run.user_id,
  'continuation','queued','child:'||action.id::TEXT||':'||p_child_ordinal,
  left(encode(digest(convert_to(p_parent_request_hash,'UTF8'),'sha256'),'hex'),32),
  p_context,parent_run.config_snapshot,parent_run.capability_snapshot,parent_run.id,
  action.id,p_child_ordinal,p_parent_request_hash) RETURNING * INTO child;
 PERFORM _agent_runtime_226_append_action_event(action.id,'action.child_run.created',
  jsonb_build_object('child_run_id',child.id,'child_ordinal',p_child_ordinal));
 RETURN jsonb_build_object('outcome','created','child_run_id',child.id,
  'parent_run_id',parent_run.id,'parent_action_id',action.id,'child_ordinal',p_child_ordinal);
END $$;
CREATE FUNCTION read_agent_child_run_binding_v3(p_child_run_id UUID,p_parent_run_id UUID,p_parent_action_id UUID,p_parent_attempt_id UUID,p_parent_request_hash TEXT,p_ownership_token UUID,p_expected_state_version BIGINT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE child agent_runs%ROWTYPE; attempt agent_action_attempts%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_parent_attempt_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
 PERFORM 1 FROM agent_runs WHERE id=p_parent_run_id FOR UPDATE;
 PERFORM 1 FROM agent_actions WHERE id=p_parent_action_id FOR UPDATE;
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_parent_attempt_id FOR UPDATE;
 SELECT * INTO child FROM agent_runs WHERE parent_run_id=p_parent_run_id
  AND parent_action_id=p_parent_action_id
  AND (p_child_run_id IS NULL OR id=p_child_run_id) FOR UPDATE;
 IF attempt.action_id<>p_parent_action_id OR attempt.request_hash<>p_parent_request_hash
  OR attempt.state_version<>p_expected_state_version
  OR (attempt.execution_token<>p_ownership_token AND attempt.reconciliation_token<>p_ownership_token) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 IF child.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','readback','child_run_id',child.id,
  'parent_run_id',child.parent_run_id,'parent_action_id',child.parent_action_id,
  'child_ordinal',child.child_ordinal,'status',child.status,
  'state_version',child.state_version,'aggregation_revision',child.aggregation_revision,
  'result_hash',child.result_hash,'result',child.child_terminal_result);
END $$;
CREATE FUNCTION aggregate_agent_child_run_strict_v2(p_child_run_id UUID,p_parent_run_id UUID,p_parent_action_id UUID,p_parent_request_hash TEXT,p_parent_attempt_id UUID,p_reconciliation_token UUID,p_expected_state_version INTEGER,p_aggregation_revision INTEGER,p_result JSONB) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE action agent_actions%ROWTYPE; intent agent_runtime_child_run_cancel_intents%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO action FROM agent_actions WHERE id=p_parent_action_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=action.session_id FOR UPDATE;
 PERFORM 1 FROM agent_runs WHERE id=p_parent_run_id FOR UPDATE;
 SELECT * INTO action FROM agent_actions WHERE id=p_parent_action_id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts WHERE id=p_parent_attempt_id FOR UPDATE;
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents
  WHERE parent_action_id=p_parent_action_id FOR UPDATE;
 PERFORM 1 FROM agent_runs WHERE id=p_child_run_id FOR UPDATE;
 IF intent.id IS NOT NULL THEN
  RETURN jsonb_build_object('outcome','cancel_pending','intent_id',intent.id,'status',intent.status);
 END IF;
 RETURN aggregate_agent_child_run_strict(p_child_run_id,p_parent_run_id,p_parent_action_id,
  p_parent_request_hash,p_parent_attempt_id,p_reconciliation_token,
  p_expected_state_version,p_aggregation_revision,p_result);
END $$;
CREATE FUNCTION claim_next_agent_child_run_cancel_intent_v1(p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 120) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE candidate UUID; intent agent_runtime_child_run_cancel_intents%ROWTYPE; token UUID;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF NULLIF(btrim(p_worker_id),'') IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
  RAISE EXCEPTION 'AGENT_CHILD_CANCEL_CLAIM_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('agent_child_cancel:'||btrim(p_worker_id),0));
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents
  WHERE claim_worker_id=btrim(p_worker_id) AND status IN('requested','applied')
   AND claim_lease_expires_at>clock_timestamp() ORDER BY updated_at,id LIMIT 1 FOR UPDATE;
 IF FOUND THEN RETURN jsonb_build_object('outcome','claimed','intent',to_jsonb(intent)); END IF;
 FOR candidate IN SELECT id FROM agent_runtime_child_run_cancel_intents
  WHERE status IN('requested','applied') AND
   (claim_token IS NULL OR claim_lease_expires_at<=clock_timestamp())
  ORDER BY updated_at,id LIMIT 100 LOOP
  SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents WHERE id=candidate FOR UPDATE;
  IF intent.status NOT IN('requested','applied') OR
   (intent.claim_token IS NOT NULL AND intent.claim_lease_expires_at>clock_timestamp()) THEN CONTINUE; END IF;
  token:=gen_random_uuid();
  UPDATE agent_runtime_child_run_cancel_intents SET claim_worker_id=btrim(p_worker_id),
   claim_token=token,claim_lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
   state_version=state_version+1,updated_at=clock_timestamp()
   WHERE id=intent.id RETURNING * INTO intent;
  RETURN jsonb_build_object('outcome','claimed','intent',to_jsonb(intent));
 END LOOP;
 RETURN jsonb_build_object('outcome','not_found');
END $$;
CREATE FUNCTION get_claimed_agent_child_run_cancel_intent_v1(p_worker_id TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE intent agent_runtime_child_run_cancel_intents%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents
  WHERE claim_worker_id=btrim(p_worker_id) AND status IN('requested','applied')
   AND claim_lease_expires_at>clock_timestamp() ORDER BY updated_at,id LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','intent',to_jsonb(intent));
END $$;
CREATE FUNCTION _cancel_child_run_from_intent_v1(p_run_id UUID,p_reason TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE run agent_runs%ROWTYPE; action agent_actions%ROWTYPE; event JSONB;
 reconciliation_count INTEGER;
BEGIN
 SELECT * INTO run FROM agent_runs WHERE id=p_run_id FOR UPDATE;
 IF run.status='cancelled' THEN
  RETURN jsonb_build_object('outcome','already_cancelled','entity_id',run.id,
   'state_version',run.state_version); END IF;
 IF run.status IN('completed','failed') THEN
  RETURN jsonb_build_object('outcome','terminal_conflict'); END IF;
 PERFORM 1 FROM agent_actions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts attempt JOIN agent_actions a ON a.id=attempt.action_id
  WHERE a.run_id=p_run_id ORDER BY attempt.id FOR UPDATE OF attempt;
 PERFORM _seed_agent_child_cancel_intents_v1(p_run_id);
 PERFORM 1 FROM agent_interactions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_authorization_grants WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 UPDATE agent_interactions SET status='cancelled',resolved_at=clock_timestamp(),
  recovery_worker_id=NULL,recovery_token=NULL,recovery_lease_expires_at=NULL,
  state_version=state_version+1,updated_at=clock_timestamp()
  WHERE run_id=p_run_id AND status='open';
 UPDATE agent_authorization_grants SET status='revoked',revoked_at=clock_timestamp()
  WHERE run_id=p_run_id AND status='active';
 PERFORM _cancel_agent_run_action_work(p_run_id);
 UPDATE agent_action_attempts SET status='cancelled',reconciliation_token=NULL,
  reconciliation_lease_expires_at=NULL,state_version=state_version+1,
  ended_at=clock_timestamp(),updated_at=clock_timestamp()
  WHERE action_id IN(SELECT id FROM agent_actions WHERE run_id=p_run_id)
   AND status NOT IN('completed','failed','cancelled','accepted','unknown');
 FOR action IN UPDATE agent_actions SET status='cancelled',terminal_reason=LEFT(p_reason,200),
  state_version=state_version+1,completed_at=clock_timestamp(),updated_at=clock_timestamp()
  WHERE run_id=p_run_id AND status NOT IN(
   'completed','failed','rejected','cancelled','accepted','unknown') RETURNING * LOOP
  PERFORM append_agent_runtime_event(action.session_id,'action.cancelled',action.run_id,
   action.model_step_id,action.id,'system',session_user,
   jsonb_build_object('action_id',action.id,'reason',p_reason),ARRAY['web_runtime','audit']::TEXT[]);
 END LOOP;
 SELECT count(*) INTO reconciliation_count FROM agent_actions
  WHERE run_id=p_run_id AND status IN('accepted','unknown');
 UPDATE agent_runs SET status='cancelled',blocking_action_count=0,execution_token=NULL,
  lease_expires_at=NULL,completed_at=clock_timestamp(),terminal_reason=LEFT(p_reason,200),
  state_version=state_version+1,updated_at=clock_timestamp()
  WHERE id=p_run_id RETURNING * INTO run;
 UPDATE agent_run_attempts SET ended_at=clock_timestamp(),outcome='cancelled'
  WHERE run_id=p_run_id AND ended_at IS NULL;
 event:=append_agent_runtime_event(run.session_id,'run.cancelled',run.id,NULL,gen_random_uuid(),
  'system',session_user,jsonb_build_object('reason',p_reason,
   'pending_reconciliation_count',reconciliation_count),ARRAY['web_runtime','audit']::TEXT[]);
 RETURN jsonb_build_object('outcome','cancelled','entity_id',run.id,
  'state_version',run.state_version,'pending_reconciliation_count',reconciliation_count,
  'event_sequence',event->'event_sequence');
END $$;
CREATE FUNCTION apply_agent_child_run_cancel_intent_v1(p_intent_id UUID,p_claim_token UUID,p_expected_state_version BIGINT,p_reason TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE intent agent_runtime_child_run_cancel_intents%ROWTYPE; child agent_runs%ROWTYPE;
 cancel_result JSONB; pending INTEGER; descendants INTEGER; kind TEXT; proof TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents WHERE id=p_intent_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=intent.session_id FOR UPDATE;
 PERFORM 1 FROM agent_runs WHERE id=intent.parent_run_id FOR UPDATE;
 PERFORM 1 FROM agent_actions WHERE id=intent.parent_action_id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts WHERE id=intent.parent_attempt_id FOR UPDATE;
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents WHERE id=p_intent_id FOR UPDATE;
 SELECT * INTO child FROM agent_runs WHERE parent_action_id=intent.parent_action_id
  AND child_ordinal=intent.child_ordinal FOR UPDATE;
 IF intent.status='confirmed' THEN RETURN jsonb_build_object('outcome','confirmed',
  'intent_id',intent.id,'proof_hash',intent.proof_hash,'terminal_kind',intent.terminal_kind); END IF;
 IF intent.claim_token IS DISTINCT FROM p_claim_token OR intent.state_version<>p_expected_state_version
  OR intent.claim_lease_expires_at<=clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
 IF child.id IS NULL THEN kind:='not_created';
 ELSE
  IF intent.child_run_id IS NULL THEN
   UPDATE agent_runtime_child_run_cancel_intents SET child_run_id=child.id,
    state_version=state_version+1,updated_at=clock_timestamp()
    WHERE id=intent.id RETURNING * INTO intent;
  END IF;
  IF child.status='completed' THEN kind:='completed_before_cancel';
  ELSIF child.status='failed' THEN kind:='failed_before_cancel';
  ELSIF child.status<>'cancelled' THEN
   SELECT _cancel_child_run_from_intent_v1(
    child.id,LEFT(COALESCE(p_reason,'parent_cancel'),200))
    INTO cancel_result;
   SELECT * INTO child FROM agent_runs WHERE id=child.id FOR UPDATE;
  ELSE
   PERFORM _seed_agent_child_cancel_intents_v1(child.id);
  END IF;
  SELECT count(*) INTO pending FROM agent_actions WHERE run_id=child.id
   AND status NOT IN('completed','failed','rejected','cancelled');
  SELECT count(*) INTO descendants FROM agent_runtime_child_run_cancel_intents d
   WHERE d.parent_run_id=child.id AND d.status<>'confirmed';
  IF pending>0 OR descendants>0 THEN kind:=NULL;
  ELSIF child.status='cancelled' THEN kind:='cancelled';
  END IF;
 END IF;
 IF kind IS NOT NULL THEN
  IF intent.child_run_id IS NULL AND child.id IS NOT NULL THEN intent.child_run_id:=child.id; END IF;
  proof:=_agent_child_cancel_proof_v1(intent,child,kind);
  UPDATE agent_runtime_child_run_cancel_intents SET child_run_id=intent.child_run_id,
   status='confirmed',applied_at=COALESCE(applied_at,clock_timestamp()),
   confirmed_at=clock_timestamp(),terminal_kind=kind,proof_hash=proof,
   claim_worker_id=NULL,claim_token=NULL,claim_lease_expires_at=NULL,
   state_version=state_version+1,updated_at=clock_timestamp()
   WHERE id=intent.id RETURNING * INTO intent;
  RETURN jsonb_build_object('outcome','confirmed','intent_id',intent.id,
   'proof_hash',intent.proof_hash,'terminal_kind',intent.terminal_kind,
   'child_run_id',intent.child_run_id);
 END IF;
 UPDATE agent_runtime_child_run_cancel_intents SET status='applied',
  applied_at=COALESCE(applied_at,clock_timestamp()),claim_worker_id=NULL,
  claim_token=NULL,claim_lease_expires_at=NULL,state_version=state_version+1,
  updated_at=clock_timestamp() WHERE id=intent.id RETURNING * INTO intent;
 RETURN jsonb_build_object('outcome','applied','intent_id',intent.id,
  'child_run_id',intent.child_run_id);
END $$;
CREATE FUNCTION read_agent_child_run_cancel_intent_v1(p_parent_action_id UUID,p_parent_attempt_id UUID,p_reconciliation_token UUID,p_expected_state_version BIGINT,p_request_hash TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE attempt agent_action_attempts%ROWTYPE; intent agent_runtime_child_run_cancel_intents%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_parent_attempt_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
 PERFORM 1 FROM agent_runs WHERE id=attempt.run_id FOR UPDATE;
 PERFORM 1 FROM agent_actions WHERE id=p_parent_action_id FOR UPDATE;
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_parent_attempt_id FOR UPDATE;
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents
  WHERE parent_action_id=p_parent_action_id FOR UPDATE;
 IF attempt.action_id<>p_parent_action_id OR attempt.request_hash<>p_request_hash
  OR attempt.reconciliation_token<>p_reconciliation_token
  OR attempt.reconciliation_lease_expires_at<=clock_timestamp()
  OR attempt.state_version<>p_expected_state_version THEN
  RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF intent.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome',CASE WHEN intent.status='confirmed' THEN 'confirmed' ELSE 'pending' END,
  'intent_id',intent.id,'status',intent.status,'child_run_id',intent.child_run_id,
  'terminal_kind',intent.terminal_kind,'proof_hash',intent.proof_hash,
  'state_version',intent.state_version);
END $$;
CREATE FUNCTION finalize_agent_action_child_cancel_v1(p_attempt_id UUID,p_reconciliation_token UUID,p_expected_state_version BIGINT,p_request_hash TEXT,p_intent_id UUID,p_proof_hash TEXT,p_reserved_amount BIGINT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE attempt agent_action_attempts%ROWTYPE; action agent_actions%ROWTYPE; run agent_runs%ROWTYPE;
 intent agent_runtime_child_run_cancel_intents%ROWTYPE; reserve_cost agent_action_cost_settlements%ROWTYPE; kill_context JSONB; event JSONB; cost_result JSONB; cost_kind TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_reserved_amount<0 THEN RAISE EXCEPTION 'AGENT_CHILD_CANCEL_COST_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=attempt.session_id FOR UPDATE;
 SELECT * INTO run FROM agent_runs WHERE id=attempt.run_id FOR UPDATE;
 SELECT * INTO action FROM agent_actions WHERE id=attempt.action_id FOR UPDATE;
 SELECT * INTO attempt FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
 SELECT * INTO intent FROM agent_runtime_child_run_cancel_intents WHERE id=p_intent_id AND parent_action_id=action.id FOR UPDATE;
 SELECT * INTO reserve_cost FROM agent_action_cost_settlements WHERE action_id=action.id AND attempt_id=attempt.id AND kind='reserve' FOR UPDATE;
 IF reserve_cost.id IS NULL OR reserve_cost.reserved_amount<>p_reserved_amount OR reserve_cost.actual_amount<>0 OR reserve_cost.currency<>'credits' THEN RAISE EXCEPTION 'AGENT_CHILD_CANCEL_RESERVE_FACT_MISMATCH' USING ERRCODE='42501'; END IF;
 IF attempt.status='cancelled' AND action.status='cancelled' THEN
  IF intent.status<>'confirmed' OR intent.parent_attempt_id<>attempt.id
   OR intent.request_hash<>p_request_hash OR intent.proof_hash<>p_proof_hash
   OR attempt.external_receipt->>'child_cancel_intent_id'<>intent.id::TEXT
   OR attempt.external_receipt->>'proof_hash'<>intent.proof_hash THEN
   RAISE EXCEPTION 'AGENT_CHILD_CANCEL_TERMINAL_CONFLICT' USING ERRCODE='40001'; END IF;
  cost_kind:=CASE WHEN intent.terminal_kind='not_created' THEN 'release' ELSE 'refund' END;
  SELECT record_agent_action_cost_strict(action.id,attempt.id,cost_kind,p_reserved_amount,0,
   'credits','parent_run_cancelled',intent.proof_hash) INTO cost_result;
  RETURN jsonb_build_object('outcome','already_cancelled','action_id',action.id,'cost',cost_result); END IF;
 IF run.status<>'cancelled' OR action.tool_name NOT IN('image_agent','erp_agent','erp_analyze')
  OR action.status NOT IN('accepted','unknown') OR attempt.status NOT IN('accepted','unknown')
  OR attempt.reconciliation_operation<>'cancel'
  OR attempt.reconciliation_parent_run_state_version<>run.state_version
  OR attempt.reconciliation_token<>p_reconciliation_token
  OR attempt.reconciliation_lease_expires_at<=clock_timestamp()
  OR attempt.state_version<>p_expected_state_version OR attempt.request_hash<>p_request_hash
  OR intent.status<>'confirmed' OR intent.parent_attempt_id<>attempt.id
  OR intent.request_hash<>p_request_hash OR intent.proof_hash<>p_proof_hash THEN
  RAISE EXCEPTION 'AGENT_CHILD_CANCEL_FINALIZE_FENCED' USING ERRCODE='42501'; END IF;
 kill_context:=_agent_runtime_kill_epoch_context(attempt.id,attempt.execution_token,
  attempt.request_hash,attempt.state_version,'cleanup');
 IF kill_context->>'outcome'<>'allowed' THEN
  RAISE EXCEPTION 'AGENT_CHILD_CANCEL_KILL_FENCED' USING ERRCODE='42501'; END IF;
 UPDATE agent_action_attempts SET status='cancelled',last_provider_status='cancelled',
  external_receipt=jsonb_build_object('child_cancel_intent_id',intent.id,
   'proof_hash',intent.proof_hash,'terminal_kind',intent.terminal_kind),
  cancel_requested_at=COALESCE(cancel_requested_at,intent.requested_at),
  cancel_confirmed_at=intent.confirmed_at,ended_at=clock_timestamp(),
  retry_disposition='non_retryable',reconciliation_token=NULL,
  reconciliation_lease_expires_at=NULL,next_reconcile_at=NULL,
  state_version=state_version+1,updated_at=clock_timestamp() WHERE id=attempt.id;
 UPDATE agent_actions SET status='cancelled',retry_disposition='non_retryable',
  terminal_reason='child_cancel_confirmed',completed_at=clock_timestamp(),
  state_version=state_version+1,updated_at=clock_timestamp() WHERE id=action.id;
 cost_kind:=CASE WHEN intent.terminal_kind='not_created' THEN 'release' ELSE 'refund' END;
 SELECT record_agent_action_cost_strict(action.id,attempt.id,cost_kind,p_reserved_amount,0,
  'credits','parent_run_cancelled',intent.proof_hash) INTO cost_result;
 event:=append_agent_runtime_event(attempt.session_id,'action.cancelled',attempt.run_id,
  action.model_step_id,action.id,'reconciler',session_user,
  jsonb_build_object('action_id',action.id,'child_cancel_confirmed',TRUE),
  ARRAY['web_runtime','audit']::TEXT[]);
 RETURN jsonb_build_object('outcome','cancelled','action_id',action.id,'run_status',run.status,
  'blocking_action_count',run.blocking_action_count,'cost',cost_result,'event_sequence',event->'event_sequence');
END $$;
CREATE OR REPLACE FUNCTION cancel_agent_run(p_run_id UUID,p_expected_state_version BIGINT,p_reason TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE run agent_runs%ROWTYPE; session_id UUID; interaction agent_interactions%ROWTYPE; result JSONB;
BEGIN
 IF session_user='everydayai_worker' THEN PERFORM _assert_agent_runtime_actor(TRUE);
 ELSE PERFORM _assert_agent_runtime_actor(FALSE); END IF;
 SELECT r.session_id INTO session_id FROM agent_runs r WHERE r.id=p_run_id;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=session_id FOR UPDATE;
 SELECT * INTO run FROM agent_runs WHERE id=p_run_id FOR UPDATE;
 PERFORM _lock_agent_model_gateway_cancel_scope_v1(p_run_id);
 PERFORM 1 FROM agent_actions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts attempt JOIN agent_actions action ON action.id=attempt.action_id
  WHERE action.run_id=p_run_id ORDER BY attempt.id FOR UPDATE OF attempt;
 IF run.status NOT IN('completed','failed','cancelled') AND run.state_version=p_expected_state_version THEN
  PERFORM _seed_agent_child_cancel_intents_v1(p_run_id);
 END IF;
 PERFORM 1 FROM agent_interactions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_authorization_grants WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 IF run.status NOT IN('completed','failed','cancelled') AND run.state_version=p_expected_state_version THEN
  FOR interaction IN UPDATE agent_interactions SET status='cancelled',resolved_at=clock_timestamp(),
   recovery_worker_id=NULL,recovery_token=NULL,recovery_lease_expires_at=NULL,
   state_version=state_version+1,updated_at=clock_timestamp()
   WHERE run_id=p_run_id AND status='open' RETURNING * LOOP
   PERFORM append_agent_runtime_event(interaction.session_id,'interaction.cancelled',interaction.run_id,
    NULL,interaction.id,'system',session_user,jsonb_build_object('interaction_id',interaction.id,
     'action_id',interaction.action_id,'reason',p_reason),ARRAY['web_runtime','audit']::TEXT[]);
  END LOOP;
  UPDATE agent_authorization_grants SET status='revoked',revoked_at=clock_timestamp()
   WHERE run_id=p_run_id AND status='active';
 END IF;
 result:=_cancel_agent_run_220_23(p_run_id,p_expected_state_version,p_reason);
 RETURN result;
END $$;
REVOKE EXECUTE ON FUNCTION create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB),read_agent_child_run_strict_v2(UUID,UUID,UUID,UUID,TEXT,UUID,INTEGER,INTEGER),aggregate_agent_child_run_strict(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB),cancel_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,TEXT) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker;
REVOKE ALL ON FUNCTION _agent_child_cancel_intent_immutable_v1(),_agent_child_cancel_proof_v1(agent_runtime_child_run_cancel_intents,agent_runs,TEXT),_seed_agent_child_cancel_intents_v1(UUID),create_agent_child_run_strict_v2(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB),read_agent_child_run_binding_v3(UUID,UUID,UUID,UUID,TEXT,UUID,BIGINT),aggregate_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB),claim_next_agent_child_run_cancel_intent_v1(TEXT,INTEGER),get_claimed_agent_child_run_cancel_intent_v1(TEXT),_cancel_child_run_from_intent_v1(UUID,TEXT),apply_agent_child_run_cancel_intent_v1(UUID,UUID,BIGINT,TEXT),read_agent_child_run_cancel_intent_v1(UUID,UUID,UUID,BIGINT,TEXT),finalize_agent_action_child_cancel_v1(UUID,UUID,BIGINT,TEXT,UUID,TEXT,BIGINT) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_agent_model_gateway,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION create_agent_child_run_strict_v2(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB),read_agent_child_run_binding_v3(UUID,UUID,UUID,UUID,TEXT,UUID,BIGINT),aggregate_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB),claim_next_agent_child_run_cancel_intent_v1(TEXT,INTEGER),get_claimed_agent_child_run_cancel_intent_v1(TEXT),apply_agent_child_run_cancel_intent_v1(UUID,UUID,BIGINT,TEXT),read_agent_child_run_cancel_intent_v1(UUID,UUID,UUID,BIGINT,TEXT),finalize_agent_action_child_cancel_v1(UUID,UUID,BIGINT,TEXT,UUID,TEXT,BIGINT) TO everydayai_agent_runtime_worker;
RESET ROLE;
