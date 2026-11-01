-- Original restore publication and deleted_files settlement share native identity.
SET LOCAL ROLE everydayai;
CREATE UNIQUE INDEX agent_task_file_restore_record ON public.agent_task_native_writes((intent->>'record_id'))
 WHERE domain='file' AND intent->>'action'='restore' AND phase IN ('dispatched','returned');
CREATE FUNCTION public.prepare_agent_task_file_restore(p_task_id uuid,p_execution_token uuid,p_call_id text,p_args_hash text,p_intent jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; i public.tool_invocations%ROWTYPE;
 op jsonb; checked jsonb; r public.agent_task_native_writes%ROWTYPE; d public.deleted_files%ROWTYPE;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=p_task_id FOR UPDATE;
 IF s.root_task_id IS NULL THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,s.root_task_id,s.revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' OR s.state->>'image_binding_version' IS DISTINCT FROM '3'
   OR jsonb_typeof(p_intent) IS DISTINCT FROM 'object'
   OR octet_length(p_intent::text)>8192 THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO i FROM public.tool_invocations WHERE task_id=t.id AND turn_id=t.turn_id AND conversation_id=t.conversation_id
   AND tool_call_id=p_call_id AND tool_name='restore_file' AND status='running' AND args_hash=p_args_hash;
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'owner_task_id'=t.id::text
   AND v->>'owner_turn_id'=t.turn_id::text AND v->>'invocation_id'=p_call_id AND v->>'tool_name'=i.tool_name
   AND v->>'capability'='platform.restore_file' AND v->>'arguments_sha256'=i.args_hash AND v->>'status'='reserved';
 IF i.id IS NULL OR op IS NULL THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 IF p_intent->>'action' IS DISTINCT FROM 'restore' OR (p_intent->>'record_id' ~ '^[1-9][0-9]*$') IS NOT TRUE
    OR NULLIF(p_intent->>'backup_etag','') IS NULL THEN
   RAISE EXCEPTION 'TASK_FILE_RESTORE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.deleted_files AS df WHERE id=(p_intent->>'record_id')::bigint
   AND user_id=t.user_id AND org_id IS NOT DISTINCT FROM t.org_id AND NOT purged AND purge_after>now()
   AND COALESCE(to_jsonb(df)->>'native_status','completed')='completed'
   AND relative_path=p_intent->>'relative_path' AND oss_object_key=p_intent->>'oss_object_key';
 IF d.id IS NULL THEN RAISE EXCEPTION 'TASK_FILE_RESTORE_RECORD_CHANGED' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=op->>'id' FOR UPDATE;
 IF r.operation_id IS NOT NULL THEN
   IF r.native_invocation_id=i.id AND r.args_hash=i.args_hash AND r.intent=p_intent AND r.domain='file' THEN
     RETURN jsonb_build_object('operation_id',r.operation_id,'native_id',i.id,'dispatch_token',r.dispatch_token); END IF;
   -- Only the original not-started evidence authorizes a newly fenced attempt.
   IF r.phase<>'prepared' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op->'previous_attempts') old
       WHERE old->>'invocation_id'=(SELECT tool_call_id FROM public.tool_invocations WHERE id=r.native_invocation_id)
         AND old->>'arguments_sha256'=r.args_hash AND old->>'owner_task_id'=r.owner_task_id::text) THEN
     RAISE EXCEPTION 'TASK_NATIVE_WRITE_ALREADY_DISPATCHED' USING ERRCODE='42501'; END IF;
 END IF;
 INSERT INTO public.agent_task_native_writes(operation_id,native_invocation_id,owner_task_id,actor_user_id,org_id,domain,args_hash,intent,execution_token,dispatch_token)
 VALUES(op->>'id',i.id,t.id,t.user_id,t.org_id,'file',i.args_hash,p_intent,p_execution_token,gen_random_uuid())
 ON CONFLICT(operation_id) DO UPDATE SET native_invocation_id=EXCLUDED.native_invocation_id,owner_task_id=EXCLUDED.owner_task_id,
   args_hash=EXCLUDED.args_hash,intent=EXCLUDED.intent,execution_token=EXCLUDED.execution_token,dispatch_token=EXCLUDED.dispatch_token,
   dispatch=NULL,response=NULL,phase='prepared',updated_at=now()
 RETURNING * INTO r;
 RETURN jsonb_build_object('operation_id',r.operation_id,'native_id',i.id,'dispatch_token',r.dispatch_token);
