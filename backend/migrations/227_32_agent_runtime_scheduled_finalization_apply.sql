-- 227_32: Atomically project a claimed Runtime terminal intent into scheduled facts.

SET LOCAL ROLE everydayai_owner;

ALTER TABLE agent_runtime_scheduled_finalization_intents
 ADD COLUMN application_request_id UUID,
 ADD COLUMN application_hash TEXT CHECK(application_hash IS NULL OR application_hash~'^[0-9a-f]{64}$'),
 ADD COLUMN application_receipt JSONB CHECK(application_receipt IS NULL OR
  (jsonb_typeof(application_receipt)='object' AND pg_column_size(application_receipt)<=8192)),
 ADD COLUMN applied_at TIMESTAMPTZ,
 ADD CONSTRAINT runtime_scheduled_finalization_application_shape CHECK(
  (status='applied')=(application_request_id IS NOT NULL AND application_hash IS NOT NULL
   AND application_receipt IS NOT NULL AND applied_at IS NOT NULL));

CREATE UNIQUE INDEX uq_runtime_scheduled_finalization_application_request
 ON agent_runtime_scheduled_finalization_intents(application_request_id)
 WHERE application_request_id IS NOT NULL;

CREATE FUNCTION _agent_runtime_scheduled_application_guard() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF OLD.status='applied' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_ALREADY_APPLIED_IMMUTABLE'
   USING ERRCODE='55000';
 END IF;
 IF (OLD.application_request_id,OLD.application_hash,OLD.application_receipt,OLD.applied_at)
 IS DISTINCT FROM
 (NEW.application_request_id,NEW.application_hash,NEW.application_receipt,NEW.applied_at) THEN
  IF NEW.status<>'applied' OR NEW.application_request_id IS NULL OR NEW.application_hash IS NULL
   OR NEW.application_receipt IS NULL OR NEW.applied_at IS NULL
   OR current_setting('app.agent_runtime_scheduled_application_request',TRUE)
    IS DISTINCT FROM NEW.application_request_id::TEXT THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_APPLY_RPC_REQUIRED' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.status='applied' AND OLD.status<>'applied' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_APPLICATION_REQUIRED' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_finalization_application_guard
 BEFORE UPDATE ON agent_runtime_scheduled_finalization_intents FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_application_guard();

CREATE FUNCTION _agent_runtime_scheduled_application_hash(
 p_scheduled_run_id UUID,p_request_id UUID,p_expected_intent_version BIGINT,
 p_expected_task_version BIGINT,p_schedule_hash TEXT,p_reason TEXT,p_next_run_at TIMESTAMPTZ)
RETURNS TEXT LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 SELECT encode(digest(convert_to(jsonb_build_object(
  'scheduled_run_id',p_scheduled_run_id,'request_id',p_request_id,
  'expected_intent_version',p_expected_intent_version,
  'expected_task_version',p_expected_task_version,'schedule_hash',p_schedule_hash,
  'reason',p_reason,'next_run_epoch',CASE WHEN p_next_run_at IS NULL THEN NULL
   ELSE extract(epoch FROM p_next_run_at) END)::TEXT,'UTF8'),'sha256'),'hex')
$$;

