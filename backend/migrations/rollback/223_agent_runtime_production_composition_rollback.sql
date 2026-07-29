\set ON_ERROR_STOP on
SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM agent_tool_confirmation_results)
 OR EXISTS(SELECT 1 FROM agent_runtime_worker_heartbeats)
 OR EXISTS(SELECT 1 FROM agent_runtime_capabilities)
 OR EXISTS(SELECT 1 FROM agent_runtime_admin_audit)
 OR EXISTS(SELECT 1 FROM agent_projection_dead_recoveries)
 OR EXISTS(SELECT 1 FROM agent_sandbox_jobs)
 OR EXISTS(SELECT 1 FROM agent_runtime_sessions)
 OR EXISTS(SELECT 1 FROM agent_session_commands)
 OR EXISTS(SELECT 1 FROM agent_runs)
 OR EXISTS(SELECT 1 FROM agent_model_steps)
 OR EXISTS(SELECT 1 FROM agent_model_attempts)
 OR EXISTS(SELECT 1 FROM agent_actions)
 OR EXISTS(SELECT 1 FROM agent_interactions)
 OR EXISTS(SELECT 1 FROM agent_runtime_events) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_223_ROLLBACK_GUARD_FACTS_EXIST';
 END IF;
END $$;
DROP FUNCTION gate_agent_action_dispatch(
 UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT);
ALTER FUNCTION _gate_agent_action_dispatch_220_24(
 UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT)
 RENAME TO gate_agent_action_dispatch;
DROP FUNCTION _agent_compat_project_command(agent_runtime_events);
DROP FUNCTION _agent_compat_project_completed_run(
 agent_runs,agent_runtime_sessions,agent_session_commands,tasks);
DROP FUNCTION _agent_compat_project_run(agent_runtime_events,TEXT);
ALTER FUNCTION _agent_compat_project_command_220_12(agent_runtime_events)
 RENAME TO _agent_compat_project_command;
ALTER FUNCTION _agent_compat_project_completed_run_220_12(
 agent_runs,agent_runtime_sessions,agent_session_commands,tasks)
 RENAME TO _agent_compat_project_completed_run;
ALTER FUNCTION _agent_compat_project_run_220_12(agent_runtime_events,TEXT)
 RENAME TO _agent_compat_project_run;
GRANT EXECUTE ON FUNCTION
 _agent_compat_project_command(agent_runtime_events),
 _agent_compat_project_completed_run(
  agent_runs,agent_runtime_sessions,agent_session_commands,tasks),
 _agent_compat_project_run(agent_runtime_events,TEXT)
 TO PUBLIC;
DO $$
DECLARE x RECORD;
BEGIN
 FOR x IN SELECT * FROM _agent_runtime_223_grant_snapshot LOOP
  IF x.had_execute THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',
     x.function_signature,x.role_name);
  ELSE
   EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM %I',
     x.function_signature,x.role_name);
  END IF;
 END LOOP;
END $$;
DO $$
DECLARE x RECORD;
BEGIN
 FOR x IN SELECT p.oid::regprocedure signature FROM pg_proc p
  JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND (
   p.proname LIKE '%agent%' OR p.proname LIKE '%model%'
   OR p.proname LIKE '%sandbox%')
 LOOP
  EXECUTE format(
   'REVOKE EXECUTE ON FUNCTION %s FROM %s',x.signature,
   'everydayai_agent_runtime_worker,everydayai_projection_worker,'||
   'everydayai_authorization_worker,everydayai_runtime_admin');
 END LOOP;
END $$;
REVOKE USAGE ON SCHEMA public FROM everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_runtime_admin;
DROP FUNCTION get_agent_runtime_admin_status();
DROP FUNCTION set_agent_runtime_control(UUID,BIGINT,JSONB,TEXT);
DROP FUNCTION set_agent_runtime_org_rollout(UUID,UUID,BOOLEAN,TEXT);
DROP FUNCTION admin_requeue_agent_projection_dead(
 UUID,TEXT,BIGINT,INTEGER,UUID,TEXT,TIMESTAMPTZ);
DROP FUNCTION get_agent_runtime_worker_control(TEXT);
DROP FUNCTION report_agent_runtime_worker_heartbeat(
 TEXT,TEXT,TEXT,BOOLEAN,BOOLEAN,TEXT,JSONB);
DROP FUNCTION report_agent_runtime_capability(TEXT,BOOLEAN,JSONB);
DROP FUNCTION resolve_agent_tool_confirmation_v3(
 TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN);
DROP FUNCTION claim_agent_tool_confirmation_notification(TEXT,INTEGER);
DROP FUNCTION complete_agent_tool_confirmation_notification(UUID,UUID,BOOLEAN);
DROP FUNCTION enqueue_wecom_runtime_turn_v3(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT);
DROP FUNCTION complete_model_attempt_with_raw_actions(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB);
DROP FUNCTION get_agent_runtime_model_context(UUID,TEXT,UUID);
DROP FUNCTION get_agent_runtime_ai_bundle(UUID,TEXT,UUID,TEXT);
DROP FUNCTION runtime_submit_ingress(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,JSONB);
DROP TABLE agent_tool_confirmation_results;
ALTER TABLE agent_interactions
 DROP COLUMN confirmation_notification_worker,
 DROP COLUMN confirmation_notification_token,
 DROP COLUMN confirmation_notification_lease_expires_at,
 DROP COLUMN confirmation_notification_not_before,
 DROP COLUMN confirmation_notified_at;
DROP TABLE agent_runtime_worker_heartbeats;
DROP TABLE agent_runtime_capabilities;
DROP TABLE agent_runtime_org_rollout;
DROP TABLE agent_runtime_control;
DROP TABLE agent_runtime_admin_audit;
DROP FUNCTION _agent_runtime_admin_audit_immutable();
DROP TABLE _agent_runtime_223_grant_snapshot;
CREATE OR REPLACE FUNCTION _assert_agent_runtime_actor(p_worker BOOLEAN)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
 IF (p_worker AND(session_user<>'everydayai_worker' OR
   current_setting('app.access_kind',true)<>'worker')) OR
   (NOT p_worker AND(session_user NOT IN
   ('everydayai_runtime','everydayai_wecom_runtime') OR
   current_setting('app.access_kind',true)<>'runtime')) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_ACTOR_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
END $$;
CREATE OR REPLACE FUNCTION tenant_platform_admin()
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT session_user='everydayai_runtime'
  AND current_setting('app.access_kind',true)='runtime'
  AND tenant_actor_user_id() IS NOT NULL
  AND EXISTS(SELECT 1 FROM users app_user
   WHERE app_user.id=tenant_actor_user_id()
    AND app_user.role::text='super_admin'
    AND app_user.status::text='active')
$$;
REVOKE EXECUTE ON FUNCTION tenant_platform_admin()
 FROM everydayai_runtime_admin;
CREATE OR REPLACE FUNCTION _assert_agent_sandbox_actor(p_kind TEXT)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path=pg_catalog,public AS $$
BEGIN
 IF NULLIF(current_setting('app.request_id',TRUE),'') IS NULL
 OR (p_kind='runtime' AND (
   session_user<>'everydayai_runtime'
   OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime'))
 OR (p_kind='sandbox_worker' AND (
   session_user<>'everydayai_sandbox_worker'
   OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'sandbox_worker'))
 OR p_kind NOT IN('runtime','sandbox_worker') THEN
  RAISE EXCEPTION 'AGENT_SANDBOX_ROLE_SCOPE_MISMATCH'
    USING ERRCODE='42501';
 END IF;
END $$;
RESET ROLE;
