-- 227_31: Durable scheduled Runtime terminal intent and recoverable claim/readback.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_finalization_intents(
 scheduled_run_id UUID PRIMARY KEY REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
 runtime_run_id UUID NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 terminal_status TEXT NOT NULL CHECK(terminal_status IN('completed','failed','cancelled')),
 terminal_reason TEXT NOT NULL CHECK(length(terminal_reason) BETWEEN 1 AND 500),
 result_hash TEXT CHECK(result_hash IS NULL OR result_hash~'^[0-9a-f]{64}$'),
 runtime_run_state_version BIGINT NOT NULL CHECK(runtime_run_state_version>0),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN(
  'pending','claimed','applied','reconcile_required')),
 claim_worker_id TEXT CHECK(claim_worker_id IS NULL OR length(btrim(claim_worker_id)) BETWEEN 1 AND 200),
 claim_token UUID,claim_lease_expires_at TIMESTAMPTZ,
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK((status='claimed')=(claim_worker_id IS NOT NULL AND claim_token IS NOT NULL
  AND claim_lease_expires_at IS NOT NULL)),
 CHECK((terminal_status='completed')=(result_hash IS NOT NULL))
);
CREATE INDEX idx_runtime_scheduled_finalization_claim
 ON agent_runtime_scheduled_finalization_intents(status,claim_lease_expires_at,created_at,scheduled_run_id)
 WHERE status IN('pending','claimed','reconcile_required');
ALTER TABLE agent_runtime_scheduled_finalization_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_finalization_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_finalization_owner_all
 ON agent_runtime_scheduled_finalization_intents FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_runtime_scheduled_finalization_intents
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker;

