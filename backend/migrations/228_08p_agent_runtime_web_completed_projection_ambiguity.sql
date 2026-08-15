-- 228.08p: completed Web Runtime projections must write the prepared
-- assistant message without a PL/pgSQL variable/column name collision.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _agent_compat_project_completed_run(
 p_run agent_runs,p_session agent_runtime_sessions,
 p_command agent_session_commands,p_task tasks
) RETURNS UUID LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE r agent_model_results%ROWTYPE; step agent_model_steps%ROWTYPE;
 m messages%ROWTYPE; v_content TEXT; anchor UUID;
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
 v_content:=CASE WHEN r.output_kind='text'
  THEN jsonb_build_array(jsonb_build_object(
   'type','text','text',r.text_content))::text
  ELSE jsonb_build_array(jsonb_build_object(
   'type','data','data',r.structured_content))::text END;
 SELECT * INTO m FROM messages WHERE id=anchor FOR UPDATE;
 IF m.id IS NULL OR m.conversation_id<>p_session.conversation_id
   OR m.org_id IS DISTINCT FROM p_run.org_id OR m.role::text<>'assistant'
   OR m.turn_id IS DISTINCT FROM
      nullif(p_command.payload->>'turn_id','')::uuid THEN
  RAISE EXCEPTION 'AGENT_COMPAT_PREPARED_OUTPUT_CONFLICT' USING ERRCODE='55000';
 END IF;
 UPDATE messages AS target
  SET content=v_content,status='completed',credits_cost=0
  WHERE target.id=m.id RETURNING target.* INTO m;
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

RESET ROLE;
