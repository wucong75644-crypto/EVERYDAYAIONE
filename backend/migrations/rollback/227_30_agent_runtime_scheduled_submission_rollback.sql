SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_submission_intents) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_SUBMISSION_ROLLBACK_FACTS_EXIST' USING ERRCODE='55000';
 END IF;
END $$;
REVOKE ALL ON FUNCTION worker_claim_due_scheduled_executions_v1(TIMESTAMPTZ,INTEGER),
 worker_assert_scheduled_task_legacy_owner_v1(UUID),
 request_agent_runtime_scheduled_execution_v1(TEXT,UUID,UUID,UUID,BIGINT,TIMESTAMPTZ),
 read_agent_runtime_scheduled_submission_v1(UUID,TEXT,TEXT) FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker;
DROP TRIGGER create_runtime_scheduled_profile_after_insert ON scheduled_tasks;
DROP FUNCTION _create_agent_runtime_scheduled_profile_after_insert();
DROP FUNCTION _agent_runtime_scheduled_profile_seed(UUID);
DROP TRIGGER bind_runtime_scheduled_run_after_claim ON agent_session_commands;
DROP FUNCTION _bind_agent_runtime_scheduled_run_after_claim();
DROP FUNCTION read_agent_runtime_scheduled_submission_v1(UUID,TEXT,TEXT);
DROP FUNCTION request_agent_runtime_scheduled_execution_v1(TEXT,UUID,UUID,UUID,BIGINT,TIMESTAMPTZ);
DROP FUNCTION worker_claim_due_scheduled_executions_v1(TIMESTAMPTZ,INTEGER);
DROP FUNCTION worker_assert_scheduled_task_legacy_owner_v1(UUID);
DROP FUNCTION _submit_agent_runtime_scheduled_execution_v1(UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,TIMESTAMPTZ);
DROP FUNCTION _agent_runtime_scheduled_gate_snapshot(UUID,TEXT,TEXT);
DROP FUNCTION _agent_runtime_scheduled_submission_enabled();
DROP FUNCTION _agent_runtime_scheduled_submission_worker();
DROP TRIGGER runtime_scheduled_submission_intent_immutable ON agent_runtime_scheduled_submission_intents;
DROP TABLE agent_runtime_scheduled_submission_intents;
DROP TABLE agent_runtime_scheduled_submission_control;

CREATE OR REPLACE FUNCTION _agent_command_run_envelope(p_command agent_session_commands)
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE v_envelope JSONB:=p_command.payload->'run_envelope';
BEGIN
 IF jsonb_typeof(v_envelope) IS DISTINCT FROM 'object'
 OR v_envelope='{}'::JSONB OR v_envelope->>'run_kind' NOT IN('user','continuation')
 OR jsonb_typeof(v_envelope->'context_receipt') IS DISTINCT FROM 'object'
 OR jsonb_typeof(v_envelope->'config_snapshot') IS DISTINCT FROM 'object'
 OR jsonb_typeof(v_envelope->'capability_snapshot') IS DISTINCT FROM 'object'
 OR jsonb_typeof(v_envelope->'request_identity') IS DISTINCT FROM 'object'
 OR v_envelope->'context_receipt'='{}'::JSONB OR v_envelope->'config_snapshot'='{}'::JSONB
 OR v_envelope->'capability_snapshot'='{}'::JSONB
 OR v_envelope->'request_identity'->>'session_id' IS DISTINCT FROM p_command.session_id::TEXT
 OR v_envelope->'request_identity'->>'idempotency_key' IS DISTINCT FROM p_command.idempotency_key
 OR pg_column_size(v_envelope)>262144 THEN RETURN NULL; END IF;
 RETURN v_envelope;
END $$;

CREATE OR REPLACE FUNCTION worker_claim_due_scheduled_tasks(p_now TIMESTAMPTZ,p_limit INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v_tasks JSONB;
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_now IS NULL OR p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN RAISE EXCEPTION 'SCHEDULED_WORKER_CLAIM_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 WITH claimed AS(UPDATE scheduled_tasks task SET status='running',next_run_at=NULL,updated_at=p_now
  WHERE task.id IN(SELECT candidate.id FROM scheduled_tasks candidate WHERE candidate.status='active'
   AND candidate.next_run_at IS NOT NULL AND candidate.next_run_at<=p_now
   AND(candidate.org_id IS NULL OR EXISTS(SELECT 1 FROM organizations o WHERE o.id=candidate.org_id AND o.status='active'))
   ORDER BY candidate.next_run_at LIMIT p_limit FOR UPDATE OF candidate SKIP LOCKED) RETURNING task.*)
 SELECT COALESCE(jsonb_agg(to_jsonb(claimed)),'[]'::JSONB) INTO v_tasks FROM claimed; RETURN v_tasks;
END $$;

CREATE OR REPLACE FUNCTION worker_create_scheduled_run(p_task_id UUID,p_lease_seconds INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v_task scheduled_tasks%ROWTYPE;v_run scheduled_task_runs%ROWTYPE;v_token UUID:=gen_random_uuid();
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_task_id IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN RAISE EXCEPTION 'SCHEDULED_RUN_CREATE_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO v_task FROM scheduled_tasks WHERE id=p_task_id AND status='running' FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_running'); END IF;
 IF EXISTS(SELECT 1 FROM scheduled_task_runs WHERE task_id=p_task_id AND status='running') THEN RETURN jsonb_build_object('outcome','already_running'); END IF;
 INSERT INTO scheduled_task_runs(task_id,org_id,status,execution_token,lease_expires_at)
 VALUES(v_task.id,v_task.org_id,'running',v_token,clock_timestamp()+make_interval(secs=>p_lease_seconds)) RETURNING * INTO v_run;
 RETURN jsonb_build_object('outcome','created','run',to_jsonb(v_run)-'execution_token','execution_token',v_token);
END $$;

CREATE OR REPLACE FUNCTION worker_list_stale_scheduled_tasks(p_cutoff TIMESTAMPTZ) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v_tasks JSONB;
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_cutoff IS NULL THEN RAISE EXCEPTION 'SCHEDULED_WORKER_STALE_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(task)),'[]'::JSONB) INTO v_tasks FROM scheduled_tasks task
 WHERE task.status='running' AND task.updated_at<p_cutoff; RETURN v_tasks;
END $$;
CREATE OR REPLACE FUNCTION worker_recover_stale_scheduled_task(
 p_task_id UUID,p_cutoff TIMESTAMPTZ,p_status TEXT,p_next_run_at TIMESTAMPTZ,p_now TIMESTAMPTZ) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF;
 IF p_task_id IS NULL OR p_cutoff IS NULL OR p_now IS NULL OR p_status NOT IN('active','paused') THEN
  RAISE EXCEPTION 'SCHEDULED_WORKER_RECOVER_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 UPDATE scheduled_tasks SET status=p_status,next_run_at=p_next_run_at,updated_at=p_now
 WHERE id=p_task_id AND status='running' AND updated_at<p_cutoff;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_recovered'); END IF;
 UPDATE scheduled_task_runs SET status='failed',error_message='进程异常退出，任务自动恢复',finished_at=p_now
 WHERE task_id=p_task_id AND status='running'; RETURN jsonb_build_object('outcome','recovered');
END $$;
RESET ROLE;