CREATE FUNCTION _agent_runtime_scheduled_terminal_reason(p_reason TEXT,p_status TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 SELECT CASE WHEN COALESCE(NULLIF(btrim(p_reason),''),p_status)=ANY(ARRAY[
  'completed','failed','cancelled','command_attempts_exhausted','attempts_exhausted',
  'cancelled_before_start','runtime_cancel','task_cancel_requested','parent_cancelled',
  'parent_run_cancelled','user_cancelled','model_step_failed','model_step_nonfinal',
  'length','content_filter','model_refusal','budget_exhausted','provider_error',
  'protocol_error'
 ]::TEXT[]) THEN COALESCE(NULLIF(btrim(p_reason),''),p_status)
 ELSE 'redacted_terminal_reason' END
$$;

CREATE FUNCTION _agent_runtime_scheduled_finalization_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_FACT_IMMUTABLE' USING ERRCODE='55000';
 END IF;
 IF (OLD.scheduled_run_id,OLD.runtime_run_id,OLD.scheduled_task_id,OLD.org_id,OLD.user_id,
  OLD.terminal_status,OLD.terminal_reason,OLD.result_hash,OLD.runtime_run_state_version,OLD.created_at)
 IS DISTINCT FROM
 (NEW.scheduled_run_id,NEW.runtime_run_id,NEW.scheduled_task_id,NEW.org_id,NEW.user_id,
  NEW.terminal_status,NEW.terminal_reason,NEW.result_hash,NEW.runtime_run_state_version,NEW.created_at) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_IDENTITY_IMMUTABLE' USING ERRCODE='55000';
 END IF;
 IF NEW.state_version<=OLD.state_version THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_VERSION_INVALID' USING ERRCODE='40001';
 END IF;
 IF NEW.status='claimed' AND current_setting(
   'app.agent_runtime_scheduled_finalization_claim',TRUE) IS DISTINCT FROM NEW.claim_token::TEXT THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_CLAIM_RPC_REQUIRED' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_finalization_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_finalization_intents FOR EACH ROW
 EXECUTE FUNCTION _agent_runtime_scheduled_finalization_immutable();

CREATE FUNCTION _capture_agent_runtime_scheduled_terminal_intent() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE b agent_runtime_scheduled_run_bindings%ROWTYPE;q scheduled_task_runs%ROWTYPE;
 t scheduled_tasks%ROWTYPE;i agent_runtime_scheduled_finalization_intents%ROWTYPE;
 reason TEXT;
BEGIN
 IF OLD.status IN('completed','failed','cancelled')
 OR NEW.status NOT IN('completed','failed','cancelled') OR NEW.run_kind<>'scheduled' THEN
  RETURN NEW;
 END IF;
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings
  WHERE runtime_run_id=NEW.id FOR UPDATE;
 IF NOT FOUND OR b.owner_kind<>'runtime' OR b.runtime_command_id IS DISTINCT FROM NEW.command_id
 OR b.org_id IS DISTINCT FROM NEW.org_id OR b.user_id IS DISTINCT FROM NEW.user_id THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TERMINAL_BINDING_REQUIRED' USING ERRCODE='55000';
 END IF;
 SELECT * INTO q FROM scheduled_task_runs WHERE id=b.scheduled_run_id FOR SHARE;
 SELECT * INTO t FROM scheduled_tasks WHERE id=b.scheduled_task_id FOR SHARE;
 IF q.id IS NULL OR t.id IS NULL OR(q.task_id,q.org_id,q.status)
   IS DISTINCT FROM(b.scheduled_task_id,b.org_id,'running')
 OR(t.org_id,t.user_id) IS DISTINCT FROM(b.org_id,b.user_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TERMINAL_IDENTITY_CONFLICT' USING ERRCODE='55000';
 END IF;
 reason:=_agent_runtime_scheduled_terminal_reason(NEW.terminal_reason,NEW.status);
 INSERT INTO agent_runtime_scheduled_finalization_intents(
  scheduled_run_id,runtime_run_id,scheduled_task_id,org_id,user_id,terminal_status,
  terminal_reason,result_hash,runtime_run_state_version)
 VALUES(b.scheduled_run_id,NEW.id,b.scheduled_task_id,b.org_id,b.user_id,NEW.status,
  reason,NEW.result_hash,NEW.state_version)
 ON CONFLICT(scheduled_run_id) DO NOTHING RETURNING * INTO i;
 IF i.scheduled_run_id IS NULL THEN
  SELECT * INTO i FROM agent_runtime_scheduled_finalization_intents
   WHERE scheduled_run_id=b.scheduled_run_id;
 END IF;
 IF (i.runtime_run_id,i.scheduled_task_id,i.org_id,i.user_id,i.terminal_status,
  i.terminal_reason,i.result_hash,i.runtime_run_state_version) IS DISTINCT FROM
 (NEW.id,b.scheduled_task_id,b.org_id,b.user_id,NEW.status,reason,NEW.result_hash,NEW.state_version) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TERMINAL_INTENT_CONFLICT' USING ERRCODE='55000';
 END IF;
 UPDATE agent_runtime_scheduled_run_bindings SET owner_status='reconcile_required',
  state_version=state_version+1,updated_at=clock_timestamp()
  WHERE scheduled_run_id=b.scheduled_run_id;
 RETURN NEW;
END $$;
CREATE TRIGGER capture_runtime_scheduled_terminal_intent
 AFTER UPDATE OF status ON agent_runs FOR EACH ROW
 EXECUTE FUNCTION _capture_agent_runtime_scheduled_terminal_intent();

DO $$
DECLARE bad RECORD;
BEGIN
 SELECT r.id INTO bad FROM agent_runs r
 LEFT JOIN agent_runtime_scheduled_run_bindings b ON b.runtime_run_id=r.id
 LEFT JOIN scheduled_task_runs q ON q.id=b.scheduled_run_id
 LEFT JOIN scheduled_tasks t ON t.id=b.scheduled_task_id
 WHERE r.run_kind='scheduled' AND r.status IN('completed','failed','cancelled') AND(
  b.scheduled_run_id IS NULL OR b.owner_kind<>'runtime'
  OR b.runtime_command_id IS DISTINCT FROM r.command_id
  OR(b.org_id,b.user_id) IS DISTINCT FROM(r.org_id,r.user_id)
  OR q.id IS NULL OR(q.task_id,q.org_id,q.status) IS DISTINCT FROM(b.scheduled_task_id,b.org_id,'running')
  OR t.id IS NULL OR(t.org_id,t.user_id) IS DISTINCT FROM(b.org_id,b.user_id))
 LIMIT 1;
 IF FOUND THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TERMINAL_BACKFILL_CONFLICT: %',bad.id USING ERRCODE='55000';
 END IF;
 INSERT INTO agent_runtime_scheduled_finalization_intents(
  scheduled_run_id,runtime_run_id,scheduled_task_id,org_id,user_id,terminal_status,
  terminal_reason,result_hash,runtime_run_state_version)
 SELECT b.scheduled_run_id,r.id,b.scheduled_task_id,b.org_id,b.user_id,r.status,
  _agent_runtime_scheduled_terminal_reason(r.terminal_reason,r.status),r.result_hash,r.state_version
 FROM agent_runs r JOIN agent_runtime_scheduled_run_bindings b ON b.runtime_run_id=r.id
 WHERE r.run_kind='scheduled' AND r.status IN('completed','failed','cancelled');
 UPDATE agent_runtime_scheduled_run_bindings b SET owner_status='reconcile_required',
  state_version=b.state_version+1,updated_at=clock_timestamp()
 FROM agent_runtime_scheduled_finalization_intents i
 WHERE i.scheduled_run_id=b.scheduled_run_id AND b.owner_status<>'reconcile_required';
END $$;

CREATE FUNCTION _agent_runtime_scheduled_finalization_payload(
 p_intent agent_runtime_scheduled_finalization_intents) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE b agent_runtime_scheduled_run_bindings%ROWTYPE;t scheduled_tasks%ROWTYPE;
 r agent_runs%ROWTYPE;s agent_model_steps%ROWTYPE;m agent_model_results%ROWTYPE;
 usage_input JSONB;cost_input JSONB;model_input JSONB;
BEGIN
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=p_intent.scheduled_run_id;
 SELECT * INTO t FROM scheduled_tasks WHERE id=p_intent.scheduled_task_id;
 SELECT * INTO r FROM agent_runs WHERE id=p_intent.runtime_run_id;
 IF b.scheduled_run_id IS NULL OR t.id IS NULL OR r.id IS NULL OR b.owner_kind<>'runtime'
 OR(b.runtime_run_id,b.runtime_command_id,b.scheduled_task_id,b.org_id,b.user_id)
   IS DISTINCT FROM(r.id,r.command_id,p_intent.scheduled_task_id,p_intent.org_id,p_intent.user_id)
 OR(t.org_id,t.user_id) IS DISTINCT FROM(p_intent.org_id,p_intent.user_id)
 OR(r.status,r.result_hash,r.state_version) IS DISTINCT FROM
   (p_intent.terminal_status,p_intent.result_hash,p_intent.runtime_run_state_version) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_SCOPE_CONFLICT' USING ERRCODE='55000';
 END IF;
 SELECT * INTO s FROM agent_model_steps WHERE run_id=r.id ORDER BY step_number DESC LIMIT 1;
 IF s.id IS NOT NULL THEN SELECT * INTO m FROM agent_model_results WHERE model_step_id=s.id; END IF;
 IF p_intent.terminal_status='completed' AND(s.id IS NULL OR s.status<>'completed'
  OR s.stop_reason NOT IN('final','structured_final') OR m.id IS NULL
  OR m.run_id<>r.id OR m.session_id<>r.session_id OR m.content_hash IS DISTINCT FROM r.result_hash) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_MODEL_RESULT_INVALID' USING ERRCODE='55000';
 END IF;
 SELECT jsonb_build_object('input_tokens',COALESCE(sum(input_tokens),0),
  'output_tokens',COALESCE(sum(output_tokens),0),'reasoning_tokens',COALESCE(sum(reasoning_tokens),0),
  'total_tokens',COALESCE(sum(input_tokens+output_tokens+reasoning_tokens),0)) INTO usage_input
 FROM agent_model_steps WHERE run_id=r.id;
 SELECT jsonb_build_object('settled_credits',COALESCE(sum(c.settled_credits),0),
  'adjusted_credits',COALESCE(sum(c.adjusted_credits),0),
  'effective_credits',COALESCE(sum(CASE WHEN c.status='adjusted' THEN c.adjusted_credits
   WHEN c.status='settled' THEN c.settled_credits ELSE 0 END),0),
  'settlement_count',count(c.id)) INTO cost_input
 FROM agent_model_steps ms LEFT JOIN agent_model_credit_settlements c ON c.model_step_id=ms.id
 WHERE ms.run_id=r.id;
 model_input:=CASE WHEN m.id IS NULL THEN NULL ELSE jsonb_build_object(
  'id',m.id,'model_step_id',m.model_step_id,'output_kind',m.output_kind,
  'text_content',m.text_content,'structured_content',m.structured_content,
  'schema_revision',m.schema_revision,'content_hash',m.content_hash) END;
 RETURN jsonb_build_object('outcome','found','intent',jsonb_build_object(
  'scheduled_run_id',p_intent.scheduled_run_id,'runtime_run_id',p_intent.runtime_run_id,
  'scheduled_task_id',p_intent.scheduled_task_id,'org_id',p_intent.org_id,'user_id',p_intent.user_id,
  'terminal_status',p_intent.terminal_status,'terminal_reason',p_intent.terminal_reason,
  'result_hash',p_intent.result_hash,'runtime_run_state_version',p_intent.runtime_run_state_version,
  'status',p_intent.status,'claim_worker_id',p_intent.claim_worker_id,
  'claim_token',p_intent.claim_token,'claim_lease_expires_at',p_intent.claim_lease_expires_at,
  'attempt_count',p_intent.attempt_count,'state_version',p_intent.state_version,
  'created_at',p_intent.created_at,'updated_at',p_intent.updated_at),
  'binding',to_jsonb(b),'task_schedule',jsonb_build_object('task_id',t.id,
  'state_version',t.runtime_state_version,'status',t.status,'schedule_type',t.schedule_type,
  'cron_expr',t.cron_expr,'timezone',t.timezone,'run_at',t.run_at,'weekdays',t.weekdays,
  'day_of_month',t.day_of_month,'schedule_hash',_runtime_scheduler_schedule_hash(t)),
  'model_result',model_input,'usage_projection_input',usage_input,'cost_projection_input',cost_input);
END $$;

CREATE FUNCTION claim_next_agent_runtime_scheduled_finalization_v1(
 p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 90) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE i agent_runtime_scheduled_finalization_intents%ROWTYPE;token UUID:=gen_random_uuid();
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_agent_runtime_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'agent_runtime' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_ACTOR_REQUIRED' USING ERRCODE='42501';
 END IF;
 IF NULLIF(btrim(p_worker_id),'') IS NULL OR length(btrim(p_worker_id))>200
 OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_CLAIM_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO i FROM agent_runtime_scheduled_finalization_intents
 WHERE status IN('pending','reconcile_required')
 OR(status='claimed' AND claim_lease_expires_at<=clock_timestamp())
 ORDER BY created_at,scheduled_run_id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM set_config('app.agent_runtime_scheduled_finalization_claim',token::TEXT,TRUE);
 UPDATE agent_runtime_scheduled_finalization_intents SET status='claimed',
  claim_worker_id=btrim(p_worker_id),claim_token=token,
 claim_lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
  attempt_count=attempt_count+1,state_version=state_version+1,updated_at=clock_timestamp()
 WHERE scheduled_run_id=i.scheduled_run_id AND state_version=i.state_version AND(
  status IN('pending','reconcile_required')
  OR(status='claimed' AND claim_lease_expires_at<=clock_timestamp()))
 RETURNING * INTO i;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','claim_conflict'); END IF;
 RETURN _agent_runtime_scheduled_finalization_payload(i)||jsonb_build_object('outcome','claimed');
END $$;

CREATE FUNCTION read_agent_runtime_scheduled_finalization_v1(
 p_scheduled_run_id UUID,p_claim_token UUID DEFAULT NULL) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE i agent_runtime_scheduled_finalization_intents%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_agent_runtime_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'agent_runtime' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_ACTOR_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO i FROM agent_runtime_scheduled_finalization_intents
  WHERE scheduled_run_id=p_scheduled_run_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF i.status='claimed' AND i.claim_lease_expires_at>clock_timestamp()
 AND i.claim_token IS DISTINCT FROM p_claim_token THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 RETURN _agent_runtime_scheduled_finalization_payload(i);
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_terminal_reason(TEXT,TEXT),
 _agent_runtime_scheduled_finalization_immutable(),
 _capture_agent_runtime_scheduled_terminal_intent(),
 _agent_runtime_scheduled_finalization_payload(agent_runtime_scheduled_finalization_intents),
 claim_next_agent_runtime_scheduled_finalization_v1(TEXT,INTEGER),
 read_agent_runtime_scheduled_finalization_v1(UUID,UUID)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION claim_next_agent_runtime_scheduled_finalization_v1(TEXT,INTEGER),
 read_agent_runtime_scheduled_finalization_v1(UUID,UUID)
 TO everydayai_agent_runtime_worker;

RESET ROLE;
