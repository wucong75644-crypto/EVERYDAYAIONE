-- A nullable link preserves legacy Reach operations and retention behavior.
SET LOCAL ROLE everydayai;
GRANT REFERENCES(id) ON public.tool_invocations TO everydayai_owner;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.reach_operations ADD COLUMN native_invocation_id uuid
 REFERENCES public.tool_invocations(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX reach_operations_native_invocation ON public.reach_operations(native_invocation_id)
 WHERE native_invocation_id IS NOT NULL;
RESET ROLE;
SET LOCAL ROLE everydayai;
REVOKE REFERENCES(id) ON public.tool_invocations FROM everydayai_owner;

-- Called inside the original domain reservation transaction, before platform IO.
CREATE FUNCTION public.agent_task_reach_invocation(p_task_id uuid,p_execution_token uuid,p_call_id text)
RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; i public.tool_invocations%ROWTYPE; checked jsonb;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=p_task_id FOR UPDATE;
 IF s.root_task_id IS NULL THEN RAISE EXCEPTION 'TASK_REACH_NATIVE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,s.root_task_id,s.revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' OR s.state->>'image_binding_version' IS DISTINCT FROM '3' THEN
   RAISE EXCEPTION 'TASK_REACH_NATIVE_BINDING_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO i FROM public.tool_invocations WHERE task_id=t.id AND turn_id=t.turn_id
   AND conversation_id=t.conversation_id AND tool_call_id=p_call_id AND tool_name='agent_reach' AND status='running';
 IF i.id IS NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'operations') op
   WHERE op->>'owner_task_id'=t.id::text AND op->>'owner_turn_id'=t.turn_id::text
     AND op->>'invocation_id'=p_call_id AND op->>'tool_name'='agent_reach' AND op->>'capability'='platform.agent_reach'
     AND op->>'arguments_sha256'=i.args_hash AND op->>'status'='reserved') THEN
   RAISE EXCEPTION 'TASK_REACH_NATIVE_BINDING_REQUIRED' USING ERRCODE='42501';
 END IF;
 RETURN i.id;