END $$;
REVOKE ALL ON FUNCTION public.prepare_agent_task_file_restore(uuid,uuid,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.prepare_agent_task_file_restore(uuid,uuid,text,text,jsonb) TO everydayai;


CREATE FUNCTION public.complete_agent_task_file_restore(p_operation_id text,p_dispatch_token uuid,p_evidence jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE r public.agent_task_native_writes%ROWTYPE; d public.deleted_files%ROWTYPE; value jsonb;
BEGIN
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id FOR UPDATE;
 IF r.operation_id IS NULL OR r.domain IS DISTINCT FROM 'file' OR r.intent->>'action' IS DISTINCT FROM 'restore'
   OR r.dispatch_token IS DISTINCT FROM p_dispatch_token OR r.phase NOT IN ('dispatched','returned')
   OR p_evidence IS NULL OR p_evidence IS DISTINCT FROM r.dispatch
   OR r.dispatch->>'method' IS DISTINCT FROM 'restore_file'
   OR r.dispatch->>'relative_path' IS DISTINCT FROM r.intent->>'relative_path'
   OR r.dispatch->>'backup_etag' IS DISTINCT FROM r.intent->>'backup_etag'
   OR (r.dispatch->>'sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
   OR jsonb_typeof(r.dispatch->'identity') IS DISTINCT FROM 'array'
   OR jsonb_array_length(r.dispatch->'identity')<>3 THEN
   RAISE EXCEPTION 'TASK_FILE_RESTORE_EVIDENCE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.deleted_files AS df WHERE id=(r.intent->>'record_id')::bigint
   AND user_id=r.actor_user_id AND org_id IS NOT DISTINCT FROM r.org_id AND relative_path=r.intent->>'relative_path'
   AND oss_object_key=r.intent->>'oss_object_key' FOR UPDATE;
 IF d.id IS NULL THEN RAISE EXCEPTION 'TASK_FILE_RESTORE_RECORD_CHANGED' USING ERRCODE='42501'; END IF;
 value:=jsonb_build_object('method','restore_file','body',jsonb_build_object('success',true,'record_id',d.id,'restored',p_evidence));
 IF r.phase='returned' THEN
   IF r.response IS DISTINCT FROM value OR d.purged IS NOT TRUE THEN
     RAISE EXCEPTION 'TASK_FILE_RESTORE_RECEIPT_CHANGED' USING ERRCODE='42501'; END IF;
 ELSE
   UPDATE public.deleted_files SET purged=true WHERE id=d.id;
   UPDATE public.agent_task_native_writes SET phase='returned',response=value,updated_at=now() WHERE operation_id=p_operation_id;
 END IF;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.complete_agent_task_file_restore(text,uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.complete_agent_task_file_restore(text,uuid,jsonb) TO everydayai;

CREATE FUNCTION public.reconcile_agent_task_file_restore(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text,p_evidence jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; r public.agent_task_native_writes%ROWTYPE;
BEGIN
 -- Active current owner is mandatory for reading facts into a continuation.
 -- Native settlement above can finish after lease loss without a new send.
 checked:=public.read_agent_task_native_write(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
 PERFORM public.complete_agent_task_file_restore(p_operation_id,r.dispatch_token,p_evidence);
 RETURN public.read_agent_task_native_write(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
END $$;
REVOKE ALL ON FUNCTION public.reconcile_agent_task_file_restore(uuid,uuid,uuid,bigint,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reconcile_agent_task_file_restore(uuid,uuid,uuid,bigint,text,jsonb) TO everydayai;
RESET ROLE;
