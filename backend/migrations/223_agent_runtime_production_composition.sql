-- 223: production ownership, rollout control and V3 authorization binding.
-- LOGIN roles and credentials are bootstrap-only.
SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE role_name TEXT;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'everydayai_agent_runtime_worker','everydayai_projection_worker',
    'everydayai_authorization_worker','everydayai_runtime_admin'
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
      RAISE EXCEPTION 'AGENT_RUNTIME_PRODUCTION_ROLE_MISSING: %',role_name;
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION tenant_platform_admin()
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT (
   (session_user='everydayai_runtime'
    AND current_setting('app.access_kind',true)='runtime')
   OR
   (session_user='everydayai_runtime_admin'
    AND current_setting('app.access_kind',true)='runtime_admin')
  )
  AND tenant_actor_user_id() IS NOT NULL
  AND EXISTS(SELECT 1 FROM users app_user
   WHERE app_user.id=tenant_actor_user_id()
    AND app_user.role::text='super_admin'
    AND app_user.status::text='active')
$$;
GRANT EXECUTE ON FUNCTION tenant_platform_admin()
 TO everydayai_runtime_admin;

CREATE OR REPLACE FUNCTION _assert_agent_runtime_actor(p_worker BOOLEAN)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE kind TEXT:=current_setting('app.access_kind',true);
BEGIN
  IF p_worker AND NOT (
    (session_user='everydayai_agent_runtime_worker' AND kind='agent_runtime') OR
    (session_user='everydayai_projection_worker' AND kind='projection') OR
    (session_user='everydayai_authorization_worker' AND kind='authorization')
  ) THEN RAISE EXCEPTION 'AGENT_RUNTIME_WORKER_SCOPE_REQUIRED'
    USING ERRCODE='42501';
  ELSIF NOT p_worker AND (
    session_user NOT IN ('everydayai_runtime','everydayai_wecom_runtime')
    OR kind<>'runtime'
  ) THEN RAISE EXCEPTION 'AGENT_RUNTIME_REQUEST_SCOPE_REQUIRED'
    USING ERRCODE='42501';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION _assert_agent_sandbox_actor(p_kind TEXT)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
  IF (p_kind='runtime' AND (
      session_user<>'everydayai_agent_runtime_worker' OR
      current_setting('app.access_kind',true)<>'agent_runtime'))
    OR (p_kind='sandbox_worker' AND (
      session_user<>'everydayai_sandbox_worker' OR
      current_setting('app.access_kind',true)<>'sandbox_worker'))
    OR p_kind NOT IN ('runtime','sandbox_worker') THEN
    RAISE EXCEPTION 'AGENT_SANDBOX_ACTOR_SCOPE_REQUIRED'
      USING ERRCODE='42501';
  END IF;
END $$;

