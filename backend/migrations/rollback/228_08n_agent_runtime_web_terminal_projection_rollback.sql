SET LOCAL ROLE everydayai_owner;

DROP FUNCTION repair_agent_runtime_web_terminal_projection_v1(UUID,UUID,TEXT);

CREATE OR REPLACE FUNCTION _agent_compat_project_run(
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

RESET ROLE;
