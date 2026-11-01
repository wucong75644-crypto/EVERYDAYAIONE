SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_erp_session_rotations NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_erp_session_rotations) THEN RAISE EXCEPTION 'ERP_SESSION_ROTATIONS_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_erp_session_rotations FORCE ROW LEVEL SECURITY;
END $$;
RESET ROLE;
-- Read existing native facts; no extra query, mutation, user turn or credential.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.read_agent_task_erp_postcondition(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_operation_id text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; r public.agent_task_native_writes%ROWTYPE; current_task public.tasks%ROWTYPE; witness jsonb;
BEGIN
 checked:=public.read_agent_task_native_write(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 IF proof->>'domain' IS DISTINCT FROM 'erp' OR proof#>>'{dispatch,method}' IS DISTINCT FROM 'item.general.addorupdate'
   OR proof#>>'{dispatch,postcondition,kind}' IS DISTINCT FROM 'product.title' THEN
   RAISE EXCEPTION 'TASK_ERP_POSTCONDITION_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
 SELECT * INTO current_task FROM public.tasks WHERE id=p_task_id;
 IF r.phase='returned' THEN
   SELECT jsonb_build_object('id',i.id,'task_id',i.task_id,'tool_call_id',i.tool_call_id,'args_hash',i.args_hash,
     'tool_name',i.tool_name,'status',i.status,'result',i.result,
     'started_at',i.started_at,'completed_at',i.completed_at,'write_returned_at',r.updated_at) INTO witness
   FROM public.tool_invocations i JOIN public.tasks source ON source.id=i.task_id
   WHERE i.conversation_id=current_task.conversation_id AND source.conversation_id=current_task.conversation_id
     AND source.user_id=current_task.user_id AND source.org_id IS NOT DISTINCT FROM current_task.org_id AND source.type='chat'
     AND (source.id=p_task_id OR source.id=p_logical_task_id OR source.delivery_context->>'logical_task_id'=p_logical_task_id::text)
     AND i.tool_name='erp_product_query' AND i.status='succeeded' AND i.started_at>r.updated_at AND i.completed_at>=i.started_at
     AND i.result#>'{tool_result,execution}' @> '{"status":"succeeded","handler_started":true,"cancelled":false,"cached":false,"replayed":false}'::jsonb
     AND i.result#>>'{tool_result,raw,metadata,_erp_readback_v1,method}'='item.single.get'
     AND i.result#>'{tool_result,raw,metadata,_erp_readback_v1,parameters}'=
       jsonb_build_object('outerId',proof#>>'{dispatch,postcondition,outer_id}')
   ORDER BY i.started_at DESC,i.id DESC LIMIT 1;
 END IF;
 RETURN jsonb_build_object('outcome','found','proof',proof||jsonb_build_object('readback',witness));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_erp_postcondition(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_erp_postcondition(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;

SET LOCAL ROLE everydayai;
DROP FUNCTION public.agent_erp_session_rotation_path(uuid,text,text,timestamptz);
DROP FUNCTION public.record_agent_erp_session_rotation(uuid,text,text);
DROP TRIGGER agent_erp_session_rotation_immutable ON public.agent_erp_session_rotations;
DROP FUNCTION public.guard_agent_erp_session_rotation();
DROP TABLE public.agent_erp_session_rotations;
RESET ROLE;
