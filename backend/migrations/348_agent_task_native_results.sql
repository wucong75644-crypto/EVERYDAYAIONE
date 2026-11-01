-- A read of positive native facts, never a dispatch/retry grant.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_task_native_results(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,
 p_tool_call_id text DEFAULT NULL,p_artifact_id text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; source public.tasks%ROWTYPE;
 item public.tool_invocations%ROWTYPE; a jsonb; args jsonb;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
   OR t.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR t.type IS DISTINCT FROM 'chat' OR (t.delivery_context @> '{"actor":true}') IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'TASK_NATIVE_RESULT_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 IF t.execution_token IS DISTINCT FROM p_execution_token OR t.status<>'running' THEN RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
 IF t.lease_expires_at IS NULL OR t.lease_expires_at<=now() THEN RETURN jsonb_build_object('outcome','lease_expired'); END IF;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id AND owner_task_id=p_task_id
   AND actor_user_id=t.user_id AND conversation_id=t.conversation_id AND org_id IS NOT DISTINCT FROM t.org_id;
 IF NOT FOUND OR s.revision<>p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
 IF s.state->>'status'<>'active' THEN RETURN jsonb_build_object('outcome','not_active'); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
   OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
       WHERE m.user_id=t.user_id AND m.org_id=t.org_id AND m.status='active' AND o.status='active')) THEN
   RAISE EXCEPTION 'TASK_NATIVE_RESULT_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 IF p_tool_call_id IS NULL AND p_artifact_id IS NULL THEN
   RETURN jsonb_build_object('outcome','found','results',COALESCE((SELECT jsonb_agg(value) FROM (
     SELECT jsonb_build_object('tool_call_id',l.tool_call_id,'tool_name',l.tool_name,'status',l.status,
       'args_hash',l.args_hash,'completed_at',l.completed_at) value FROM public.tool_invocations l
       WHERE l.task_id=t.id AND l.turn_id=t.turn_id AND l.conversation_id=t.conversation_id
       ORDER BY l.started_at DESC,l.id LIMIT 100) rows),'[]'::jsonb));
 END IF;
 IF p_tool_call_id IS NOT NULL AND p_artifact_id IS NULL THEN
   SELECT * INTO item FROM public.tool_invocations WHERE task_id=t.id AND turn_id=t.turn_id
     AND conversation_id=t.conversation_id AND tool_call_id=p_tool_call_id;
 ELSIF p_artifact_id IS NOT NULL AND p_tool_call_id IS NULL THEN
   SELECT value INTO a FROM jsonb_array_elements(s.state->'artifacts') WHERE value->>'id'=p_artifact_id
     AND value->'validations' @> '["native.read.result"]'::jsonb;
   IF a IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
   SELECT l.* INTO item FROM public.tool_invocations l WHERE a->>'reference'='task-native-result:'||l.id::text
     AND a->>'source_task_id'=l.task_id::text AND a->>'source_call_id'=l.tool_call_id AND l.conversation_id=t.conversation_id;
 ELSE RAISE EXCEPTION 'TASK_NATIVE_RESULT_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 IF item.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO source FROM public.tasks WHERE id=item.task_id;
 IF source.user_id IS DISTINCT FROM t.user_id OR source.org_id IS DISTINCT FROM t.org_id
    OR source.conversation_id IS DISTINCT FROM t.conversation_id OR source.type IS DISTINCT FROM 'chat'
    OR source.turn_id IS DISTINCT FROM item.turn_id
    OR (source.id<>t.id AND source.id<>s.root_task_id
        AND source.delivery_context->>'logical_task_id' IS DISTINCT FROM s.root_task_id::text) THEN
   RAISE EXCEPTION 'TASK_NATIVE_RESULT_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 SELECT state#>ARRAY['_task_read_arguments','calls',item.tool_call_id,'arguments'] INTO args
   FROM public.conversation_turn_checkpoints WHERE task_id=item.task_id
     AND state#>>'{_task_read_arguments,task_id}'=item.task_id::text
     AND state#>>'{_task_read_arguments,turn_id}'=item.turn_id::text
     AND state#>>ARRAY['_task_read_arguments','calls',item.tool_call_id,'args_hash']=item.args_hash;
 RETURN jsonb_build_object('outcome','found','record',jsonb_build_object('id',item.id,'task_id',item.task_id,
   'turn_id',item.turn_id,'tool_call_id',item.tool_call_id,'tool_name',item.tool_name,'status',item.status,
   'args_hash',item.args_hash,'arguments',args,'result',item.result));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_native_results(uuid,uuid,uuid,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_native_results(uuid,uuid,uuid,bigint,text,text) TO everydayai;
RESET ROLE;
