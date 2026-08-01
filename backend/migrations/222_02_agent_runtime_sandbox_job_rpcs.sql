-- 222_02: Narrow Runtime and dedicated Sandbox Worker RPC boundary.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION _assert_agent_sandbox_actor(p_kind TEXT)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog, public AS $$ BEGIN
IF NULLIF(current_setting('app.request_id', TRUE), '') IS NULL OR (p_kind = 'runtime' AND (
session_user <> 'everydayai_runtime' OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'runtime'
)) OR (p_kind = 'sandbox_worker' AND ( session_user <> 'everydayai_sandbox_worker'
OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'sandbox_worker' ))
OR p_kind NOT IN ('runtime','sandbox_worker') THEN RAISE EXCEPTION 'AGENT_SANDBOX_ROLE_SCOPE_MISMATCH'
USING ERRCODE = '42501'; END IF; END; $$;
CREATE FUNCTION _agent_sandbox_runtime_scope_ok( p_session agent_runtime_sessions, p_action agent_actions
) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
SELECT tenant_org_id() IS NOT DISTINCT FROM p_action.org_id AND p_session.id = p_action.session_id AND (
(p_session.scope_kind = 'user' AND tenant_actor_user_id() = p_action.user_id) OR
(p_session.scope_kind = 'channel' AND EXISTS ( SELECT 1 FROM org_members member
WHERE member.org_id = p_session.org_id AND member.user_id = tenant_actor_user_id()
AND member.status = 'active' )) ) $$;
CREATE FUNCTION _lock_agent_sandbox_job(p_job_id UUID)
RETURNS agent_sandbox_jobs LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; BEGIN
SELECT * INTO v_job FROM agent_sandbox_jobs WHERE id = p_job_id; IF NOT FOUND THEN RETURN NULL; END IF;
PERFORM 1 FROM agent_runtime_sessions WHERE id=v_job.session_id FOR UPDATE;
PERFORM 1 FROM agent_runs WHERE id=v_job.run_id FOR UPDATE;
PERFORM 1 FROM agent_actions WHERE id=v_job.action_id FOR UPDATE; PERFORM 1 FROM agent_action_attempts
WHERE id=v_job.attempt_id ORDER BY id FOR UPDATE; PERFORM 1 FROM agent_action_dispatch_intents
WHERE id=v_job.dispatch_intent_id FOR UPDATE;
SELECT * INTO v_job FROM agent_sandbox_jobs WHERE id=p_job_id FOR UPDATE; IF NOT EXISTS (
SELECT 1 FROM agent_actions action JOIN agent_action_attempts attempt ON attempt.id=v_job.attempt_id
JOIN agent_action_dispatch_intents intent ON intent.id=v_job.dispatch_intent_id
WHERE action.id=v_job.action_id AND action.session_id=v_job.session_id AND action.run_id=v_job.run_id
AND action.org_id IS NOT DISTINCT FROM v_job.org_id AND action.user_id IS NOT DISTINCT FROM v_job.user_id
AND attempt.action_id=action.id AND intent.action_id=action.id AND intent.attempt_id=attempt.id
AND intent.external_idempotency_key=v_job.external_idempotency_key
AND intent.request_hash=v_job.request_hash ) THEN RAISE EXCEPTION 'AGENT_SANDBOX_PERSISTED_BINDING_INVALID'
USING ERRCODE='55000'; END IF; RETURN v_job; END; $$;
CREATE FUNCTION _agent_sandbox_runtime_job(p_job agent_sandbox_jobs)
RETURNS JSONB LANGUAGE sql IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
SELECT to_jsonb(p_job) - ARRAY[
'claim_worker_id','claim_token','lease_expires_at',
'reconciliation_worker_id','reconciliation_token',
'reconciliation_lease_expires_at','ambiguity_evidence'] $$;
CREATE FUNCTION create_or_get_sandbox_job( p_action_id UUID, p_attempt_id UUID, p_dispatch_intent_id UUID,
p_expected_action_version BIGINT, p_expected_attempt_version BIGINT,
p_external_idempotency_key TEXT, p_request_hash TEXT, p_executor_type TEXT, p_executor_revision INTEGER,
p_runtime_revision TEXT, p_workspace_scope_ref TEXT,
p_code_sha256 TEXT, p_input_manifest JSONB, p_resource_limits JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ DECLARE
v_action agent_actions%ROWTYPE; v_attempt agent_action_attempts%ROWTYPE;
v_intent agent_action_dispatch_intents%ROWTYPE;
v_session agent_runtime_sessions%ROWTYPE; v_job agent_sandbox_jobs%ROWTYPE; BEGIN
PERFORM _assert_agent_sandbox_actor('runtime');
SELECT * INTO v_action FROM agent_actions WHERE id=p_action_id;
IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
SELECT * INTO v_session FROM agent_runtime_sessions WHERE id=v_action.session_id FOR UPDATE;
PERFORM 1 FROM agent_runs WHERE id=v_action.run_id FOR UPDATE;
SELECT * INTO v_action FROM agent_actions WHERE id=p_action_id FOR UPDATE;
SELECT * INTO v_attempt FROM agent_action_attempts WHERE id=p_attempt_id ORDER BY id FOR UPDATE;
SELECT * INTO v_intent FROM agent_action_dispatch_intents WHERE id=p_dispatch_intent_id FOR UPDATE;
IF NOT _agent_sandbox_runtime_scope_ok(v_session,v_action) THEN
RAISE EXCEPTION 'AGENT_SANDBOX_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
IF p_workspace_scope_ref IS DISTINCT FROM
   'ws-scope:'||v_session.scope_kind||':'||v_session.scope_id THEN
RETURN jsonb_build_object('outcome','scope_binding_invalid'); END IF;
PERFORM pg_advisory_xact_lock(hashtextextended(p_external_idempotency_key,222));
SELECT * INTO v_job FROM agent_sandbox_jobs
WHERE external_idempotency_key=p_external_idempotency_key FOR UPDATE; IF FOUND THEN
IF v_job.action_id<>p_action_id OR v_job.attempt_id<>p_attempt_id
OR v_job.dispatch_intent_id<>p_dispatch_intent_id OR v_job.request_hash<>p_request_hash
OR v_job.executor_type<>btrim(p_executor_type) OR v_job.executor_revision<>p_executor_revision
OR v_job.runtime_revision<>btrim(p_runtime_revision) OR v_job.workspace_scope_ref<>p_workspace_scope_ref
OR v_job.code_sha256<>p_code_sha256 OR v_job.input_manifest<>p_input_manifest
OR v_job.resource_limits<>p_resource_limits THEN
RETURN jsonb_build_object('outcome','idempotency_conflict'); END IF; RETURN jsonb_build_object(
'outcome','already_created','job',_agent_sandbox_runtime_job(v_job)); END IF;
IF v_action.state_version<>p_expected_action_version
OR v_attempt.state_version<>p_expected_attempt_version THEN
RETURN jsonb_build_object('outcome','stale_version'); END IF;
IF v_action.tool_name<>'code_execute' OR v_action.status<>'running'
OR v_attempt.action_id<>v_action.id OR v_attempt.status<>'dispatching'
OR v_intent.action_id<>v_action.id OR v_intent.attempt_id<>v_attempt.id
OR v_intent.external_idempotency_key<>p_external_idempotency_key OR v_intent.request_hash<>p_request_hash
OR v_intent.executor_type<>btrim(p_executor_type) OR v_intent.executor_revision<>p_executor_revision
OR v_intent.recovery_mode<>'reconcile_only' THEN
RETURN jsonb_build_object('outcome','dispatch_intent_invalid'); END IF; INSERT INTO agent_sandbox_jobs(
session_id,run_id,action_id,attempt_id,dispatch_intent_id,org_id,user_id,
external_idempotency_key,request_hash,executor_type,executor_revision,
runtime,runtime_revision,workspace_scope_ref,code_ref,code_sha256, input_manifest,resource_limits,status
) VALUES ( v_action.session_id,v_action.run_id,v_action.id,v_attempt.id,v_intent.id,
v_action.org_id,v_action.user_id,p_external_idempotency_key,p_request_hash,
btrim(p_executor_type),p_executor_revision,'python', btrim(p_runtime_revision),p_workspace_scope_ref,
'agent-action:'||v_action.id::TEXT||':arguments.code',p_code_sha256,
p_input_manifest,p_resource_limits,'prepared' ) RETURNING * INTO v_job;
UPDATE agent_sandbox_jobs SET status='queued',state_version=state_version+1,
queued_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=v_job.id RETURNING * INTO v_job;
RETURN jsonb_build_object(
'outcome','created','job',_agent_sandbox_runtime_job(v_job)); END; $$;
CREATE FUNCTION get_sandbox_job(p_job_id UUID) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; v_session agent_runtime_sessions%ROWTYPE;
v_action agent_actions%ROWTYPE; BEGIN IF session_user='everydayai_runtime' THEN
PERFORM _assert_agent_sandbox_actor('runtime');
ELSE PERFORM _assert_agent_sandbox_actor('sandbox_worker'); END IF;
v_job := _lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF session_user='everydayai_runtime' THEN
SELECT * INTO v_session FROM agent_runtime_sessions WHERE id=v_job.session_id;
SELECT * INTO v_action FROM agent_actions WHERE id=v_job.action_id;
IF NOT _agent_sandbox_runtime_scope_ok(v_session,v_action) THEN
RAISE EXCEPTION 'AGENT_SANDBOX_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF; END IF;
IF session_user='everydayai_runtime' THEN RETURN jsonb_build_object(
'outcome','found','job',_agent_sandbox_runtime_job(v_job)); END IF;
RETURN jsonb_build_object('outcome','found','job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION claim_next_sandbox_job( p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_candidate UUID; v_job agent_sandbox_jobs%ROWTYPE; v_token UUID; BEGIN
PERFORM _assert_agent_sandbox_actor('sandbox_worker'); IF NULLIF(btrim(p_worker_id),'') IS NULL
OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
RAISE EXCEPTION 'AGENT_SANDBOX_CLAIM_INVALID' USING ERRCODE='22023'; END IF;
FOR v_candidate IN SELECT id FROM agent_sandbox_jobs
WHERE status='queued' ORDER BY queued_at,id LIMIT 100 LOOP v_job := _lock_agent_sandbox_job(v_candidate);
IF v_job.status<>'queued' THEN CONTINUE; END IF; v_token:=gen_random_uuid();
UPDATE agent_sandbox_jobs SET status='claimed', claim_worker_id=btrim(p_worker_id),claim_token=v_token,
fencing_token=fencing_token+1, lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
claimed_at=clock_timestamp(),state_version=state_version+1, updated_at=clock_timestamp()
WHERE id=v_job.id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','claimed','job',to_jsonb(v_job)); END LOOP;
RETURN jsonb_build_object('outcome','not_found'); END; $$;
CREATE FUNCTION renew_sandbox_job_lease( p_job_id UUID, p_claim_token UUID, p_fencing_token BIGINT,
p_expected_version BIGINT, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; v_partial BOOLEAN;
BEGIN PERFORM _assert_agent_sandbox_actor('sandbox_worker');
v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.claim_token IS DISTINCT FROM p_claim_token OR v_job.fencing_token<>p_fencing_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF; IF v_job.state_version<>p_expected_version
OR v_job.lease_expires_at<=clock_timestamp()
OR v_job.status NOT IN ('claimed','starting','running','cancel_requested')
OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN RETURN jsonb_build_object('outcome','stale_version'); END IF;
UPDATE agent_sandbox_jobs SET lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','renewed','job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION mark_sandbox_job_started( p_job_id UUID, p_claim_token UUID, p_fencing_token BIGINT,
p_expected_version BIGINT, p_phase TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE v_job agent_sandbox_jobs%ROWTYPE; BEGIN
PERFORM _assert_agent_sandbox_actor('sandbox_worker'); v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.claim_token IS DISTINCT FROM p_claim_token OR v_job.fencing_token<>p_fencing_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF; IF v_job.state_version<>p_expected_version
OR v_job.lease_expires_at<=clock_timestamp() THEN RETURN jsonb_build_object('outcome','stale_version');
END IF; IF (p_phase='starting' AND v_job.status<>'claimed')
OR (p_phase='running' AND v_job.status<>'starting') OR p_phase NOT IN ('starting','running') THEN
RETURN jsonb_build_object('outcome','invalid_transition'); END IF;
UPDATE agent_sandbox_jobs SET status=p_phase,
starting_at=CASE WHEN p_phase='starting' THEN clock_timestamp() ELSE starting_at END,
started_at=CASE WHEN p_phase='running' THEN clock_timestamp() ELSE started_at END,
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome',p_phase,'job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION recover_expired_sandbox_job( p_job_id UUID, p_expected_version BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; v_outcome TEXT; BEGIN
PERFORM _assert_agent_sandbox_actor('sandbox_worker'); v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.state_version<>p_expected_version OR v_job.lease_expires_at>clock_timestamp() THEN
RETURN jsonb_build_object('outcome','stale_version'); END IF;
IF v_job.status='claimed' AND v_job.starting_at IS NULL AND v_job.started_at IS NULL
AND v_job.artifact_manifest->'items'='[]'::JSONB AND v_job.partial_effects->'items'='[]'::JSONB
AND v_job.cancel_accepted_at IS NULL THEN
UPDATE agent_sandbox_jobs SET status='queued',claim_worker_id=NULL,
claim_token=NULL,lease_expires_at=NULL,state_version=state_version+1, updated_at=clock_timestamp()
WHERE id=p_job_id RETURNING * INTO v_job; v_outcome:='requeued';
ELSIF v_job.status IN ('claimed','starting','running','cancel_requested') THEN
UPDATE agent_sandbox_jobs SET status='unknown',
ambiguity_evidence='{"kind":"SANDBOX_WORKER_LEASE_EXPIRED"}',
claim_worker_id=NULL,claim_token=NULL,lease_expires_at=NULL,
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
v_outcome:='unknown'; ELSE RETURN jsonb_build_object('outcome','invalid_transition'); END IF;
RETURN jsonb_build_object('outcome',v_outcome,'job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION request_sandbox_job_cancel( p_job_id UUID, p_expected_version BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; v_session agent_runtime_sessions%ROWTYPE;
v_action agent_actions%ROWTYPE; v_receipt JSONB;
BEGIN PERFORM _assert_agent_sandbox_actor('runtime');
v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
SELECT * INTO v_session FROM agent_runtime_sessions WHERE id=v_job.session_id;
SELECT * INTO v_action FROM agent_actions WHERE id=v_job.action_id;
IF NOT _agent_sandbox_runtime_scope_ok(v_session,v_action) THEN
RAISE EXCEPTION 'AGENT_SANDBOX_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
IF v_job.status IN ('succeeded','failed','timed_out','cancelled') THEN
RETURN jsonb_build_object(
'outcome','already_terminal','job',_agent_sandbox_runtime_job(v_job)); END IF;
IF v_job.status='cancel_requested' THEN
RETURN jsonb_build_object(
'outcome','already_cancel_requested','job',_agent_sandbox_runtime_job(v_job)); END IF;
IF v_job.state_version<>p_expected_version THEN RETURN jsonb_build_object('outcome','stale_version');
END IF; IF v_job.status NOT IN ('queued','claimed','starting','running') THEN
RETURN jsonb_build_object('outcome','invalid_transition'); END IF;
IF v_job.status='queued' THEN
v_receipt:=jsonb_build_object(
'receipt_revision',1,'execution_outcome','interrupted',
'stdout_summary','','stdout_original_length',0,
'stdout_sha256','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
'stdout_truncated',FALSE,'stderr_summary','','stderr_original_length',0,
'stderr_sha256','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
'stderr_truncated',FALSE,'artifact_manifest','{"schema_revision":1,"items":[]}'::JSONB,
'partial_effects','{"schema_revision":1,"items":[]}'::JSONB,
'materialization_status','not_started','cleanup_status','not_required',
'cleanup_evidence','{}'::JSONB);
UPDATE agent_sandbox_jobs SET status='cancelled',
cancel_requested_at=clock_timestamp(),cancel_accepted_at=clock_timestamp(),
cancel_confirmed_at=clock_timestamp(),terminal_at=clock_timestamp(),
terminal_reason='CANCELLED_BEFORE_START',execution_outcome='interrupted',
stdout_summary='',stderr_summary='',
stdout_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
stderr_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
receipt_hash=_agent_sandbox_receipt_hash(v_receipt),
state_version=state_version+1,updated_at=clock_timestamp()
WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object(
'outcome','cancelled','job',_agent_sandbox_runtime_job(v_job)); END IF;
UPDATE agent_sandbox_jobs SET status='cancel_requested',
cancel_requested_at=clock_timestamp(),state_version=state_version+1, updated_at=clock_timestamp()
WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object(
'outcome','cancel_requested','job',_agent_sandbox_runtime_job(v_job)); END; $$;
CREATE FUNCTION record_sandbox_cancel_signal( p_job_id UUID, p_claim_token UUID, p_fencing_token BIGINT,
p_expected_version BIGINT, p_signal_state TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE v_job agent_sandbox_jobs%ROWTYPE; BEGIN
PERFORM _assert_agent_sandbox_actor('sandbox_worker'); v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.claim_token IS DISTINCT FROM p_claim_token OR v_job.fencing_token<>p_fencing_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF; IF v_job.state_version<>p_expected_version
OR v_job.lease_expires_at<=clock_timestamp()
OR v_job.status<>'cancel_requested' OR p_signal_state NOT IN ('accepted','confirmed') THEN
RETURN jsonb_build_object('outcome','invalid_transition'); END IF;
IF p_signal_state='confirmed' AND v_job.cancel_accepted_at IS NULL THEN
RETURN jsonb_build_object('outcome','invalid_transition'); END IF; UPDATE agent_sandbox_jobs SET
cancel_accepted_at=CASE WHEN p_signal_state='accepted' THEN clock_timestamp() ELSE cancel_accepted_at END,
cancel_confirmed_at=CASE WHEN p_signal_state='confirmed'
THEN clock_timestamp() ELSE cancel_confirmed_at END,
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','cancel_'||p_signal_state,'job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION finish_sandbox_job( p_job_id UUID, p_claim_token UUID, p_fencing_token BIGINT,
p_expected_version BIGINT, p_terminal_status TEXT, p_terminal_reason TEXT,
p_receipt_hash TEXT, p_receipt JSONB ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE v_job agent_sandbox_jobs%ROWTYPE;
v_partial BOOLEAN; v_now TIMESTAMPTZ;
BEGIN PERFORM _assert_agent_sandbox_actor('sandbox_worker'); v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF p_receipt_hash !~ '^[0-9a-f]{64}$'
OR p_receipt_hash IS DISTINCT FROM _agent_sandbox_receipt_hash(p_receipt) THEN
RETURN jsonb_build_object('outcome','receipt_hash_conflict'); END IF;
IF v_job.status IN ('succeeded','failed','timed_out','cancelled') THEN
IF v_job.status=p_terminal_status AND v_job.receipt_hash=p_receipt_hash THEN
RETURN jsonb_build_object('outcome','already_terminal','job',to_jsonb(v_job)); END IF;
RETURN jsonb_build_object('outcome','terminal_conflict'); END IF;
IF v_job.claim_token IS DISTINCT FROM p_claim_token OR v_job.fencing_token<>p_fencing_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF; IF v_job.state_version<>p_expected_version
OR v_job.lease_expires_at<=clock_timestamp()
OR p_terminal_status NOT IN ('succeeded','failed','timed_out','cancelled')
OR p_terminal_reason !~ '^[A-Z][A-Z0-9_]{0,199}$' OR p_receipt_hash !~ '^[0-9a-f]{64}$'
OR jsonb_typeof(p_receipt)<>'object' THEN
RETURN jsonb_build_object('outcome','invalid_transition'); END IF;
IF NOT _agent_sandbox_receipt_is_valid(p_receipt) THEN
RETURN jsonb_build_object('outcome','malformed_receipt'); END IF;
v_partial:=jsonb_array_length(p_receipt->'partial_effects'->'items')>0;
v_now:=clock_timestamp();
IF p_terminal_status='cancelled' AND v_job.cancel_confirmed_at IS NULL
OR p_terminal_status='succeeded' AND ( p_receipt->>'materialization_status'<>'completed'
OR p_receipt->>'cleanup_status' NOT IN ('not_required','completed') )
OR v_partial AND p_receipt->>'cleanup_status'<>'completed' THEN
RETURN jsonb_build_object('outcome','terminal_guard_failed'); END IF;
UPDATE agent_sandbox_jobs SET status=p_terminal_status,
terminal_at=clock_timestamp(),terminal_reason=p_terminal_reason,
execution_outcome=p_receipt->>'execution_outcome',
receipt_hash=p_receipt_hash,receipt_revision=(p_receipt->>'receipt_revision')::INTEGER,
stdout_summary=p_receipt->>'stdout_summary',
stdout_original_length=(p_receipt->>'stdout_original_length')::BIGINT,
stdout_sha256=p_receipt->>'stdout_sha256', stdout_truncated=(p_receipt->>'stdout_truncated')::BOOLEAN,
stderr_summary=p_receipt->>'stderr_summary',
stderr_original_length=(p_receipt->>'stderr_original_length')::BIGINT,
stderr_sha256=p_receipt->>'stderr_sha256', stderr_truncated=(p_receipt->>'stderr_truncated')::BOOLEAN,
artifact_manifest=p_receipt->'artifact_manifest', partial_effects=p_receipt->'partial_effects',
materialization_status=p_receipt->>'materialization_status',
materialization_receipt=COALESCE(p_receipt->'materialization_receipt','{}'),
cleanup_status=p_receipt->>'cleanup_status',
cleanup_evidence=COALESCE(p_receipt->'cleanup_evidence','{}'),
partial_effects_recorded_at=CASE WHEN v_partial
THEN v_now ELSE NULL END, cleanup_deadline_at=CASE WHEN v_partial
THEN LEAST(v_now+interval '24 hours', COALESCE((p_receipt->>'cleanup_deadline_at')::TIMESTAMPTZ,
v_now+interval '24 hours')) ELSE NULL END,
claim_worker_id=NULL,claim_token=NULL,lease_expires_at=NULL,
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome',p_terminal_status,'job',to_jsonb(v_job));
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
RETURN jsonb_build_object('outcome','malformed_receipt'); END; $$;
CREATE FUNCTION record_sandbox_job_unknown( p_job_id UUID, p_claim_token UUID, p_fencing_token BIGINT,
p_expected_version BIGINT, p_ambiguity_evidence JSONB,
p_partial_effects JSONB, p_cleanup_deadline_at TIMESTAMPTZ DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; v_partial BOOLEAN; v_now TIMESTAMPTZ; BEGIN
PERFORM _assert_agent_sandbox_actor('sandbox_worker'); v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.status='unknown' THEN IF v_job.ambiguity_evidence=p_ambiguity_evidence
AND v_job.partial_effects=p_partial_effects THEN
RETURN jsonb_build_object('outcome','already_unknown','job',to_jsonb(v_job)); END IF;
RETURN jsonb_build_object('outcome','terminal_conflict'); END IF;
IF v_job.claim_token IS DISTINCT FROM p_claim_token OR v_job.fencing_token<>p_fencing_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF; IF v_job.state_version<>p_expected_version
OR v_job.lease_expires_at<=clock_timestamp()
OR v_job.status NOT IN ('claimed','starting','running','cancel_requested')
OR NOT _agent_sandbox_evidence_is_valid(p_ambiguity_evidence)
OR NOT _agent_sandbox_manifest_is_valid(p_partial_effects,'partial') THEN
RETURN jsonb_build_object('outcome','invalid_transition'); END IF;
v_partial:=jsonb_array_length(p_partial_effects->'items')>0; v_now:=clock_timestamp();
UPDATE agent_sandbox_jobs SET status='unknown',
ambiguity_evidence=p_ambiguity_evidence, partial_effects=p_partial_effects,
partial_effects_recorded_at=CASE WHEN v_partial THEN v_now ELSE NULL END,
cleanup_status=CASE WHEN v_partial THEN 'pending' ELSE cleanup_status END,
cleanup_deadline_at=CASE WHEN v_partial THEN LEAST(v_now+interval '24 hours',
COALESCE(p_cleanup_deadline_at,v_now+interval '24 hours')) ELSE NULL END,
claim_worker_id=NULL,claim_token=NULL,lease_expires_at=NULL,
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','unknown','job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION claim_sandbox_job_reconciliation(
p_job_id UUID, p_expected_version BIGINT, p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; v_token UUID; BEGIN
PERFORM _assert_agent_sandbox_actor('sandbox_worker'); v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.status<>'unknown' THEN RETURN jsonb_build_object('outcome','not_reconcilable'); END IF;
IF v_job.state_version<>p_expected_version OR p_lease_seconds NOT BETWEEN 15 AND 300
OR NULLIF(btrim(p_worker_id),'') IS NULL THEN RETURN jsonb_build_object('outcome','stale_version'); END IF;
IF v_job.reconciliation_token IS NOT NULL AND v_job.reconciliation_lease_expires_at>clock_timestamp() THEN
RETURN jsonb_build_object('outcome','busy'); END IF; v_token:=gen_random_uuid();
UPDATE agent_sandbox_jobs SET reconciliation_worker_id=btrim(p_worker_id), reconciliation_token=v_token,
reconciliation_lease_expires_at=clock_timestamp() +make_interval(secs=>p_lease_seconds),
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','claimed','job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION renew_sandbox_job_reconciliation( p_job_id UUID, p_reconciliation_token UUID,
p_expected_version BIGINT, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE;
BEGIN PERFORM _assert_agent_sandbox_actor('sandbox_worker');
v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.reconciliation_token IS DISTINCT FROM p_reconciliation_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
IF v_job.status<>'unknown' OR v_job.state_version<>p_expected_version
OR v_job.reconciliation_lease_expires_at<=clock_timestamp() OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
RETURN jsonb_build_object('outcome','stale_version'); END IF; UPDATE agent_sandbox_jobs SET
reconciliation_lease_expires_at=clock_timestamp() +make_interval(secs=>p_lease_seconds),
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','renewed','job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION resolve_sandbox_job_reconciliation( p_job_id UUID, p_reconciliation_token UUID,
p_expected_version BIGINT, p_resolution TEXT, p_terminal_reason TEXT, p_receipt_hash TEXT, p_receipt JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; v_partial BOOLEAN; v_now TIMESTAMPTZ;
BEGIN PERFORM _assert_agent_sandbox_actor('sandbox_worker');
v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.reconciliation_token IS DISTINCT FROM p_reconciliation_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
IF v_job.status<>'unknown' OR v_job.state_version<>p_expected_version
OR v_job.reconciliation_lease_expires_at<=clock_timestamp() THEN
RETURN jsonb_build_object('outcome','stale_version'); END IF; IF p_resolution='still_unknown' THEN
UPDATE agent_sandbox_jobs SET reconciliation_worker_id=NULL,
reconciliation_token=NULL,reconciliation_lease_expires_at=NULL,
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','still_unknown','job',to_jsonb(v_job)); END IF;
IF p_receipt_hash !~ '^[0-9a-f]{64}$'
OR p_receipt_hash IS DISTINCT FROM _agent_sandbox_receipt_hash(p_receipt) THEN
RETURN jsonb_build_object('outcome','receipt_hash_conflict'); END IF;
IF p_resolution NOT IN ('succeeded','failed','timed_out','cancelled')
OR p_terminal_reason !~ '^[A-Z][A-Z0-9_]{0,199}$' OR p_receipt_hash !~ '^[0-9a-f]{64}$'
OR NOT _agent_sandbox_receipt_is_valid(p_receipt)
OR p_resolution='cancelled' AND v_job.cancel_confirmed_at IS NULL OR p_resolution='succeeded' AND (
p_receipt->>'materialization_status'<>'completed'
OR p_receipt->>'cleanup_status' NOT IN ('not_required','completed')
) THEN RETURN jsonb_build_object('outcome','terminal_guard_failed'); END IF;
v_partial:=jsonb_array_length(p_receipt->'partial_effects'->'items')>0;
v_now:=clock_timestamp();
IF p_receipt->'partial_effects' IS DISTINCT FROM v_job.partial_effects
OR p_receipt->>'cleanup_status' IS DISTINCT FROM v_job.cleanup_status
OR p_receipt->'cleanup_evidence' IS DISTINCT FROM v_job.cleanup_evidence
OR v_partial AND (
   v_job.cleanup_status<>'completed'
   OR NOT _agent_sandbox_evidence_is_valid(v_job.cleanup_evidence)
) THEN
RETURN jsonb_build_object('outcome','terminal_guard_failed'); END IF;
UPDATE agent_sandbox_jobs SET status=p_resolution,
terminal_at=clock_timestamp(),terminal_reason=p_terminal_reason,
receipt_hash=p_receipt_hash,receipt_revision=(p_receipt->>'receipt_revision')::INTEGER,
ambiguity_evidence='{}',execution_outcome=p_receipt->>'execution_outcome',
stdout_summary=p_receipt->>'stdout_summary',
stdout_original_length=(p_receipt->>'stdout_original_length')::BIGINT,
stdout_sha256=p_receipt->>'stdout_sha256',stdout_truncated=(p_receipt->>'stdout_truncated')::BOOLEAN,
stderr_summary=p_receipt->>'stderr_summary',
stderr_original_length=(p_receipt->>'stderr_original_length')::BIGINT,
stderr_sha256=p_receipt->>'stderr_sha256',stderr_truncated=(p_receipt->>'stderr_truncated')::BOOLEAN,
artifact_manifest=p_receipt->'artifact_manifest',partial_effects=v_job.partial_effects,
materialization_status=p_receipt->>'materialization_status',
materialization_receipt=COALESCE(p_receipt->'materialization_receipt','{}'),
cleanup_status=v_job.cleanup_status,cleanup_evidence=v_job.cleanup_evidence,
partial_effects_recorded_at=CASE WHEN v_partial THEN v_now
                                 ELSE partial_effects_recorded_at END,
cleanup_deadline_at=CASE WHEN v_partial THEN LEAST(
v_now+interval '24 hours',COALESCE((p_receipt->>'cleanup_deadline_at')::TIMESTAMPTZ,
v_now+interval '24 hours')) ELSE cleanup_deadline_at END,
reconciliation_worker_id=NULL,reconciliation_token=NULL,
reconciliation_lease_expires_at=NULL, state_version=state_version+1,updated_at=clock_timestamp()
WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome',p_resolution,'job',to_jsonb(v_job)); END; $$;
CREATE FUNCTION record_sandbox_job_cleanup( p_job_id UUID, p_reconciliation_token UUID,
p_expected_version BIGINT, p_cleanup_status TEXT, p_cleanup_evidence JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_job agent_sandbox_jobs%ROWTYPE; BEGIN PERFORM _assert_agent_sandbox_actor('sandbox_worker');
v_job:=_lock_agent_sandbox_job(p_job_id);
IF v_job.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
IF v_job.reconciliation_token IS DISTINCT FROM p_reconciliation_token THEN
RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
IF v_job.status<>'unknown' OR v_job.state_version<>p_expected_version
OR v_job.reconciliation_lease_expires_at<=clock_timestamp()
OR p_cleanup_status NOT IN ('running','completed','failed','unknown')
OR NOT _agent_sandbox_evidence_is_valid(p_cleanup_evidence) THEN
RETURN jsonb_build_object('outcome','invalid_transition'); END IF;
UPDATE agent_sandbox_jobs SET cleanup_status=p_cleanup_status, cleanup_attempts=cleanup_attempts+1,
cleanup_evidence=p_cleanup_evidence,
ambiguity_evidence=CASE WHEN p_cleanup_status IN ('completed','running') THEN ambiguity_evidence
ELSE jsonb_build_object('kind','SANDBOX_CLEANUP_UNPROVEN','reason_codes',
jsonb_build_array(p_cleanup_evidence->>'kind')) END,
state_version=state_version+1,updated_at=clock_timestamp() WHERE id=p_job_id RETURNING * INTO v_job;
RETURN jsonb_build_object('outcome','cleanup_'||p_cleanup_status, 'job',to_jsonb(v_job)); END; $$;
REVOKE ALL ON FUNCTION _assert_agent_sandbox_actor(TEXT),
_agent_sandbox_runtime_scope_ok(agent_runtime_sessions,agent_actions), _lock_agent_sandbox_job(UUID),
_agent_sandbox_runtime_job(agent_sandbox_jobs),
create_or_get_sandbox_job(UUID,UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,JSONB,JSONB),
get_sandbox_job(UUID), claim_next_sandbox_job(TEXT,INTEGER),
renew_sandbox_job_lease(UUID,UUID,BIGINT,BIGINT,INTEGER),
mark_sandbox_job_started(UUID,UUID,BIGINT,BIGINT,TEXT), recover_expired_sandbox_job(UUID,BIGINT),
request_sandbox_job_cancel(UUID,BIGINT), record_sandbox_cancel_signal(UUID,UUID,BIGINT,BIGINT,TEXT),
finish_sandbox_job(UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TEXT,JSONB), record_sandbox_job_unknown(
UUID,UUID,BIGINT,BIGINT,JSONB,JSONB,TIMESTAMPTZ),
claim_sandbox_job_reconciliation(UUID,BIGINT,TEXT,INTEGER),
renew_sandbox_job_reconciliation(UUID,UUID,BIGINT,INTEGER),
resolve_sandbox_job_reconciliation(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,JSONB),
record_sandbox_job_cleanup(UUID,UUID,BIGINT,TEXT,JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
everydayai_worker, everydayai_sandbox_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
create_or_get_sandbox_job(UUID,UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,JSONB,JSONB),
get_sandbox_job(UUID), request_sandbox_job_cancel(UUID,BIGINT) TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION get_sandbox_job(UUID), claim_next_sandbox_job(TEXT,INTEGER),
renew_sandbox_job_lease(UUID,UUID,BIGINT,BIGINT,INTEGER),
mark_sandbox_job_started(UUID,UUID,BIGINT,BIGINT,TEXT), recover_expired_sandbox_job(UUID,BIGINT),
record_sandbox_cancel_signal(UUID,UUID,BIGINT,BIGINT,TEXT),
finish_sandbox_job(UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TEXT,JSONB), record_sandbox_job_unknown(
UUID,UUID,BIGINT,BIGINT,JSONB,JSONB,TIMESTAMPTZ),
claim_sandbox_job_reconciliation(UUID,BIGINT,TEXT,INTEGER),
renew_sandbox_job_reconciliation(UUID,UUID,BIGINT,INTEGER),
resolve_sandbox_job_reconciliation(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,JSONB),
record_sandbox_job_cleanup(UUID,UUID,BIGINT,TEXT,JSONB) TO everydayai_sandbox_worker;
RESET ROLE;