END $$;
REVOKE ALL ON FUNCTION public.agent_task_reach_invocation(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_reach_invocation(uuid,uuid,text) TO everydayai;

CREATE OR REPLACE FUNCTION public.read_agent_task_reach_receipt(
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
 SELECT jsonb_build_object('native_id',r.id,'linked_native_id',CASE WHEN r.native_invocation_id=i.id THEN i.id ELSE NULL END,'connection_id',r.connection_id,'platform',c.platform,
   'state',r.state,'receipt',r.receipt,'tool_result',i.result,'ledger_status',i.status,
   'task_id',i.task_id,'turn_id',i.turn_id,'tool_call_id',i.tool_call_id,'args_hash',i.args_hash)
 INTO value FROM public.tool_invocations i JOIN public.tasks original ON original.id=i.task_id
 JOIN public.reach_operations r ON r.actor_user_id=t.user_id AND r.org_id=t.org_id
   AND (r.native_invocation_id=i.id OR (r.native_invocation_id IS NULL AND r.call_id=i.tool_call_id
     AND NOT EXISTS(SELECT 1 FROM public.reach_operations linked WHERE linked.native_invocation_id=i.id)))
 JOIN public.reach_connections c ON c.id=r.connection_id AND c.org_id=r.org_id
 WHERE i.task_id=(op->>'owner_task_id')::uuid AND i.turn_id=(op->>'owner_turn_id')::uuid
   AND i.tool_call_id=op->>'invocation_id' AND i.tool_name='agent_reach' AND i.args_hash=op->>'arguments_sha256'
   AND i.conversation_id=t.conversation_id AND original.conversation_id=t.conversation_id
   AND original.type='chat' AND original.user_id=t.user_id AND original.org_id IS NOT DISTINCT FROM t.org_id
   AND (original.id=t.id OR original.id=s.root_task_id OR original.delivery_context->>'logical_task_id'=s.root_task_id::text);
 IF value IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','proof',value);
END $$;

-- Only positive linked receipts can repair the logical operation. Preserve the
-- original running/uncertain native invocation; never fabricate its return.
CREATE FUNCTION public.reconcile_agent_reach_operation(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; checked jsonb; proof jsonb;
 op jsonb; updated jsonb; next_state jsonb; cp jsonb; saved jsonb; linked uuid;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 checked:=public.read_agent_task_reach_receipt(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'id'=p_operation_id;
 SELECT r.native_invocation_id INTO linked FROM public.reach_operations r JOIN public.tool_invocations i ON i.id=r.native_invocation_id
   WHERE r.id=(proof->>'native_id')::uuid AND i.task_id=(op->>'owner_task_id')::uuid
     AND i.turn_id=(op->>'owner_turn_id')::uuid AND i.tool_call_id=op->>'invocation_id'
     AND i.args_hash=op->>'arguments_sha256' AND i.tool_name='agent_reach' AND i.conversation_id=t.conversation_id;
 IF (linked IS NOT NULL AND proof->>'state'='succeeded' AND op->>'status' IN ('reserved','uncertain','completed')
   AND proof#>>'{receipt,action}' IN ('create_post','create_comment','set_like','upload_video')
   AND COALESCE(proof#>>'{receipt,stage}','')<>'backend_submitted'
   AND COALESCE(proof#>>'{tool_result,tool_result,status}','')<>'partial'
   AND NOT (proof->>'platform'='bilibili' AND proof#>>'{receipt,action}'='upload_video')
   AND (proof#>>'{receipt,action}'<>'set_like' OR COALESCE(proof#>>'{receipt,target}','')<>'')
   AND (proof#>>'{receipt,action}'='set_like' OR COALESCE(proof#>>'{receipt,remote_id}','')<>''
       OR (proof->>'platform'='xiaohongshu' AND proof#>>'{receipt,action}'='create_comment'
         AND proof#>'{receipt,confirmed}'='true'::jsonb))) IS NOT TRUE THEN
   RETURN jsonb_build_object('outcome','unresolved');
 END IF;
 IF op->>'status'='completed' AND op->>'receipt_ref' IS DISTINCT FROM 'reach-operation:'||(proof->>'native_id') THEN
   RAISE EXCEPTION 'TASK_REACH_RECEIPT_CONFLICT' USING ERRCODE='42501';
 END IF;
 updated:=op||jsonb_build_object('status','completed','receipt_ref','reach-operation:'||(proof->>'native_id'));
 next_state:=s.state;
 IF updated IS DISTINCT FROM op THEN
   next_state:=jsonb_set(jsonb_set(s.state,'{operations}',(SELECT jsonb_agg(CASE WHEN v->>'id'=p_operation_id THEN updated ELSE v END)
     FROM jsonb_array_elements(s.state->'operations') v)),'{revision}',to_jsonb(s.revision+1));
   SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=p_task_id FOR UPDATE;
   IF cp->'_task_execution' IS DISTINCT FROM s.state THEN RAISE EXCEPTION 'TASK_EXECUTION_CHECKPOINT_MISMATCH' USING ERRCODE='42501'; END IF;
   saved:=public.save_agent_task_checkpoint(p_task_id,p_execution_token,'after_tool',jsonb_set(cp,'{_task_execution}',next_state),s.revision);
   IF saved->>'outcome' IS DISTINCT FROM 'saved' THEN RETURN saved; END IF;
 END IF;
 proof:=proof||jsonb_build_object('linked_native_id',linked,'reconciled',true);
 RETURN jsonb_build_object('outcome','reconciled','state',next_state,'operation',updated,'receipt',proof);
END $$;
REVOKE ALL ON FUNCTION public.reconcile_agent_reach_operation(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reconcile_agent_reach_operation(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
