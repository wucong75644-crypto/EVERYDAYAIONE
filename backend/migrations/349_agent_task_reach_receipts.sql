-- Read positive domain facts without granting a retry or returning credentials.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_task_reach_receipt(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; s public.agent_task_states%ROWTYPE; t public.tasks%ROWTYPE; op jsonb; value jsonb;
BEGIN
 -- Reuse the existing owner/token/lease/revision/current-member read fence.
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision);
 IF checked->>'outcome'<>'found' THEN RETURN checked; END IF;
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id AND owner_task_id=p_task_id;
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'id'=p_operation_id;
 IF op IS NULL OR op->>'tool_name'<>'agent_reach' OR op->>'capability'<>'platform.agent_reach' THEN
   RETURN jsonb_build_object('outcome','not_found');
 END IF;
 SELECT jsonb_build_object('native_id',r.id,'connection_id',r.connection_id,'platform',c.platform,
   'state',r.state,'receipt',r.receipt,'tool_result',i.result,'ledger_status',i.status,
   'task_id',i.task_id,'turn_id',i.turn_id,'tool_call_id',i.tool_call_id,'args_hash',i.args_hash)
 INTO value FROM public.tool_invocations i JOIN public.tasks original ON original.id=i.task_id
 JOIN public.reach_operations r ON r.call_id=i.tool_call_id AND r.actor_user_id=t.user_id AND r.org_id=t.org_id
 JOIN public.reach_connections c ON c.id=r.connection_id AND c.org_id=r.org_id
 WHERE i.task_id=(op->>'owner_task_id')::uuid AND i.turn_id=(op->>'owner_turn_id')::uuid
   AND i.tool_call_id=op->>'invocation_id' AND i.tool_name='agent_reach' AND i.args_hash=op->>'arguments_sha256'
   AND i.conversation_id=t.conversation_id AND original.conversation_id=t.conversation_id
   AND original.type='chat' AND original.user_id=t.user_id AND original.org_id IS NOT DISTINCT FROM t.org_id
   AND (original.id=t.id OR original.id=s.root_task_id OR original.delivery_context->>'logical_task_id'=s.root_task_id::text);
 IF value IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','proof',value);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_reach_receipt(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_reach_receipt(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
