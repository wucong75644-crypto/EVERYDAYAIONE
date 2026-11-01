-- Exact stable product scalar postconditions; old title projection stays valid.
-- Lost ACK exposes a later desired-state observation, never settles an unknown write effect.
-- Read existing native facts; no extra query, mutation, user turn or credential.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.read_agent_task_erp_postcondition(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_operation_id text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; r public.agent_task_native_writes%ROWTYPE; current_task public.tasks%ROWTYPE; witness jsonb; projection_key text;
BEGIN
 checked:=public.read_agent_task_native_write(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 IF proof->>'domain' IS DISTINCT FROM 'erp' OR proof#>>'{dispatch,method}' IS DISTINCT FROM 'item.general.addorupdate'
   OR COALESCE(proof#>>'{dispatch,postcondition,kind}','') NOT IN ('product.title','product.fields') THEN
   RAISE EXCEPTION 'TASK_ERP_POSTCONDITION_REQUIRED' USING ERRCODE='42501'; END IF;
 projection_key:=CASE WHEN proof#>>'{dispatch,postcondition,kind}'='product.fields' THEN '_erp_readback_fields_v1' ELSE '_erp_readback_v1' END;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
 SELECT * INTO current_task FROM public.tasks WHERE id=p_task_id;
 IF r.phase IN ('returned','dispatched') THEN
   SELECT jsonb_build_object('id',i.id,'task_id',i.task_id,'tool_call_id',i.tool_call_id,'args_hash',i.args_hash,
     'tool_name',i.tool_name,'status',i.status,'result',i.result,
     'started_at',i.started_at,'completed_at',i.completed_at,'write_returned_at',CASE WHEN r.phase='returned' THEN r.updated_at ELSE NULL END,
     'write_phase',r.phase,'write_invocation_completed_at',original_write.completed_at) INTO witness
   FROM public.tool_invocations i JOIN public.tasks source ON source.id=i.task_id
     JOIN public.tool_invocations original_write ON original_write.id=r.native_invocation_id
   WHERE i.conversation_id=current_task.conversation_id AND source.conversation_id=current_task.conversation_id
     AND source.user_id=current_task.user_id AND source.org_id IS NOT DISTINCT FROM current_task.org_id AND source.type='chat'
     AND (source.id=p_task_id OR source.id=p_logical_task_id OR source.delivery_context->>'logical_task_id'=p_logical_task_id::text)
     AND (r.phase='returned' OR (original_write.status IN ('succeeded','failed','uncertain')
       AND original_write.completed_at IS NOT NULL AND i.started_at>original_write.completed_at))
     AND i.tool_name='erp_product_query' AND i.status='succeeded' AND i.started_at>r.updated_at AND i.completed_at>=i.started_at
     AND i.result#>'{tool_result,execution}' @> '{"status":"succeeded","handler_started":true,"cancelled":false,"cached":false,"replayed":false}'::jsonb
     AND i.result#>>ARRAY['tool_result','raw','metadata',projection_key,'method']='item.single.get'
     AND i.result#>ARRAY['tool_result','raw','metadata',projection_key,'parameters']=
       jsonb_build_object('outerId',proof#>>'{dispatch,postcondition,outer_id}')
   ORDER BY i.started_at DESC,i.id DESC LIMIT 1;
 END IF;
 RETURN jsonb_build_object('outcome','found','proof',proof||jsonb_build_object('readback',witness,'source_rotations',
   CASE WHEN witness IS NULL THEN NULL ELSE public.agent_erp_session_rotation_path(current_task.org_id,
     proof#>>'{dispatch,postcondition,source_fingerprint}',witness#>>ARRAY['result','tool_result','raw','metadata',projection_key,'source_fingerprint'],
     (witness->>'completed_at')::timestamptz) END));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_erp_postcondition(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_erp_postcondition(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