CREATE FUNCTION apply_agent_runtime_scheduled_finalization_v1(
 p_scheduled_run_id UUID,p_claim_token UUID,p_expected_intent_version BIGINT,
 p_expected_task_version BIGINT,p_schedule_hash TEXT,p_request_id UUID,
 p_reason TEXT,p_next_run_at TIMESTAMPTZ) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE i agent_runtime_scheduled_finalization_intents%ROWTYPE;
 b agent_runtime_scheduled_run_bindings%ROWTYPE;r agent_runs%ROWTYPE;
 q scheduled_task_runs%ROWTYPE;t scheduled_tasks%ROWTYPE;e agent_runtime_scheduled_execution_profiles%ROWTYPE;
 m agent_model_results%ROWTYPE;credits INTEGER:=0;tokens INTEGER:=0;duration INTEGER:=0;
 failures INTEGER;threshold INTEGER;attempts_used INTEGER;next_status TEXT;run_status TEXT;
 summary TEXT;result JSONB;artifacts JSONB:='[]'::JSONB;receipt JSONB;app_hash TEXT;
 terminal_baseline TIMESTAMPTZ;te BIGINT:=0;pe BIGINT:=0;ce BIGINT:=0;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_agent_runtime_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'agent_runtime' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_ACTOR_REQUIRED' USING ERRCODE='42501';
 END IF;
 IF p_scheduled_run_id IS NULL OR p_claim_token IS NULL OR p_request_id IS NULL
 OR p_expected_intent_version<0 OR p_expected_task_version<0
 OR p_schedule_hash !~ '^[0-9a-f]{64}$'
 OR btrim(COALESCE(p_reason,'')) NOT IN('runtime_finalizer','runtime_finalizer_recovery') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_APPLY_INVALID' USING ERRCODE='22023';
 END IF;
 app_hash:=_agent_runtime_scheduled_application_hash(p_scheduled_run_id,p_request_id,
  p_expected_intent_version,p_expected_task_version,p_schedule_hash,btrim(p_reason),p_next_run_at);

 SELECT * INTO i FROM agent_runtime_scheduled_finalization_intents
  WHERE scheduled_run_id=p_scheduled_run_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('scheduled-finalization-request:'||p_request_id,0));
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_finalization_intents other
  WHERE other.application_request_id=p_request_id AND other.scheduled_run_id<>p_scheduled_run_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_IDEMPOTENCY_CONFLICT' USING ERRCODE='55000';
 END IF;
 IF i.status='applied' THEN
  IF (i.application_request_id,i.application_hash) IS DISTINCT FROM(p_request_id,app_hash) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_IDEMPOTENCY_CONFLICT' USING ERRCODE='55000';
  END IF;
  RETURN i.application_receipt||jsonb_build_object('outcome','already_applied');
 END IF;
 IF i.status<>'claimed' OR i.claim_token IS DISTINCT FROM p_claim_token
 OR i.claim_lease_expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_CLAIM_FENCED' USING ERRCODE='40001';
 END IF;
 IF i.state_version IS DISTINCT FROM p_expected_intent_version THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_STALE_VERSION' USING ERRCODE='40001';
 END IF;

 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings
  WHERE scheduled_run_id=i.scheduled_run_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=i.runtime_run_id FOR UPDATE;
 SELECT * INTO q FROM scheduled_task_runs WHERE id=i.scheduled_run_id FOR UPDATE;
 SELECT * INTO t FROM scheduled_tasks WHERE id=i.scheduled_task_id FOR UPDATE;
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=i.scheduled_task_id;
 IF b.scheduled_run_id IS NULL OR r.id IS NULL OR q.id IS NULL OR t.id IS NULL OR e.scheduled_task_id IS NULL
 OR b.owner_kind<>'runtime' OR b.owner_status<>'reconcile_required'
 OR(b.runtime_run_id,b.runtime_command_id,b.scheduled_task_id,b.org_id,b.user_id)
   IS DISTINCT FROM(r.id,r.command_id,t.id,i.org_id,i.user_id)
 OR(q.task_id,q.org_id,q.status) IS DISTINCT FROM(t.id,i.org_id,'running')
 OR(t.org_id,t.user_id,t.status,t.runtime_state_version)
   IS DISTINCT FROM(i.org_id,i.user_id,'running',p_expected_task_version)
 OR b.task_revision IS DISTINCT FROM p_expected_task_version
 OR _runtime_scheduler_schedule_hash(t) IS DISTINCT FROM p_schedule_hash
 OR(r.status,r.state_version,r.result_hash)
   IS DISTINCT FROM(i.terminal_status,i.runtime_run_state_version,i.result_hash) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_SCOPE_FENCED' USING ERRCODE='40001';
 END IF;
 SELECT COALESCE(max(kill_epoch),0) INTO te FROM agent_runtime_tenant_gate_controls
  WHERE org_id=i.org_id AND gate_scope='tenant' AND scope_key='tenant';
 SELECT COALESCE(max(kill_epoch),0) INTO pe FROM agent_runtime_tenant_gate_controls
  WHERE org_id=i.org_id AND gate_scope='provider' AND scope_key=e.provider_key;
 SELECT COALESCE(max(kill_epoch),0) INTO ce FROM agent_runtime_tenant_gate_controls
  WHERE org_id=i.org_id AND gate_scope='capability' AND scope_key=e.capability_key;
 IF (b.tenant_kill_epoch,b.provider_kill_epoch,b.capability_kill_epoch,b.provider_revision,b.capability_revision)
 IS DISTINCT FROM(te,pe,ce,e.provider_revision,e.capability_revision) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_EPOCH_FENCED' USING ERRCODE='40001';
 END IF;
 terminal_baseline:=GREATEST(i.created_at,r.completed_at);

 SELECT COALESCE(sum(CASE WHEN c.status='adjusted' THEN c.adjusted_credits
  WHEN c.status='settled' THEN c.settled_credits ELSE 0 END),0)::INTEGER,
  COALESCE(sum(ms.input_tokens+ms.output_tokens+ms.reasoning_tokens),0)::INTEGER
 INTO credits,tokens FROM agent_model_steps ms
 LEFT JOIN agent_model_credit_settlements c ON c.model_step_id=ms.id WHERE ms.run_id=r.id;
 duration:=GREATEST(0,LEAST(2147483647,
  (extract(epoch FROM(COALESCE(r.completed_at,clock_timestamp())-
   COALESCE(r.started_at,r.created_at)))*1000)::BIGINT))::INTEGER;
 failures:=t.consecutive_failures+1;threshold:=GREATEST(3,t.retry_count+1);attempts_used:=failures-1;

 IF i.terminal_status='completed' THEN
  SELECT mr.* INTO m FROM agent_model_steps ms JOIN agent_model_results mr ON mr.model_step_id=ms.id
   WHERE ms.run_id=r.id AND ms.status='completed' AND ms.stop_reason IN('final','structured_final')
   ORDER BY ms.step_number DESC LIMIT 1;
  IF m.id IS NULL OR m.content_hash IS DISTINCT FROM i.result_hash THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_RESULT_FENCED' USING ERRCODE='55000';
  END IF;
  IF EXISTS(SELECT 1 FROM agent_action_artifact_links l
   JOIN agent_actions a ON a.id=l.action_id
   JOIN conversation_artifacts ca ON ca.id=l.artifact_id
   JOIN agent_runtime_sessions ars ON ars.id=r.session_id
   WHERE a.run_id=r.id AND(l.attempt_id<>ALL(SELECT aa.id FROM agent_action_attempts aa
    WHERE aa.action_id=a.id) OR a.org_id IS DISTINCT FROM i.org_id
    OR ca.org_id IS DISTINCT FROM i.org_id OR ca.conversation_id<>ars.conversation_id
    OR l.content_hash!~'^[0-9a-f]{64}$')) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_ARTIFACT_FENCED' USING ERRCODE='55000';
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('artifact_id',l.artifact_id,
   'content_hash',l.content_hash,'role',l.role,'materialize_status',l.materialize_status)
   ORDER BY l.created_at,l.artifact_id,l.role),'[]'::JSONB) INTO artifacts
  FROM agent_action_artifact_links l JOIN agent_actions a ON a.id=l.action_id
  JOIN conversation_artifacts ca ON ca.id=l.artifact_id
  JOIN agent_runtime_sessions ars ON ars.id=r.session_id
  WHERE a.run_id=r.id AND a.org_id IS NOT DISTINCT FROM i.org_id
   AND ca.org_id IS NOT DISTINCT FROM i.org_id AND ca.conversation_id=ars.conversation_id;
  next_status:=CASE WHEN t.schedule_type='once' THEN 'paused' ELSE 'active' END;
  IF (t.schedule_type='once' AND p_next_run_at IS NOT NULL)
   OR(t.schedule_type<>'once' AND p_next_run_at IS NULL) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_SCHEDULE_INVALID' USING ERRCODE='22023';
  END IF;
  run_status:='success';
  summary:=CASE WHEN m.output_kind='text' THEN NULLIF(btrim(regexp_replace(m.text_content,'\s+',' ','g')),'')
   WHEN jsonb_typeof(m.structured_content->'summary')='string'
    THEN NULLIF(btrim(regexp_replace(m.structured_content->>'summary','\s+',' ','g')),'') END;
  summary:=left(COALESCE(summary,'Runtime scheduled task completed'),500);
  result:=jsonb_build_object('runtime_run_id',r.id,'model_result_id',m.id,
   'output_kind',m.output_kind,'content_hash',m.content_hash,'artifacts',artifacts);
 ELSIF i.terminal_status='failed' THEN
  run_status:='failed';summary:=NULL;result:=NULL;
  IF failures>=threshold THEN next_status:='error';
  ELSIF attempts_used<t.retry_count THEN next_status:='active';
  ELSIF t.schedule_type='once' THEN next_status:='paused';
  ELSE next_status:='active'; END IF;
  IF (next_status IN('paused','error') AND p_next_run_at IS NOT NULL)
   OR(next_status='active' AND p_next_run_at IS NULL) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_SCHEDULE_INVALID' USING ERRCODE='22023';
  END IF;
 ELSE
  run_status:='skipped';summary:=NULL;result:=NULL;
  next_status:=CASE WHEN t.schedule_type='once' THEN 'paused' ELSE 'active' END;
  IF (t.schedule_type='once' AND p_next_run_at IS NOT NULL)
   OR(t.schedule_type<>'once' AND p_next_run_at IS NULL) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_SCHEDULE_INVALID' USING ERRCODE='22023';
  END IF;
 END IF;
 IF p_next_run_at IS NOT NULL AND p_next_run_at<=terminal_baseline THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_NEXT_RUN_INVALID' USING ERRCODE='22023';
 END IF;

 UPDATE scheduled_task_runs SET status=run_status,finished_at=clock_timestamp(),duration_ms=duration,
  result_summary=summary,result_files=NULL,push_status=NULL,
  error_message=CASE WHEN run_status='failed' THEN i.terminal_reason ELSE NULL END,
  credits_used=credits,tokens_used=tokens
  WHERE id=q.id;
 UPDATE scheduled_tasks SET status=next_status,next_run_at=p_next_run_at,last_run_at=clock_timestamp(),
  last_summary=CASE WHEN i.terminal_status='completed' THEN summary ELSE last_summary END,
  last_result=CASE WHEN i.terminal_status='completed' THEN result ELSE last_result END,
  run_count=run_count+CASE WHEN i.terminal_status='completed' THEN 1 ELSE 0 END,
  consecutive_failures=CASE WHEN i.terminal_status='completed' THEN 0
   WHEN i.terminal_status='failed' THEN failures ELSE consecutive_failures END,
  runtime_state_version=runtime_state_version+1,updated_at=clock_timestamp() WHERE id=t.id;
 UPDATE agent_runtime_scheduled_run_bindings SET owner_status=i.terminal_status,
  state_version=state_version+1,updated_at=clock_timestamp() WHERE scheduled_run_id=b.scheduled_run_id;
 receipt:=jsonb_build_object('scheduled_run_id',q.id,'scheduled_task_id',t.id,
  'terminal_status',i.terminal_status,'scheduled_run_status',run_status,'task_status',next_status,
  'credits_used',credits,'tokens_used',tokens,'duration_ms',duration,
  'task_state_version',t.runtime_state_version+1,'result_hash',i.result_hash,'reason',btrim(p_reason));
 PERFORM set_config('app.agent_runtime_scheduled_application_request',p_request_id::TEXT,TRUE);
 UPDATE agent_runtime_scheduled_finalization_intents SET status='applied',claim_worker_id=NULL,
  claim_token=NULL,claim_lease_expires_at=NULL,application_request_id=p_request_id,
  application_hash=app_hash,application_receipt=receipt,applied_at=clock_timestamp(),
  state_version=state_version+1,updated_at=clock_timestamp() WHERE scheduled_run_id=i.scheduled_run_id;
 RETURN receipt||jsonb_build_object('outcome','applied');
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_application_guard(),
 _agent_runtime_scheduled_application_hash(UUID,UUID,BIGINT,BIGINT,TEXT,TEXT,TIMESTAMPTZ),
 apply_agent_runtime_scheduled_finalization_v1(UUID,UUID,BIGINT,BIGINT,TEXT,UUID,TEXT,TIMESTAMPTZ)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION apply_agent_runtime_scheduled_finalization_v1(
 UUID,UUID,BIGINT,BIGINT,TEXT,UUID,TEXT,TIMESTAMPTZ) TO everydayai_agent_runtime_worker;

RESET ROLE;
