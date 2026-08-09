-- 227_33: Token-bound, side-effect-free schedule context for the Runtime finalizer.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION read_agent_runtime_scheduled_finalization_context_v1(
 p_scheduled_run_id UUID,p_claim_token UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE i agent_runtime_scheduled_finalization_intents%ROWTYPE;
 b agent_runtime_scheduled_run_bindings%ROWTYPE;r agent_runs%ROWTYPE;
 q scheduled_task_runs%ROWTYPE;t scheduled_tasks%ROWTYPE;
 e agent_runtime_scheduled_execution_profiles%ROWTYPE;
 g agent_runtime_tenant_gate_controls%ROWTYPE;
 tenant_epoch BIGINT:=0;provider_epoch BIGINT:=0;capability_epoch BIGINT:=0;
 schedule_hash TEXT;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_agent_runtime_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'agent_runtime' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_ACTOR_REQUIRED' USING ERRCODE='42501';
 END IF;
 IF p_scheduled_run_id IS NULL OR p_claim_token IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_CONTEXT_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO i FROM agent_runtime_scheduled_finalization_intents
  WHERE scheduled_run_id=p_scheduled_run_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 IF i.status='applied' THEN RETURN jsonb_build_object('outcome','applied'); END IF;
 IF i.status<>'claimed' OR i.claim_token IS DISTINCT FROM p_claim_token
 OR i.claim_lease_expires_at<=clock_timestamp() THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;

 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE scheduled_run_id=i.scheduled_run_id;
 SELECT * INTO r FROM agent_runs WHERE id=i.runtime_run_id;
 SELECT * INTO q FROM scheduled_task_runs WHERE id=i.scheduled_run_id;
 SELECT * INTO t FROM scheduled_tasks WHERE id=i.scheduled_task_id;
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=i.scheduled_task_id;
 IF b.scheduled_run_id IS NULL OR r.id IS NULL OR q.id IS NULL OR t.id IS NULL
 OR e.scheduled_task_id IS NULL OR b.owner_kind<>'runtime' OR b.owner_status<>'reconcile_required'
 OR(b.runtime_run_id,b.scheduled_task_id,b.org_id,b.user_id)
   IS DISTINCT FROM(i.runtime_run_id,i.scheduled_task_id,i.org_id,i.user_id)
 OR(r.id,r.org_id,r.user_id,r.status,r.state_version,r.run_kind)
   IS DISTINCT FROM(i.runtime_run_id,i.org_id,i.user_id,i.terminal_status,
    i.runtime_run_state_version,'scheduled')
 OR(q.id,q.task_id,q.org_id,q.status)
   IS DISTINCT FROM(i.scheduled_run_id,i.scheduled_task_id,i.org_id,'running')
 OR(t.id,t.org_id,t.user_id,t.status,t.runtime_state_version)
   IS DISTINCT FROM(i.scheduled_task_id,i.org_id,i.user_id,'running',b.task_revision)
 OR(e.org_id,e.user_id,e.state_version,e.provider_revision,e.capability_revision)
   IS DISTINCT FROM(b.org_id,b.user_id,b.profile_state_version,b.provider_revision,b.capability_revision) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;

 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=b.org_id AND gate_scope='tenant' AND scope_key='tenant';
 tenant_epoch:=COALESCE(g.kill_epoch,0);
 IF FOUND AND(g.claim_blocked OR g.dispatch_blocked) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=b.org_id AND gate_scope='provider' AND scope_key=e.provider_key;
 provider_epoch:=COALESCE(g.kill_epoch,0);
 IF FOUND AND g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 SELECT * INTO g FROM agent_runtime_tenant_gate_controls
  WHERE org_id=b.org_id AND gate_scope='capability' AND scope_key=e.capability_key;
 capability_epoch:=COALESCE(g.kill_epoch,0);
 IF FOUND AND g.dispatch_blocked THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF(tenant_epoch,provider_epoch,capability_epoch) IS DISTINCT FROM
   (b.tenant_kill_epoch,b.provider_kill_epoch,b.capability_kill_epoch) THEN
  RETURN jsonb_build_object('outcome','fenced');
 END IF;
 schedule_hash:=_runtime_scheduler_schedule_hash(t);
 IF schedule_hash!~'^[0-9a-f]{64}$' THEN RETURN jsonb_build_object('outcome','fenced'); END IF;

 RETURN jsonb_build_object('outcome','found','context',jsonb_build_object(
  'scheduled_run_id',i.scheduled_run_id,'runtime_run_id',i.runtime_run_id,
  'scheduled_task_id',i.scheduled_task_id,'terminal_status',i.terminal_status,
  'terminal_baseline',i.created_at,'intent_state_version',i.state_version,
  'task_state_version',t.runtime_state_version,'schedule_hash',schedule_hash,
  'schedule_type',t.schedule_type,'cron_expr',t.cron_expr,'timezone',t.timezone,
  'run_at',t.run_at,'weekdays',t.weekdays,'day_of_month',t.day_of_month,
  'retry_count',t.retry_count,'consecutive_failures',t.consecutive_failures));
END $$;

REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID)
 TO everydayai_agent_runtime_worker;

RESET ROLE;
