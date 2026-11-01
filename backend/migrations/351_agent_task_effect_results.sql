SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_task_effect_result(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; s public.agent_task_states%ROWTYPE; t public.tasks%ROWTYPE; op jsonb; value jsonb;
BEGIN
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id AND owner_task_id=p_task_id;
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'id'=p_operation_id;
 IF op IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT jsonb_build_object('id',i.id,'task_id',i.task_id,'turn_id',i.turn_id,'tool_call_id',i.tool_call_id,
   'tool_name',i.tool_name,'args_hash',i.args_hash,'status',i.status,'result',i.result) INTO value
 FROM public.tool_invocations i JOIN public.tasks source ON source.id=i.task_id
 WHERE i.task_id=(op->>'owner_task_id')::uuid AND i.turn_id=(op->>'owner_turn_id')::uuid
   AND i.tool_call_id=op->>'invocation_id' AND i.tool_name=op->>'tool_name' AND i.args_hash=op->>'arguments_sha256'
   AND i.conversation_id=t.conversation_id AND source.user_id=t.user_id AND source.org_id IS NOT DISTINCT FROM t.org_id
   AND source.conversation_id=t.conversation_id AND source.type='chat'
   AND (source.id=t.id OR source.id=s.root_task_id OR source.delivery_context->>'logical_task_id'=s.root_task_id::text);
 IF value IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','record',value);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_effect_result(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_effect_result(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
