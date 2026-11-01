-- Resume only the untouched original versions through another real file tool call.
-- Original invocation/result and original per-file settlements are never rewritten.
SET LOCAL ROLE everydayai;
CREATE TABLE public.agent_task_file_delete_resumptions(
 native_invocation_id uuid PRIMARY KEY REFERENCES public.tool_invocations(id) ON DELETE CASCADE,
 operation_id text NOT NULL, original_native_id uuid NOT NULL,
 owner_task_id uuid NOT NULL, actor_user_id uuid NOT NULL, org_id uuid,
 execution_token uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.agent_task_file_delete_resumptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_file_delete_resumptions FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_task_file_delete_resumptions_scope ON public.agent_task_file_delete_resumptions
 USING(current_setting('app.access_kind',true)='runtime' AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid)
 WITH CHECK(current_setting('app.access_kind',true)='runtime' AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid);

CREATE FUNCTION public.read_agent_task_file_delete_resume(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_operation_id text,p_call_id text,p_args_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; op jsonb; t public.tasks%ROWTYPE; i public.tool_invocations%ROWTYPE;
BEGIN
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 SELECT value INTO op FROM public.agent_task_states s,jsonb_array_elements(s.state->'operations')
 WHERE s.root_task_id=p_logical_task_id AND value->>'id'=p_operation_id;
 IF op->>'tool_name' IS DISTINCT FROM 'file_delete' OR op->>'arguments_sha256' IS DISTINCT FROM p_args_hash THEN
   RAISE EXCEPTION 'TASK_FILE_RESUME_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT native.* INTO i FROM public.tool_invocations native JOIN public.agent_task_file_delete_resumptions r ON r.native_invocation_id=native.id
 WHERE native.task_id=t.id AND native.turn_id=t.turn_id AND native.tool_call_id=p_call_id AND native.tool_name='file_delete'
   AND native.args_hash=p_args_hash AND r.operation_id=p_operation_id AND r.actor_user_id=t.user_id AND r.org_id IS NOT DISTINCT FROM t.org_id;
 IF i.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','status',i.status,'result',i.result);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,text) TO everydayai;

CREATE FUNCTION public.begin_agent_task_file_delete_resume(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_operation_id text,p_call_id text,p_args_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; op jsonb; started jsonb; t public.tasks%ROWTYPE; native uuid;
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 PERFORM 1 FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 checked:=public.read_agent_task_file_delete(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 SELECT value INTO op FROM public.agent_task_states s,jsonb_array_elements(s.state->'operations')
 WHERE s.root_task_id=p_logical_task_id AND value->>'id'=p_operation_id;
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 IF op->>'status' IS DISTINCT FROM 'uncertain' OR op->>'arguments_sha256' IS DISTINCT FROM p_args_hash
   OR (op->>'owner_task_id'=p_task_id::text AND op->>'invocation_id'=p_call_id)
   OR proof->>'phase' IS DISTINCT FROM 'dispatched' OR NOT EXISTS(
     SELECT 1 FROM public.agent_task_file_deletions f JOIN public.deleted_files d ON d.id=f.deleted_record_id
     WHERE f.native_invocation_id=(proof->>'native_id')::uuid AND f.phase='backed_up' AND f.armed
       AND NOT d.purged AND d.purge_after>now()) THEN
   RAISE EXCEPTION 'TASK_FILE_RESUME_NOT_ADMISSIBLE' USING ERRCODE='42501'; END IF;
 started:=public.begin_tool_invocation(t.id,t.conversation_id,t.turn_id,p_execution_token,p_call_id,'file_delete',p_args_hash);
 IF started->>'outcome' IS DISTINCT FROM 'execute' THEN RETURN started; END IF;
 SELECT id INTO native FROM public.tool_invocations WHERE task_id=t.id AND turn_id=t.turn_id AND tool_call_id=p_call_id;
 INSERT INTO public.agent_task_file_delete_resumptions(native_invocation_id,operation_id,original_native_id,owner_task_id,actor_user_id,org_id,execution_token)
 VALUES(native,p_operation_id,(proof->>'native_id')::uuid,t.id,t.user_id,t.org_id,p_execution_token);
 RETURN jsonb_build_object('outcome','execute','native_id',native);
END $$;
REVOKE ALL ON FUNCTION public.begin_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.begin_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,text) TO everydayai;

CREATE FUNCTION public.authorize_agent_task_file_delete_resume(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_operation_id text,p_call_id text,p_item_index int) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; native public.tool_invocations%ROWTYPE; link public.agent_task_file_delete_resumptions%ROWTYPE; token uuid;
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 PERFORM 1 FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 checked:=public.read_agent_task_file_delete(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 SELECT i.* INTO native FROM public.tool_invocations i JOIN public.tasks t ON t.id=i.task_id
 WHERE i.task_id=p_task_id AND i.turn_id=t.turn_id AND i.tool_call_id=p_call_id AND i.tool_name='file_delete'
   AND i.args_hash=proof->>'args_hash' AND i.status='running' AND i.execution_token=p_execution_token;
 SELECT * INTO link FROM public.agent_task_file_delete_resumptions WHERE native_invocation_id=native.id;
 IF link.operation_id IS DISTINCT FROM p_operation_id OR link.original_native_id IS DISTINCT FROM (proof->>'native_id')::uuid
   OR link.execution_token IS DISTINCT FROM p_execution_token OR proof->>'phase' IS DISTINCT FROM 'dispatched'
   OR NOT EXISTS(SELECT 1 FROM public.agent_task_states s,jsonb_array_elements(s.state->'operations') op
      WHERE s.root_task_id=p_logical_task_id AND op->>'id'=p_operation_id AND op->>'status'='uncertain')
   OR NOT EXISTS(SELECT 1 FROM public.agent_task_file_deletions f JOIN public.deleted_files d ON d.id=f.deleted_record_id
      WHERE f.native_invocation_id=link.original_native_id AND f.item_index=p_item_index AND f.phase='backed_up' AND f.armed
        AND f.manifest=(proof#>'{intent,items}'->p_item_index) AND NOT d.purged AND d.purge_after>now()) THEN
   RAISE EXCEPTION 'TASK_FILE_RESUME_DISPATCH_DENIED' USING ERRCODE='42501'; END IF;
 SELECT dispatch_token INTO token FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
 -- Host-private settlement capability, never part of a model/UI result.
 RETURN jsonb_build_object('outcome','ready','dispatch_token',token,'native_id',link.original_native_id);
END $$;
REVOKE ALL ON FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,int) TO everydayai;

CREATE FUNCTION public.complete_agent_task_file_delete_resume(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_operation_id text,p_call_id text,p_result jsonb,p_status text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; t public.tasks%ROWTYPE;
BEGIN
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 IF NOT EXISTS(SELECT 1 FROM public.agent_task_file_delete_resumptions r JOIN public.tool_invocations i ON i.id=r.native_invocation_id
     WHERE r.operation_id=p_operation_id AND i.task_id=t.id AND i.turn_id=t.turn_id AND i.tool_call_id=p_call_id
       AND r.execution_token=p_execution_token) THEN RAISE EXCEPTION 'TASK_FILE_RESUME_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 RETURN public.complete_tool_invocation(t.id,t.turn_id,p_call_id,p_execution_token,p_status,p_result,'');
END $$;
REVOKE ALL ON FUNCTION public.complete_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.complete_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,jsonb,text) TO everydayai;
RESET ROLE;