CREATE TABLE agent_runtime_control(
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
  ingress_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  command_claim_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  action_dispatch_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  safe_actions_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  non_safe_actions_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  code_execute_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  projection_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  authorization_recovery_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  tool_confirmation_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  release_revision TEXT NOT NULL DEFAULT 'disabled',
  config_revision TEXT NOT NULL DEFAULT 'disabled',
  updated_by UUID REFERENCES users(id) ON DELETE RESTRICT,
  update_reason TEXT NOT NULL DEFAULT 'migration_default',
  state_version BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO agent_runtime_control(singleton) VALUES(TRUE);

CREATE TABLE agent_runtime_org_rollout(
  org_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE RESTRICT,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  updated_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  update_reason TEXT NOT NULL CHECK(length(btrim(update_reason)) BETWEEN 1 AND 500),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE agent_runtime_worker_heartbeats(
  process_role TEXT NOT NULL CHECK(process_role IN
    ('agent_runtime','projection','authorization','sandbox')),
  worker_id TEXT NOT NULL, release_revision TEXT NOT NULL,
  ready BOOLEAN NOT NULL, draining BOOLEAN NOT NULL,
  status_code TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}',
  observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(process_role,worker_id)
);
CREATE TABLE agent_runtime_capabilities(
 capability_name TEXT PRIMARY KEY,
 reporter_role TEXT NOT NULL,
 ready BOOLEAN NOT NULL,
 evidence JSONB NOT NULL DEFAULT '{}',
 observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE agent_tool_confirmation_results(
  confirmation_id TEXT PRIMARY KEY CHECK(length(confirmation_id) BETWEEN 32 AND 200),
  interaction_id UUID NOT NULL UNIQUE REFERENCES agent_interactions(id),
  action_id UUID NOT NULL UNIQUE REFERENCES agent_actions(id),
  user_id UUID NOT NULL REFERENCES users(id),
  org_id UUID REFERENCES organizations(id),
  arguments_hash TEXT NOT NULL CHECK(arguments_hash ~ '^[0-9a-f]{64}$'),
  decision TEXT NOT NULL CHECK(decision IN ('approve','deny')),
  binding_hash TEXT NOT NULL UNIQUE CHECK(binding_hash ~ '^[0-9a-f]{64}$'),
  expires_at TIMESTAMPTZ NOT NULL,
  resolved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE agent_interactions
 ADD COLUMN confirmation_notification_worker TEXT,
 ADD COLUMN confirmation_notification_token UUID,
 ADD COLUMN confirmation_notification_lease_expires_at TIMESTAMPTZ,
 ADD COLUMN confirmation_notification_not_before TIMESTAMPTZ,
 ADD COLUMN confirmation_notified_at TIMESTAMPTZ,
 ADD CHECK((confirmation_notification_token IS NULL)=
   (confirmation_notification_lease_expires_at IS NULL));
CREATE TABLE _agent_runtime_223_grant_snapshot(
 role_name TEXT NOT NULL,
 function_signature TEXT NOT NULL,
 had_execute BOOLEAN NOT NULL,
 PRIMARY KEY(role_name,function_signature)
);
CREATE TABLE agent_runtime_admin_audit(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 request_id UUID NOT NULL UNIQUE,
 actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 operation TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 500),
 request_payload JSONB NOT NULL,
 result_payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION _agent_runtime_admin_audit_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'AGENT_RUNTIME_ADMIN_AUDIT_IMMUTABLE'
 USING ERRCODE='55000'; END $$;
CREATE TRIGGER agent_runtime_admin_audit_immutable
 BEFORE UPDATE OR DELETE ON agent_runtime_admin_audit
 FOR EACH ROW EXECUTE FUNCTION _agent_runtime_admin_audit_immutable();
INSERT INTO _agent_runtime_223_grant_snapshot
SELECT role_name,p.oid::regprocedure::text,
 has_function_privilege(role_name,p.oid,'EXECUTE')
FROM unnest(ARRAY['everydayai_worker','everydayai_runtime',
 'everydayai_wecom_runtime','everydayai_sync','everydayai'])
 AS roles(role_name)
CROSS JOIN pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND (
 p.proname LIKE '%agent%' OR p.proname LIKE '%model%'
 OR p.proname LIKE '%sandbox%');

ALTER TABLE agent_runtime_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_org_rollout ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_worker_heartbeats ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_capabilities ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_tool_confirmation_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE _agent_runtime_223_grant_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_admin_audit ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_control_owner ON agent_runtime_control
  FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY agent_runtime_rollout_owner ON agent_runtime_org_rollout
  FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY agent_runtime_heartbeat_owner ON agent_runtime_worker_heartbeats
  FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY agent_runtime_capabilities_owner ON agent_runtime_capabilities
  FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY agent_tool_confirmation_owner ON agent_tool_confirmation_results
  FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY agent_runtime_223_snapshot_owner
  ON _agent_runtime_223_grant_snapshot
  FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY agent_runtime_admin_audit_owner ON agent_runtime_admin_audit
  FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
ALTER TABLE agent_runtime_control FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_org_rollout FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_worker_heartbeats FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_capabilities FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_tool_confirmation_results FORCE ROW LEVEL SECURITY;
ALTER TABLE _agent_runtime_223_grant_snapshot FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_admin_audit FORCE ROW LEVEL SECURITY;

ALTER FUNCTION _agent_compat_project_command(agent_runtime_events)
 RENAME TO _agent_compat_project_command_220_12;
ALTER FUNCTION _agent_compat_project_completed_run(
 agent_runs,agent_runtime_sessions,agent_session_commands,tasks)
 RENAME TO _agent_compat_project_completed_run_220_12;
ALTER FUNCTION _agent_compat_project_run(agent_runtime_events,TEXT)
 RENAME TO _agent_compat_project_run_220_12;

CREATE FUNCTION _agent_compat_project_command(p_event agent_runtime_events)
RETURNS UUID LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE c agent_session_commands%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
 m messages%ROWTYPE; anchor UUID;
BEGIN
 SELECT * INTO c FROM agent_session_commands WHERE id=p_event.correlation_id;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=p_event.session_id;
 anchor:=nullif(c.payload->>'input_message_id','')::uuid;
 IF c.id IS NULL OR c.session_id<>p_event.session_id OR s.id IS NULL
   OR c.command_type<>'submit_input' OR anchor IS NULL THEN
  RAISE EXCEPTION 'AGENT_COMPAT_PREPARED_INPUT_REQUIRED' USING ERRCODE='55000';
 END IF;
 SELECT * INTO m FROM messages WHERE id=anchor FOR UPDATE;
 IF m.id IS NULL OR m.conversation_id<>s.conversation_id
   OR m.org_id IS DISTINCT FROM s.org_id OR m.role::text<>'user'
   OR m.turn_id IS DISTINCT FROM nullif(c.payload->>'turn_id','')::uuid THEN
  RAISE EXCEPTION 'AGENT_COMPAT_PREPARED_INPUT_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN m.id;
END $$;

CREATE FUNCTION _agent_compat_project_completed_run(
 p_run agent_runs,p_session agent_runtime_sessions,
 p_command agent_session_commands,p_task tasks
) RETURNS UUID LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE r agent_model_results%ROWTYPE; step agent_model_steps%ROWTYPE;
 m messages%ROWTYPE; content TEXT; anchor UUID;
BEGIN
 SELECT * INTO step FROM agent_model_steps WHERE run_id=p_run.id
  ORDER BY step_number DESC LIMIT 1;
 SELECT * INTO r FROM agent_model_results WHERE model_step_id=step.id;
 anchor:=nullif(p_command.payload->>'output_message_id','')::uuid;
 IF p_run.status<>'completed' OR step.status<>'completed'
   OR step.stop_reason NOT IN('final','structured_final') OR r.id IS NULL
   OR r.content_hash IS DISTINCT FROM p_run.result_hash OR anchor IS NULL THEN
  RAISE EXCEPTION 'AGENT_COMPAT_MODEL_RESULT_INVALID' USING ERRCODE='55000';
 END IF;
 content:=CASE WHEN r.output_kind='text'
  THEN jsonb_build_array(jsonb_build_object('type','text','text',r.text_content))::text
  ELSE jsonb_build_array(jsonb_build_object('type','data','data',r.structured_content))::text END;
 SELECT * INTO m FROM messages WHERE id=anchor FOR UPDATE;
 IF m.id IS NULL OR m.conversation_id<>p_session.conversation_id
   OR m.org_id IS DISTINCT FROM p_run.org_id OR m.role::text<>'assistant'
   OR m.turn_id IS DISTINCT FROM nullif(p_command.payload->>'turn_id','')::uuid THEN
  RAISE EXCEPTION 'AGENT_COMPAT_PREPARED_OUTPUT_CONFLICT' USING ERRCODE='55000';
 END IF;
 UPDATE messages SET content=content,status='completed',credits_cost=0
  WHERE id=m.id RETURNING * INTO m;
 UPDATE tasks SET status='completed',assistant_message_id=m.id,
  credits_used=(SELECT coalesce(sum(cs.settled_credits),0)
   FROM agent_model_credit_settlements cs
   JOIN agent_model_steps ms ON ms.id=cs.model_step_id
   WHERE ms.run_id=p_run.id),
  result=jsonb_build_object('runtime_run_id',p_run.id,
   'model_result_id',r.id,'content_hash',r.content_hash),
  completed_at=coalesce(p_run.completed_at,clock_timestamp())
  WHERE id=p_task.id;
 RETURN m.id;
END $$;

CREATE FUNCTION _agent_compat_project_run(
 p_event agent_runtime_events,p_action TEXT,
 OUT projected_message_id UUID,OUT projected_task_id UUID,
 OUT projected_delivery_id UUID
) LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
 c agent_session_commands%ROWTYPE; t tasks%ROWTYPE; v_status TEXT; anchor UUID;
BEGIN
 SELECT * INTO r FROM agent_runs WHERE id=p_event.run_id FOR UPDATE;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
 SELECT * INTO c FROM agent_session_commands WHERE id=r.command_id;
 anchor:=nullif(c.payload->>'task_id','')::uuid;
 SELECT * INTO t FROM tasks WHERE id=anchor FOR UPDATE;
 IF r.id IS NULL OR r.session_id<>p_event.session_id OR s.id IS NULL
   OR c.session_id<>s.id OR t.id IS NULL
   OR t.conversation_id<>s.conversation_id
   OR t.org_id IS DISTINCT FROM r.org_id
   OR NOT t.delivery_context @> '{"runtime":true}'::jsonb THEN
  RAISE EXCEPTION 'AGENT_COMPAT_PREPARED_TASK_CONFLICT' USING ERRCODE='55000';
 END IF;
 v_status:=CASE p_action WHEN 'run_pending' THEN 'pending'
  WHEN 'run_running' THEN 'running' WHEN 'run_waiting' THEN 'running'
  WHEN 'run_completed' THEN 'completed' WHEN 'run_failed' THEN 'failed'
  WHEN 'run_cancelled' THEN 'cancelled' END;
 IF v_status IS NULL THEN RAISE EXCEPTION 'AGENT_COMPAT_RUN_ACTION_INVALID'; END IF;
 IF p_action='run_completed' THEN
  projected_message_id:=_agent_compat_project_completed_run(r,s,c,t);
 ELSE
  UPDATE tasks SET status=v_status,
   error_message=CASE WHEN v_status='failed' THEN r.terminal_reason ELSE error_message END,
   completed_at=CASE WHEN v_status IN('failed','cancelled')
    THEN coalesce(r.completed_at,clock_timestamp()) ELSE NULL END
   WHERE id=t.id;
 END IF;
 projected_task_id:=t.id;
 IF v_status IN('completed','failed')
   AND t.delivery_context @> '{"channel":"wecom"}'::jsonb THEN
  INSERT INTO conversation_deliveries(task_id,channel,delivery_kind,target_context)
  VALUES(t.id,'wecom','assistant_terminal',t.delivery_context)
  ON CONFLICT(task_id,channel,delivery_kind) DO NOTHING
  RETURNING id INTO projected_delivery_id;
  IF projected_delivery_id IS NULL THEN
   SELECT id INTO projected_delivery_id FROM conversation_deliveries
    WHERE task_id=t.id AND channel='wecom'
     AND delivery_kind='assistant_terminal';
  END IF;
 END IF;
END $$;

ALTER FUNCTION gate_agent_action_dispatch(
 UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT)
 RENAME TO _gate_agent_action_dispatch_220_24;
REVOKE ALL ON FUNCTION _gate_agent_action_dispatch_220_24(
 UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT)
 FROM PUBLIC,everydayai_worker,everydayai_runtime,everydayai_wecom_runtime,
 everydayai_sync,everydayai;
CREATE FUNCTION gate_agent_action_dispatch(
 p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,
 p_request_hash TEXT,p_policy_receipt_id UUID,p_executor_type TEXT,
 p_executor_revision INTEGER,p_policy_revision TEXT,p_recovery_mode TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c agent_runtime_control%ROWTYPE; a agent_actions%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO c FROM agent_runtime_control WHERE singleton FOR SHARE;
 SELECT action.* INTO a FROM agent_actions action
  JOIN agent_action_attempts attempt ON attempt.action_id=action.id
  WHERE attempt.id=p_attempt_id;
 IF NOT c.action_dispatch_enabled OR a.id IS NULL
   OR NOT EXISTS(SELECT 1 FROM agent_runtime_org_rollout r
     WHERE r.org_id=a.org_id AND r.enabled)
   OR (a.tool_name='code_execute' AND
     (NOT c.code_execute_enabled OR NOT c.non_safe_actions_enabled
      OR NOT c.tool_confirmation_enabled
      OR a.policy_snapshot->>'safety_level'='safe'
      OR NOT EXISTS(SELECT 1 FROM agent_runtime_capabilities cap
       WHERE cap.capability_name='tool_confirmation_v3_redis' AND cap.ready
        AND cap.observed_at>clock_timestamp()-interval '60 seconds')
      OR NOT EXISTS(SELECT 1 FROM agent_runtime_worker_heartbeats h
       WHERE h.process_role='sandbox' AND h.ready AND NOT h.draining
        AND h.observed_at>clock_timestamp()-interval '30 seconds')))
   OR (a.tool_name<>'code_execute'
     AND a.policy_snapshot->>'safety_level'='safe'
     AND NOT c.safe_actions_enabled)
   OR (a.tool_name<>'code_execute'
     AND a.policy_snapshot->>'safety_level'<>'safe' AND (
     NOT c.non_safe_actions_enabled OR NOT c.tool_confirmation_enabled
     OR NOT EXISTS(SELECT 1 FROM agent_runtime_capabilities cap
       WHERE cap.capability_name='tool_confirmation_v3_redis' AND cap.ready
        AND cap.observed_at>clock_timestamp()-interval '60 seconds')))
   OR a.policy_snapshot->>'safety_level' IS NULL THEN
   RETURN jsonb_build_object('outcome','dispatch_gate_disabled');
 END IF;
 RETURN _gate_agent_action_dispatch_220_24(
  p_attempt_id,p_execution_token,p_expected_attempt_version,p_request_hash,
  p_policy_receipt_id,p_executor_type,p_executor_revision,p_policy_revision,
  p_recovery_mode);
END $$;

CREATE FUNCTION runtime_submit_ingress(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,
 p_scope_id TEXT,p_created_by_user_id UUID,p_agent_definition_id TEXT,
 p_agent_definition_revision TEXT,p_command_type TEXT,
 p_idempotency_key TEXT,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE ctl agent_runtime_control%ROWTYPE; s JSONB; c JSONB; sid UUID;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 SELECT * INTO ctl FROM agent_runtime_control WHERE singleton FOR SHARE;
 IF NOT ctl.ingress_enabled THEN
   UPDATE tasks SET delivery_context=delivery_context||
     '{"actor":true,"runtime":false}'::jsonb
    WHERE id=NULLIF(p_payload->>'task_id','')::uuid
      AND conversation_id=p_conversation_id AND user_id=p_user_id
      AND org_id IS NOT DISTINCT FROM p_org_id
      AND delivery_context @> '{"runtime":true}'::jsonb;
   RETURN jsonb_build_object('outcome','ingress_disabled');
 END IF;
 IF p_org_id IS NULL OR NOT EXISTS(
   SELECT 1 FROM agent_runtime_org_rollout WHERE org_id=p_org_id AND enabled
 ) THEN
   UPDATE tasks SET delivery_context=delivery_context||
     '{"actor":true,"runtime":false}'::jsonb
    WHERE id=NULLIF(p_payload->>'task_id','')::uuid
      AND conversation_id=p_conversation_id AND user_id=p_user_id
      AND org_id IS NOT DISTINCT FROM p_org_id
      AND delivery_context @> '{"runtime":true}'::jsonb;
   RETURN jsonb_build_object('outcome','org_not_enabled');
 END IF;
 s:=ensure_agent_runtime_session(p_conversation_id,p_org_id,p_user_id,
   p_scope_kind,p_scope_id,p_created_by_user_id,p_agent_definition_id,
   p_agent_definition_revision);
 IF s->>'outcome' NOT IN ('created','already_exists') THEN RETURN s; END IF;
 sid:=(s->>'entity_id')::uuid;
 c:=submit_session_command(sid,p_command_type,p_idempotency_key,p_payload||
   jsonb_build_object('release_revision',ctl.release_revision,
                      'config_revision',ctl.config_revision));
 IF c->>'outcome' IN ('created','already_exists') THEN
   UPDATE tasks SET delivery_context=delivery_context||
     '{"actor":false,"runtime":true}'::jsonb
    WHERE id=NULLIF(p_payload->>'task_id','')::uuid
      AND conversation_id=p_conversation_id AND user_id=p_user_id
      AND org_id IS NOT DISTINCT FROM p_org_id;
 END IF;
 RETURN c||jsonb_build_object('session_id',sid);
END $$;

CREATE FUNCTION claim_agent_tool_confirmation_notification(
 p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE i agent_interactions%ROWTYPE; a agent_actions%ROWTYPE;
 c agent_session_commands%ROWTYPE; token UUID;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
   OR current_setting('app.access_kind',true)<>'projection'
   OR nullif(btrim(p_worker_id),'') IS NULL
   OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
  RAISE EXCEPTION 'TOOL_CONFIRMATION_NOTIFICATION_SCOPE_REQUIRED'
   USING ERRCODE='42501';
 END IF;
 SELECT interaction.* INTO i
 FROM agent_interactions interaction
 JOIN agent_actions action ON action.id=interaction.action_id
 JOIN agent_runtime_org_rollout rollout ON rollout.org_id=interaction.org_id
 CROSS JOIN agent_runtime_control control
 WHERE interaction.status='open' AND interaction.expires_at>clock_timestamp()
  AND interaction.confirmation_notified_at IS NULL
  AND (interaction.confirmation_notification_not_before IS NULL OR
   interaction.confirmation_notification_not_before<=clock_timestamp())
  AND (interaction.confirmation_notification_token IS NULL OR
   interaction.confirmation_notification_lease_expires_at<=clock_timestamp())
  AND action.status='awaiting_authorization'
  AND rollout.enabled AND control.tool_confirmation_enabled
 ORDER BY interaction.created_at,interaction.id
 LIMIT 1 FOR UPDATE OF interaction SKIP LOCKED;
 IF i.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO a FROM agent_actions WHERE id=i.action_id;
 SELECT command.* INTO c FROM agent_session_commands command
  WHERE command.id=(SELECT run.command_id FROM agent_runs run WHERE run.id=i.run_id);
 IF nullif(c.payload->>'task_id','') IS NULL THEN
  UPDATE agent_interactions SET
   confirmation_notification_not_before=
    clock_timestamp()+interval '300 seconds'
   WHERE id=i.id;
  RETURN jsonb_build_object('outcome','invalid_task_binding');
 END IF;
 token:=gen_random_uuid();
 UPDATE agent_interactions SET
  confirmation_notification_worker=btrim(p_worker_id),
  confirmation_notification_token=token,
  confirmation_notification_lease_expires_at=
   clock_timestamp()+make_interval(secs=>p_lease_seconds)
  WHERE id=i.id;
 RETURN jsonb_build_object('outcome','claimed','notification_token',token,
  'interaction_id',i.id,'interaction_version',i.state_version,
  'authorization_expires_at',i.expires_at,'action_id',a.id,
  'task_id',c.payload->>'task_id','conversation_id',
   (SELECT s.conversation_id FROM agent_runtime_sessions s WHERE s.id=i.session_id),
  'tool_call_id',a.stable_tool_call_id,'tool_name',a.tool_name,
  'arguments',a.arguments,'arguments_hash',a.arguments_hash,
  'user_id',i.user_id,'org_id',i.org_id);
END $$;

CREATE FUNCTION complete_agent_tool_confirmation_notification(
 p_interaction_id UUID,p_notification_token UUID,p_delivered BOOLEAN
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE i agent_interactions%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
  OR current_setting('app.access_kind',true)<>'projection' THEN
  RAISE EXCEPTION 'TOOL_CONFIRMATION_NOTIFICATION_SCOPE_REQUIRED'
   USING ERRCODE='42501';
 END IF;
 SELECT * INTO i FROM agent_interactions WHERE id=p_interaction_id FOR UPDATE;
 IF i.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF i.confirmation_notification_token IS DISTINCT FROM p_notification_token
  OR i.confirmation_notification_lease_expires_at<=clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','ownership_lost');
 END IF;
 UPDATE agent_interactions SET
  confirmation_notified_at=CASE WHEN p_delivered THEN clock_timestamp()
   ELSE confirmation_notified_at END,
  confirmation_notification_not_before=CASE WHEN p_delivered THEN NULL
   ELSE clock_timestamp()+interval '120 seconds' END,
  confirmation_notification_worker=NULL,
  confirmation_notification_token=NULL,
  confirmation_notification_lease_expires_at=NULL
  WHERE id=i.id;
 RETURN jsonb_build_object('outcome',
  CASE WHEN p_delivered THEN 'completed' ELSE 'released' END);
END $$;

CREATE FUNCTION resolve_agent_tool_confirmation_v3(
 p_confirmation_id TEXT,p_interaction_id UUID,p_action_id UUID,
 p_expected_interaction_version BIGINT,p_user_id UUID,p_org_id UUID,
 p_arguments_hash TEXT,p_expires_at TIMESTAMPTZ,p_approved BOOLEAN
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE i agent_interactions%ROWTYPE; a agent_actions%ROWTYPE;
 e agent_tool_confirmation_results%ROWTYPE;
 ctl agent_runtime_control%ROWTYPE;
 d TEXT:=CASE WHEN p_approved THEN 'approve' ELSE 'deny' END;
 bh TEXT; rh TEXT; r JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 SELECT * INTO ctl FROM agent_runtime_control WHERE singleton FOR SHARE;
 IF NOT ctl.tool_confirmation_enabled THEN
   RETURN jsonb_build_object('outcome','confirmation_disabled');
 END IF;
 IF length(coalesce(p_confirmation_id,'')) NOT BETWEEN 32 AND 200
   OR p_arguments_hash !~ '^[0-9a-f]{64}$'
   OR p_expires_at<=clock_timestamp() THEN
   RETURN jsonb_build_object('outcome','confirmation_expired_or_invalid');
 END IF;
 SELECT * INTO i FROM agent_interactions WHERE id=p_interaction_id FOR UPDATE;
 SELECT * INTO a FROM agent_actions WHERE id=p_action_id FOR UPDATE;
 IF i.id IS NULL OR a.id IS NULL OR i.action_id IS DISTINCT FROM a.id
   OR i.user_id IS DISTINCT FROM p_user_id OR i.org_id IS DISTINCT FROM p_org_id
   OR a.user_id IS DISTINCT FROM p_user_id OR a.org_id IS DISTINCT FROM p_org_id
   OR a.arguments_hash IS DISTINCT FROM p_arguments_hash
   OR i.expires_at IS DISTINCT FROM p_expires_at
   OR tenant_actor_user_id() IS DISTINCT FROM p_user_id
   OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
   RETURN jsonb_build_object('outcome','binding_mismatch');
 END IF;
 bh:=encode(digest(concat_ws(':',p_confirmation_id,p_interaction_id,p_action_id,
   p_user_id,coalesce(p_org_id::text,''),p_arguments_hash,
   extract(epoch from p_expires_at),d),'sha256'),'hex');
 SELECT * INTO e FROM agent_tool_confirmation_results
  WHERE confirmation_id=p_confirmation_id;
 IF FOUND THEN
   RETURN jsonb_build_object('outcome',CASE WHEN e.binding_hash=bh
     THEN 'already_resolved' ELSE 'confirmation_conflict' END);
 END IF;
 IF i.status<>'open' OR i.state_version<>p_expected_interaction_version
   OR i.expires_at<=clock_timestamp() THEN
   RETURN jsonb_build_object('outcome','stale_or_expired');
 END IF;
 rh:=encode(digest(bh,'sha256'),'hex');
 r:=resolve_agent_authorization_interaction(p_interaction_id,
   p_expected_interaction_version,d,rh,'{}'::jsonb,'action',NULL,
   greatest(30,least(86400,extract(epoch from
     p_expires_at-clock_timestamp())::integer)));
 IF r->>'outcome' NOT IN ('resolved','already_resolved') THEN RETURN r; END IF;
 INSERT INTO agent_tool_confirmation_results(
   confirmation_id,interaction_id,action_id,user_id,org_id,arguments_hash,
   decision,binding_hash,expires_at)
 VALUES(p_confirmation_id,p_interaction_id,p_action_id,p_user_id,p_org_id,
   p_arguments_hash,d,bh,p_expires_at);
 RETURN jsonb_build_object('outcome','resolved');
END $$;

CREATE FUNCTION enqueue_wecom_runtime_turn_v3(
 p_task_data JSONB,p_input_message_id UUID,p_output_message_id UUID,
 p_turn_id UUID,p_input_content JSONB,p_delivery_context JSONB,
 p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE e JSONB; r JSONB; conversation_id UUID; user_id UUID; org_id UUID;
 d JSONB; scope_kind TEXT; scope_id TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 e:=enqueue_wecom_generation_turn_v2(
   p_task_data,p_input_message_id,p_output_message_id,p_turn_id,
   p_input_content,p_delivery_context);
 SELECT t.conversation_id,t.user_id,t.org_id INTO conversation_id,user_id,org_id
   FROM tasks t WHERE t.id=(e->>'task_id')::uuid FOR UPDATE;
 SELECT c.scope_type,c.scope_id INTO scope_kind,scope_id
   FROM conversations c WHERE c.id=conversation_id;
 r:=runtime_submit_ingress(
   conversation_id,org_id,user_id,scope_kind,scope_id,user_id,
   p_agent_definition_id,p_agent_definition_revision,'submit_input',
   p_idempotency_key,jsonb_build_object(
    'schema_revision',1,'channel','wecom','task_id',e->>'task_id',
    'input_message_id',p_input_message_id,'output_message_id',p_output_message_id,
    'turn_id',p_turn_id,'content',p_input_content,
    'delivery_context',p_delivery_context||'{"actor":false,"runtime":true}'::jsonb));
 IF r->>'outcome' IN ('ingress_disabled','org_not_enabled') THEN
   RETURN e||jsonb_build_object('runtime_owned',false);
 END IF;
 IF r->>'outcome' NOT IN('created','already_exists') THEN
   RAISE EXCEPTION 'WECOM_RUNTIME_INGRESS_FAILED: %',r->>'outcome'
     USING ERRCODE='55000';
 END IF;
 d:=p_delivery_context||'{"actor":false,"runtime":true}'::jsonb;
 UPDATE tasks SET delivery_context=d WHERE id=(e->>'task_id')::uuid;
 RETURN e||jsonb_build_object('runtime_owned',true,
   'runtime_session_id',r->>'session_id','runtime_command_id',r->>'entity_id');
END $$;

CREATE FUNCTION complete_model_attempt_with_raw_actions(
 p_attempt_id UUID,p_run_execution_token UUID,
 p_expected_attempt_version BIGINT,p_expected_step_version BIGINT,
 p_request_hash TEXT,p_response_receipt JSONB,p_response_hash TEXT,
 p_provider_stop_reason TEXT,p_usage JSONB,p_actual_credits INTEGER,
 p_actions JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE step agent_model_steps%ROWTYPE; canonical JSONB; batch_hash TEXT;
 result JSONB; action agent_actions%ROWTYPE; prompt JSONB; opened JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT ms.* INTO step FROM agent_model_steps ms
  JOIN agent_model_attempts ma ON ma.model_step_id=ms.id
  WHERE ma.id=p_attempt_id;
 IF step.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 canonical:=_canonical_agent_action_batch(step,p_actions);
 batch_hash:=_agent_action_batch_hash(canonical);
 result:=complete_model_attempt_step_and_create_actions(
  p_attempt_id,p_run_execution_token,p_expected_attempt_version,
  p_expected_step_version,p_request_hash,p_response_receipt,p_response_hash,
  p_provider_stop_reason,p_usage,p_actual_credits,batch_hash,p_actions);
 IF result->>'outcome' NOT IN ('completed','already_completed') THEN
  RETURN result;
 END IF;
 FOR action IN SELECT a.* FROM agent_actions a
   WHERE a.model_step_id=step.id AND a.status='awaiting_authorization'
   ORDER BY a.action_index,a.id
 LOOP
  prompt:=jsonb_build_object(
   'protocol_version',3,'action_id',action.id,
   'tool_call_id',action.stable_tool_call_id,
   'tool_name',action.tool_name,'arguments',action.arguments,
   'arguments_hash',action.arguments_hash);
  opened:=open_agent_authorization_interaction(
   action.id,action.state_version,prompt,
   encode(digest(convert_to(prompt::text,'UTF8'),'sha256'),'hex'),900);
  IF opened->>'outcome' NOT IN ('opened','already_open') THEN
   RAISE EXCEPTION 'AGENT_AUTHORIZATION_INTERACTION_OPEN_FAILED: %',
    opened->>'outcome' USING ERRCODE='55000';
  END IF;
 END LOOP;
 RETURN result;
END $$;

CREATE FUNCTION get_agent_runtime_model_context(
 p_run_id UUID,p_worker_id TEXT,p_execution_token UUID
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
 c agent_session_commands%ROWTYPE; messages JSONB; task JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO r FROM agent_runs WHERE id=p_run_id;
 IF r.id IS NULL OR r.status<>'running'
   OR r.execution_token IS DISTINCT FROM p_execution_token
   OR r.lease_expires_at<=clock_timestamp()
   OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra
     WHERE ra.run_id=r.id AND ra.execution_token=p_execution_token
      AND ra.worker_id=btrim(p_worker_id) AND ra.ended_at IS NULL) THEN
  RETURN jsonb_build_object('outcome','ownership_lost');
 END IF;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
 SELECT * INTO c FROM agent_session_commands WHERE id=r.command_id;
 SELECT to_jsonb(t) INTO task FROM tasks t
  WHERE t.id=nullif(c.payload->>'task_id','')::uuid
    AND t.conversation_id=s.conversation_id
    AND t.org_id IS NOT DISTINCT FROM s.org_id;
 SELECT coalesce(jsonb_agg(jsonb_build_object(
   'id',m.id,'role',m.role,'content',m.content,'turn_id',m.turn_id)
   ORDER BY m.created_at,m.id),'[]'::jsonb) INTO messages
 FROM messages m WHERE m.conversation_id=s.conversation_id
  AND m.org_id IS NOT DISTINCT FROM s.org_id
  AND (m.status='completed' OR m.id=nullif(c.payload->>'input_message_id','')::uuid);
 RETURN jsonb_build_object('outcome','found','session',to_jsonb(s),
  'run',to_jsonb(r),'command',to_jsonb(c),'task',task,'messages',messages);
END $$;

CREATE FUNCTION get_agent_runtime_ai_bundle(
 p_run_id UUID,p_worker_id TEXT,p_execution_token UUID,p_bundle_name TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_bundle_name NOT IN (
  'ai.provider.dashscope','ai.provider.openrouter',
  'ai.provider.kie','ai.provider.google') THEN
  RAISE EXCEPTION 'CONFIG_BUNDLE_UNKNOWN' USING ERRCODE='22023';
 END IF;
 SELECT * INTO r FROM agent_runs WHERE id=p_run_id;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
 IF r.id IS NULL OR s.id IS NULL OR r.status<>'running'
   OR r.execution_token IS DISTINCT FROM p_execution_token
   OR r.lease_expires_at<=clock_timestamp()
   OR tenant_actor_user_id() IS DISTINCT FROM s.user_id
   OR tenant_org_id() IS DISTINCT FROM s.org_id
   OR NOT EXISTS(SELECT 1 FROM agent_run_attempts ra
     WHERE ra.run_id=r.id AND ra.execution_token=p_execution_token
      AND ra.worker_id=btrim(p_worker_id) AND ra.ended_at IS NULL) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_CREDENTIAL_SCOPE_INVALID'
    USING ERRCODE='42501';
 END IF;
 RETURN _resolve_configuration_bundle(
  'v1',p_bundle_name,s.user_id,s.org_id);
END $$;

CREATE FUNCTION report_agent_runtime_worker_heartbeat(
 p_process_role TEXT,p_worker_id TEXT,p_release_revision TEXT,
 p_ready BOOLEAN,p_draining BOOLEAN,p_status_code TEXT,p_details JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE er TEXT; ea TEXT;
BEGIN
 er:=CASE p_process_role WHEN 'agent_runtime' THEN 'everydayai_agent_runtime_worker'
   WHEN 'projection' THEN 'everydayai_projection_worker'
   WHEN 'authorization' THEN 'everydayai_authorization_worker'
   WHEN 'sandbox' THEN 'everydayai_sandbox_worker' END;
 ea:=CASE p_process_role WHEN 'sandbox' THEN 'sandbox_worker' ELSE p_process_role END;
 IF er IS NULL OR session_user<>er OR
   current_setting('app.access_kind',true)<>ea THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_HEARTBEAT_SCOPE_INVALID' USING ERRCODE='42501';
 END IF;
 INSERT INTO agent_runtime_worker_heartbeats VALUES(
   p_process_role,btrim(p_worker_id),btrim(p_release_revision),p_ready,
   p_draining,btrim(p_status_code),p_details,clock_timestamp())
 ON CONFLICT(process_role,worker_id) DO UPDATE SET
   release_revision=excluded.release_revision,ready=excluded.ready,
   draining=excluded.draining,status_code=excluded.status_code,
   details=excluded.details,observed_at=excluded.observed_at;
 RETURN jsonb_build_object('outcome','recorded');
END $$;

CREATE FUNCTION report_agent_runtime_capability(
 p_capability_name TEXT,p_ready BOOLEAN,p_evidence JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_runtime'
   OR current_setting('app.access_kind',true)<>'runtime'
   OR p_capability_name<>'tool_confirmation_v3_redis'
   OR jsonb_typeof(p_evidence)<>'object' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_CAPABILITY_REPORT_INVALID'
    USING ERRCODE='42501';
 END IF;
 INSERT INTO agent_runtime_capabilities(
  capability_name,reporter_role,ready,evidence,observed_at)
 VALUES(p_capability_name,session_user,p_ready,p_evidence,clock_timestamp())
 ON CONFLICT(capability_name) DO UPDATE SET
  reporter_role=excluded.reporter_role,ready=excluded.ready,
  evidence=excluded.evidence,observed_at=excluded.observed_at;
 RETURN jsonb_build_object('outcome','recorded');
END $$;

CREATE FUNCTION get_agent_runtime_admin_status()
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user<>'everydayai_runtime_admin' OR
   current_setting('app.access_kind',true)<>'runtime_admin' OR
   NOT tenant_platform_admin() THEN
   RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 RETURN jsonb_build_object(
 'control',(SELECT to_jsonb(c) FROM agent_runtime_control c WHERE singleton),
  'rollout',(SELECT coalesce(jsonb_agg(to_jsonb(r)),'[]') FROM agent_runtime_org_rollout r
    WHERE r.org_id=tenant_org_id()),
  'workers',(SELECT coalesce(jsonb_agg(to_jsonb(h)),'[]') FROM agent_runtime_worker_heartbeats h),
  'capabilities',(SELECT coalesce(jsonb_agg(to_jsonb(cap)),'[]')
    FROM agent_runtime_capabilities cap),
  'projection',jsonb_build_object(
    'backlog',(SELECT count(*) FROM agent_projection_outbox WHERE org_id=tenant_org_id()
      AND status IN('pending','processing')),
    'dead',(SELECT count(*) FROM agent_projection_outbox WHERE org_id=tenant_org_id()
      AND status='dead'),
    'oldest_at',(SELECT min(created_at) FROM agent_projection_outbox
      WHERE org_id=tenant_org_id() AND status='pending')),
  'unknown',jsonb_build_object(
    'actions',(SELECT count(*) FROM agent_action_attempts aa JOIN agent_actions a
      ON a.id=aa.action_id WHERE a.org_id=tenant_org_id() AND aa.status='unknown'),
    'action_attempts_total',(SELECT count(*) FROM agent_action_attempts aa
      JOIN agent_actions a ON a.id=aa.action_id
      WHERE a.org_id=tenant_org_id()),
    'model_attempts',(SELECT count(*) FROM agent_model_attempts ma
      JOIN agent_model_steps ms ON ms.id=ma.model_step_id
      JOIN agent_runs ar ON ar.id=ms.run_id
      WHERE ar.org_id=tenant_org_id() AND ma.status='unknown'),
    'model_attempts_total',(SELECT count(*) FROM agent_model_attempts ma
      JOIN agent_model_steps ms ON ms.id=ma.model_step_id
      JOIN agent_runs ar ON ar.id=ms.run_id
      WHERE ar.org_id=tenant_org_id()),
    'sandbox_jobs',(SELECT count(*) FROM agent_sandbox_jobs
      WHERE org_id=tenant_org_id() AND status='unknown'),
    'sandbox_jobs_total',(SELECT count(*) FROM agent_sandbox_jobs
      WHERE org_id=tenant_org_id())));
END $$;

CREATE FUNCTION admin_requeue_agent_projection_dead(
 p_outbox_id UUID,p_expected_status TEXT,p_expected_recovery_version BIGINT,
 p_expected_attempt_count INTEGER,p_recovery_request_id UUID,p_reason TEXT,
 p_not_before TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE prior agent_runtime_admin_audit%ROWTYPE; result JSONB;
 actor UUID:=tenant_actor_user_id(); org UUID:=tenant_org_id();
BEGIN
 IF session_user<>'everydayai_runtime_admin' OR
   current_setting('app.access_kind',true)<>'runtime_admin' OR
   NOT tenant_platform_admin() OR org IS NULL OR actor IS NULL THEN
   RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO prior FROM agent_runtime_admin_audit
  WHERE request_id=p_recovery_request_id;
 IF FOUND THEN
  IF prior.actor_user_id<>actor OR prior.org_id<>org
    OR prior.operation<>'requeue_projection_dead'
    OR prior.reason<>btrim(p_reason)
    OR prior.request_payload<>jsonb_build_object(
      'outbox_id',p_outbox_id,'expected_status',p_expected_status,
      'expected_recovery_version',p_expected_recovery_version,
      'expected_attempt_count',p_expected_attempt_count,
      'not_before',p_not_before) THEN
   RETURN jsonb_build_object('outcome','idempotency_conflict');
  END IF;
  RETURN prior.result_payload||jsonb_build_object('outcome','already_applied');
 END IF;
 result:=requeue_agent_projection_dead(
   p_outbox_id,p_expected_status,p_expected_recovery_version,
   p_expected_attempt_count,p_recovery_request_id,p_reason,p_not_before);
 INSERT INTO agent_runtime_admin_audit(
  request_id,actor_user_id,org_id,operation,reason,request_payload,result_payload)
 VALUES(p_recovery_request_id,actor,org,'requeue_projection_dead',
  btrim(p_reason),jsonb_build_object(
    'outbox_id',p_outbox_id,'expected_status',p_expected_status,
    'expected_recovery_version',p_expected_recovery_version,
    'expected_attempt_count',p_expected_attempt_count,
    'not_before',p_not_before),result);
 RETURN result;
END $$;

CREATE FUNCTION set_agent_runtime_control(
 p_request_id UUID,p_expected_state_version BIGINT,p_patch JSONB,p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c agent_runtime_control%ROWTYPE; prior agent_runtime_admin_audit%ROWTYPE;
 result JSONB; actor UUID:=tenant_actor_user_id(); org UUID:=tenant_org_id();
BEGIN
 IF session_user<>'everydayai_runtime_admin'
   OR current_setting('app.access_kind',true)<>'runtime_admin'
   OR NOT tenant_platform_admin() OR org IS NULL OR actor IS NULL THEN
  RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO prior FROM agent_runtime_admin_audit WHERE request_id=p_request_id;
 IF FOUND THEN
  IF prior.actor_user_id<>actor OR prior.org_id<>org
    OR prior.operation<>'set_control'
    OR prior.reason<>btrim(p_reason)
    OR prior.request_payload<>jsonb_build_object(
      'expected_state_version',p_expected_state_version,'patch',p_patch) THEN
   RETURN jsonb_build_object('outcome','idempotency_conflict');
  END IF;
  RETURN prior.result_payload||jsonb_build_object('outcome','already_applied');
 END IF;
 IF jsonb_typeof(p_patch)<>'object'
   OR p_patch-(ARRAY['ingress_enabled','command_claim_enabled',
    'action_dispatch_enabled','safe_actions_enabled',
    'non_safe_actions_enabled','code_execute_enabled',
    'projection_enabled','authorization_recovery_enabled',
    'tool_confirmation_enabled','release_revision','config_revision'])<>'{}'::jsonb
   OR length(btrim(p_reason)) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_CONTROL_PATCH_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO c FROM agent_runtime_control WHERE singleton FOR UPDATE;
 IF c.state_version<>p_expected_state_version THEN
  RETURN jsonb_build_object('outcome','stale_version',
    'state_version',c.state_version);
 END IF;
 UPDATE agent_runtime_control SET
  ingress_enabled=coalesce((p_patch->>'ingress_enabled')::boolean,ingress_enabled),
  command_claim_enabled=coalesce((p_patch->>'command_claim_enabled')::boolean,command_claim_enabled),
  action_dispatch_enabled=coalesce((p_patch->>'action_dispatch_enabled')::boolean,action_dispatch_enabled),
  safe_actions_enabled=coalesce((p_patch->>'safe_actions_enabled')::boolean,safe_actions_enabled),
  non_safe_actions_enabled=coalesce((p_patch->>'non_safe_actions_enabled')::boolean,non_safe_actions_enabled),
  code_execute_enabled=coalesce((p_patch->>'code_execute_enabled')::boolean,code_execute_enabled),
  projection_enabled=coalesce((p_patch->>'projection_enabled')::boolean,projection_enabled),
  authorization_recovery_enabled=coalesce((p_patch->>'authorization_recovery_enabled')::boolean,authorization_recovery_enabled),
  tool_confirmation_enabled=coalesce((p_patch->>'tool_confirmation_enabled')::boolean,tool_confirmation_enabled),
  release_revision=coalesce(nullif(p_patch->>'release_revision',''),release_revision),
  config_revision=coalesce(nullif(p_patch->>'config_revision',''),config_revision),
  updated_by=actor,update_reason=btrim(p_reason),
  state_version=state_version+1,updated_at=clock_timestamp()
  WHERE singleton RETURNING * INTO c;
 IF c.code_execute_enabled AND (
   NOT c.non_safe_actions_enabled OR NOT c.tool_confirmation_enabled
   OR NOT EXISTS(SELECT 1 FROM agent_runtime_capabilities cap
     WHERE cap.capability_name='tool_confirmation_v3_redis' AND cap.ready
      AND cap.observed_at>clock_timestamp()-interval '60 seconds')
   OR NOT EXISTS(SELECT 1 FROM agent_runtime_worker_heartbeats h
     WHERE h.process_role='sandbox' AND h.ready AND NOT h.draining
      AND h.observed_at>clock_timestamp()-interval '30 seconds')) THEN
  RAISE EXCEPTION 'CODE_EXECUTE_PREREQUISITES_REQUIRED' USING ERRCODE='55000';
 END IF;
 result:=jsonb_build_object('outcome','applied','state_version',c.state_version);
 INSERT INTO agent_runtime_admin_audit(
  request_id,actor_user_id,org_id,operation,reason,request_payload,result_payload)
 VALUES(p_request_id,actor,org,'set_control',btrim(p_reason),
  jsonb_build_object('expected_state_version',p_expected_state_version,'patch',p_patch),
  result);
 RETURN result;
END $$;

CREATE FUNCTION set_agent_runtime_org_rollout(
 p_request_id UUID,p_org_id UUID,p_enabled BOOLEAN,p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE prior agent_runtime_admin_audit%ROWTYPE; result JSONB;
 actor UUID:=tenant_actor_user_id(); org UUID:=tenant_org_id();
BEGIN
 IF session_user<>'everydayai_runtime_admin'
   OR current_setting('app.access_kind',true)<>'runtime_admin'
   OR NOT tenant_platform_admin() OR org<>p_org_id OR actor IS NULL THEN
  RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO prior FROM agent_runtime_admin_audit WHERE request_id=p_request_id;
 IF FOUND THEN
  IF prior.actor_user_id<>actor OR prior.org_id<>p_org_id
    OR prior.operation<>'set_rollout' OR prior.reason<>btrim(p_reason)
    OR prior.request_payload<>jsonb_build_object('enabled',p_enabled) THEN
   RETURN jsonb_build_object('outcome','idempotency_conflict');
  END IF;
  RETURN prior.result_payload||jsonb_build_object('outcome','already_applied');
 END IF;
 INSERT INTO agent_runtime_org_rollout(org_id,enabled,updated_by,update_reason)
 VALUES(p_org_id,p_enabled,actor,btrim(p_reason))
 ON CONFLICT(org_id) DO UPDATE SET enabled=excluded.enabled,
  updated_by=excluded.updated_by,update_reason=excluded.update_reason,
  updated_at=clock_timestamp();
 result:=jsonb_build_object('outcome','applied','org_id',p_org_id,
  'enabled',p_enabled);
 INSERT INTO agent_runtime_admin_audit(
  request_id,actor_user_id,org_id,operation,reason,request_payload,result_payload)
 VALUES(p_request_id,actor,p_org_id,'set_rollout',btrim(p_reason),
  jsonb_build_object('enabled',p_enabled),result);
 RETURN result;
END $$;

CREATE FUNCTION get_agent_runtime_worker_control(p_process_role TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c agent_runtime_control%ROWTYPE; expected TEXT; access TEXT;
BEGIN
 expected:=CASE p_process_role
  WHEN 'agent_runtime' THEN 'everydayai_agent_runtime_worker'
  WHEN 'projection' THEN 'everydayai_projection_worker'
  WHEN 'authorization' THEN 'everydayai_authorization_worker'
  WHEN 'sandbox' THEN 'everydayai_sandbox_worker' END;
 access:=CASE p_process_role WHEN 'sandbox' THEN 'sandbox_worker'
  ELSE p_process_role END;
 IF expected IS NULL OR session_user<>expected OR
  current_setting('app.access_kind',true)<>access THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_CONTROL_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO c FROM agent_runtime_control WHERE singleton;
 RETURN jsonb_build_object('release_revision',c.release_revision,
  'enabled',CASE p_process_role
   WHEN 'agent_runtime' THEN c.command_claim_enabled AND c.action_dispatch_enabled
   WHEN 'projection' THEN c.projection_enabled
   WHEN 'authorization' THEN c.authorization_recovery_enabled
   WHEN 'sandbox' THEN c.code_execute_enabled END,
  'code_execute_enabled',c.code_execute_enabled);
END $$;

REVOKE ALL ON TABLE agent_runtime_control,agent_runtime_org_rollout,
 agent_runtime_worker_heartbeats,agent_runtime_capabilities,
 agent_tool_confirmation_results,
 _agent_runtime_223_grant_snapshot FROM PUBLIC;
REVOKE ALL ON TABLE agent_runtime_admin_audit FROM PUBLIC;
REVOKE ALL ON FUNCTION runtime_submit_ingress(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,JSONB),
 resolve_agent_tool_confirmation_v3(
 TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN),
 claim_agent_tool_confirmation_notification(TEXT,INTEGER),
 complete_agent_tool_confirmation_notification(UUID,UUID,BOOLEAN),
 enqueue_wecom_runtime_turn_v3(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT),
 complete_model_attempt_with_raw_actions(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB),
 get_agent_runtime_model_context(UUID,TEXT,UUID),
 get_agent_runtime_ai_bundle(UUID,TEXT,UUID,TEXT),
 report_agent_runtime_worker_heartbeat(TEXT,TEXT,TEXT,BOOLEAN,BOOLEAN,TEXT,JSONB),
 report_agent_runtime_capability(TEXT,BOOLEAN,JSONB),
 get_agent_runtime_worker_control(TEXT),
 get_agent_runtime_admin_status(),
 admin_requeue_agent_projection_dead(
 UUID,TEXT,BIGINT,INTEGER,UUID,TEXT,TIMESTAMPTZ),
 set_agent_runtime_control(UUID,BIGINT,JSONB,TEXT),
 set_agent_runtime_org_rollout(UUID,UUID,BOOLEAN,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION gate_agent_action_dispatch(
 UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),
 _gate_agent_action_dispatch_220_24(
 UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),
 _agent_compat_project_command(agent_runtime_events),
 _agent_compat_project_completed_run(
 agent_runs,agent_runtime_sessions,agent_session_commands,tasks),
 _agent_compat_project_run(agent_runtime_events,TEXT)
 ,_agent_compat_project_command_220_12(agent_runtime_events)
 ,_agent_compat_project_completed_run_220_12(
 agent_runs,agent_runtime_sessions,agent_session_commands,tasks)
 ,_agent_compat_project_run_220_12(agent_runtime_events,TEXT)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,
 everydayai_worker,everydayai_sync,everydayai;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,JSONB)
 TO everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION resolve_agent_tool_confirmation_v3(
 TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN)
 TO everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION
 claim_agent_tool_confirmation_notification(TEXT,INTEGER),
 complete_agent_tool_confirmation_notification(UUID,UUID,BOOLEAN)
 TO everydayai_projection_worker;
GRANT EXECUTE ON FUNCTION enqueue_wecom_runtime_turn_v3(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT)
 TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION report_agent_runtime_worker_heartbeat(
 TEXT,TEXT,TEXT,BOOLEAN,BOOLEAN,TEXT,JSONB)
 TO everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION report_agent_runtime_capability(TEXT,BOOLEAN,JSONB)
 TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION get_agent_runtime_worker_control(TEXT)
 TO everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION get_agent_runtime_ai_bundle(UUID,TEXT,UUID,TEXT)
 TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION get_agent_runtime_admin_status()
 TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION admin_requeue_agent_projection_dead(
 UUID,TEXT,BIGINT,INTEGER,UUID,TEXT,TIMESTAMPTZ)
 TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION set_agent_runtime_control(UUID,BIGINT,JSONB,TEXT),
 set_agent_runtime_org_rollout(UUID,UUID,BOOLEAN,TEXT)
 TO everydayai_runtime_admin;

DO $$
DECLARE x RECORD;
 runtime_names TEXT[]:=ARRAY[
  'claim_pending_agent_command_and_ensure_run','get_agent_command_run_claim',
  'renew_agent_command_claim','finish_agent_command_claim','claim_next_agent_run',
  'get_claimed_agent_run','get_agent_run_aggregate','renew_agent_run',
  'claim_agent_run','get_agent_runtime_run_claim','create_agent_run',
  'complete_agent_run','fail_agent_run','set_agent_run_waiting',
  'wake_agent_run','cancel_agent_run',
  'create_model_step','prepare_model_attempt','start_model_attempt_dispatch',
  'mark_model_attempt_response_started','record_model_attempt_unknown',
  'complete_model_attempt_with_result','complete_model_attempt_with_raw_actions',
  'complete_model_attempt_step_and_create_actions',
  'complete_model_attempt_without_actions','complete_model_step',
  'fail_model_attempt_and_step','fail_model_step','get_model_attempt',
  'renew_model_attempt_execution','claim_model_attempt_reconciliation',
  'renew_model_attempt_reconciliation','resolve_model_attempt',
  'record_late_model_receipt',
  'get_agent_runtime_model_context','get_agent_runtime_ai_bundle',
  'claim_ready_agent_action_snapshots',
  'get_agent_action_dispatch_batch','gate_agent_action_dispatch',
  'claim_next_agent_action_reconciliation','resolve_agent_action_reconciliation',
  'claim_ready_agent_actions','get_agent_action_claim_batch',
  'get_agent_action','renew_agent_action_attempt',
  'mark_agent_action_dispatching','recover_expired_agent_action_attempt',
  'mark_agent_action_accepted','record_agent_action_unknown',
  'fail_claimed_agent_action','complete_agent_action','fail_agent_action',
  'claim_agent_action_reconciliation','get_claimed_agent_action_reconciliation',
  'renew_agent_action_reconciliation',
  'create_or_get_sandbox_job','get_sandbox_job','request_sandbox_job_cancel',
  'readback_sandbox_job_by_binding','get_sandbox_job_by_binding'];
 projection_names TEXT[]:=ARRAY['claim_agent_compat_projection_outbox',
  'claim_agent_projection_outbox','get_claimed_agent_projection_event',
  'apply_agent_compat_projection','get_agent_compat_projection_result',
  'fail_agent_projection_outbox'];
 auth_names TEXT[]:=ARRAY['claim_next_agent_authorization_recovery',
  'renew_agent_authorization_recovery','record_agent_policy_receipt',
  'activate_agent_authorized_action','expire_agent_authorization_interaction'];
BEGIN
 FOR x IN SELECT p.oid::regprocedure signature,p.proname FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' LOOP
  IF x.proname=ANY(runtime_names) THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO everydayai_agent_runtime_worker',x.signature);
   EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM everydayai_worker,everydayai_runtime',x.signature);
  ELSIF x.proname=ANY(projection_names) THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO everydayai_projection_worker',x.signature);
   EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM everydayai_worker',x.signature);
  ELSIF x.proname=ANY(auth_names) THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO everydayai_authorization_worker',x.signature);
   EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM everydayai_worker',x.signature);
  END IF;
 END LOOP;
END $$;

GRANT USAGE ON SCHEMA public TO everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_runtime_admin;
REVOKE CREATE ON SCHEMA public FROM everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_runtime_admin;
RESET ROLE;
