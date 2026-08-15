-- 228.08n: terminal Web Runtime projections must close the prepared assistant
-- placeholder. Also expose one narrow, audited admin repair for projections
-- that were delivered before this contract existed.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _agent_compat_project_run(
 p_event agent_runtime_events,p_action TEXT,
 OUT projected_message_id UUID,OUT projected_task_id UUID,
 OUT projected_delivery_id UUID
) LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE;
 c agent_session_commands%ROWTYPE; t tasks%ROWTYPE; m messages%ROWTYPE;
 v_status TEXT; anchor UUID; output_anchor UUID; terminal_content TEXT;
BEGIN
 SELECT * INTO r FROM agent_runs WHERE id=p_event.run_id FOR UPDATE;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
 SELECT * INTO c FROM agent_session_commands WHERE id=r.command_id;
 anchor:=nullif(c.payload->>'task_id','')::uuid;
 output_anchor:=nullif(c.payload->>'output_message_id','')::uuid;
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
  IF v_status IN ('failed','cancelled') THEN
   SELECT * INTO m FROM messages WHERE id=output_anchor FOR UPDATE;
   IF m.id IS NULL OR t.assistant_message_id IS DISTINCT FROM m.id
      OR m.conversation_id IS DISTINCT FROM t.conversation_id
      OR m.org_id IS DISTINCT FROM t.org_id OR m.role::TEXT<>'assistant'
      OR m.turn_id IS DISTINCT FROM NULLIF(c.payload->>'turn_id','')::UUID
      OR m.reply_to_message_id IS DISTINCT FROM
         NULLIF(c.payload->>'input_message_id','')::UUID THEN
    RAISE EXCEPTION 'AGENT_COMPAT_PREPARED_OUTPUT_CONFLICT' USING ERRCODE='55000';
   END IF;
   terminal_content:=jsonb_build_array(jsonb_build_object(
    'type','text','text',CASE WHEN v_status='cancelled' THEN '任务已取消'
      ELSE '生成失败，请点击「重新生成」重试' END))::TEXT;
   UPDATE messages SET status='failed',content=terminal_content,
      is_error=(v_status='failed') WHERE id=m.id;
   projected_message_id:=m.id;
  END IF;
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

CREATE FUNCTION repair_agent_runtime_web_terminal_projection_v1(
 p_task_id UUID,p_repair_request_id UUID,p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE t tasks%ROWTYPE; m messages%ROWTYPE; c agent_session_commands%ROWTYPE;
 r agent_runs%ROWTYPE; s agent_runtime_sessions%ROWTYPE; terminal_content TEXT;
BEGIN
 IF session_user<>'everydayai_runtime_admin'
    OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime_admin'
    OR NOT tenant_platform_admin() OR tenant_actor_user_id() IS NULL
    OR p_task_id IS NULL OR p_repair_request_id IS NULL
    OR NULLIF(BTRIM(p_reason),'') IS NULL OR length(p_reason)>500
    OR NULLIF(BTRIM(current_setting('app.request_id',TRUE)),'') IS NULL THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_WEB_TERMINAL_REPAIR_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO t FROM tasks WHERE id=p_task_id FOR UPDATE;
 IF t.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO c FROM agent_session_commands
  WHERE id=NULLIF(t.delivery_context->>'runtime_command_id','')::UUID;
 SELECT * INTO r FROM agent_runs WHERE command_id=c.id;
 SELECT * INTO s FROM agent_runtime_sessions WHERE id=r.session_id;
 SELECT * INTO m FROM messages WHERE id=t.assistant_message_id FOR UPDATE;
 IF c.id IS NULL OR r.id IS NULL OR s.id IS NULL OR m.id IS NULL
    OR c.payload->>'task_id' IS DISTINCT FROM t.id::TEXT
    OR c.payload->>'output_message_id' IS DISTINCT FROM m.id::TEXT
    OR r.status NOT IN ('failed','cancelled')
    OR t.status IS DISTINCT FROM r.status
    OR NOT t.delivery_context @> '{"runtime":true}'::JSONB
    OR t.user_id IS DISTINCT FROM r.user_id
    OR t.org_id IS DISTINCT FROM r.org_id
    OR t.conversation_id IS DISTINCT FROM s.conversation_id
    OR m.conversation_id IS DISTINCT FROM t.conversation_id
    OR m.org_id IS DISTINCT FROM t.org_id OR m.role::TEXT<>'assistant'
    OR m.status::TEXT NOT IN ('pending','generating','streaming') THEN
  IF m.status::TEXT='failed' THEN
   RETURN jsonb_build_object('outcome','already_repaired','task_id',t.id);
  END IF;
  RAISE EXCEPTION 'AGENT_RUNTIME_WEB_TERMINAL_REPAIR_BINDING_INVALID' USING ERRCODE='55000';
 END IF;
 terminal_content:=jsonb_build_array(jsonb_build_object(
  'type','text','text',CASE WHEN r.status='cancelled' THEN '任务已取消'
    ELSE '生成失败，请点击「重新生成」重试' END))::TEXT;
 UPDATE messages SET status='failed',content=terminal_content,
    is_error=(r.status='failed') WHERE id=m.id;
 UPDATE tasks SET delivery_context=delivery_context||jsonb_build_object(
    'runtime_terminal_projection_repaired',TRUE,
    'runtime_terminal_projection_repair_request_id',p_repair_request_id,
    'runtime_terminal_projection_repair_reason',BTRIM(p_reason),
    'runtime_terminal_projection_repaired_at',clock_timestamp())
  WHERE id=t.id;
 RETURN jsonb_build_object('outcome','repaired','task_id',t.id,
  'message_id',m.id,'run_id',r.id,'terminal_status',r.status);
END $$;

REVOKE ALL ON FUNCTION repair_agent_runtime_web_terminal_projection_v1(UUID,UUID,TEXT)
 FROM PUBLIC,everydayai,everydayai_runtime,everydayai_wecom_runtime,
 everydayai_worker,everydayai_sync,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION repair_agent_runtime_web_terminal_projection_v1(UUID,UUID,TEXT)
 TO everydayai_runtime_admin;

RESET ROLE;
