-- Business handlers retain ownership of IO. This journal captures their native
-- dispatch boundary and real response before formatting or UI delivery.
SET LOCAL ROLE everydayai;
CREATE TABLE public.agent_task_native_writes (
 operation_id text PRIMARY KEY CHECK(length(operation_id)=64),
 native_invocation_id uuid UNIQUE REFERENCES public.tool_invocations(id) ON DELETE SET NULL,
 owner_task_id uuid NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
 actor_user_id uuid NOT NULL,org_id uuid,domain text NOT NULL CHECK(domain IN ('erp','file')),
 args_hash text NOT NULL CHECK(length(args_hash)=64),intent jsonb NOT NULL,
 execution_token uuid NOT NULL,dispatch_token uuid NOT NULL,phase text NOT NULL DEFAULT 'prepared' CHECK(phase IN ('prepared','dispatched','returned')),
 dispatch jsonb,response jsonb,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.agent_task_native_writes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_native_writes FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_task_native_write_scope ON public.agent_task_native_writes
 USING(current_setting('app.access_kind',true)='runtime'
   AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid)
 WITH CHECK(current_setting('app.access_kind',true)='runtime'
   AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid);

CREATE FUNCTION public.prepare_agent_task_native_write(p_task_id uuid,p_execution_token uuid,p_call_id text,p_args_hash text,p_domain text,p_intent jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; i public.tool_invocations%ROWTYPE;
 op jsonb; checked jsonb; r public.agent_task_native_writes%ROWTYPE;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=p_task_id FOR UPDATE;
 IF s.root_task_id IS NULL THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,s.root_task_id,s.revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' OR s.state->>'image_binding_version' IS DISTINCT FROM '3'
   OR p_domain IS DISTINCT FROM 'erp' OR jsonb_typeof(p_intent) IS DISTINCT FROM 'object'
   OR octet_length(p_intent::text)>8192 THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO i FROM public.tool_invocations WHERE task_id=t.id AND turn_id=t.turn_id AND conversation_id=t.conversation_id
   AND tool_call_id=p_call_id AND tool_name='erp_execute' AND status='running' AND args_hash=p_args_hash;
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'owner_task_id'=t.id::text
   AND v->>'owner_turn_id'=t.turn_id::text AND v->>'invocation_id'=p_call_id AND v->>'tool_name'=i.tool_name
   AND v->>'capability'='platform.erp_execute' AND v->>'arguments_sha256'=i.args_hash AND v->>'status'='reserved';
 IF i.id IS NULL OR op IS NULL THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=op->>'id' FOR UPDATE;
 IF r.operation_id IS NOT NULL THEN
   IF r.native_invocation_id=i.id AND r.args_hash=i.args_hash AND r.intent=p_intent AND r.domain=p_domain THEN
     RETURN jsonb_build_object('operation_id',r.operation_id,'native_id',i.id,'dispatch_token',r.dispatch_token); END IF;
   -- Only the original not-started evidence authorizes a newly fenced attempt.
   IF r.phase<>'prepared' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op->'previous_attempts') old
       WHERE old->>'invocation_id'=(SELECT tool_call_id FROM public.tool_invocations WHERE id=r.native_invocation_id)
         AND old->>'arguments_sha256'=r.args_hash AND old->>'owner_task_id'=r.owner_task_id::text) THEN
     RAISE EXCEPTION 'TASK_NATIVE_WRITE_ALREADY_DISPATCHED' USING ERRCODE='42501'; END IF;
 END IF;
 INSERT INTO public.agent_task_native_writes(operation_id,native_invocation_id,owner_task_id,actor_user_id,org_id,domain,args_hash,intent,execution_token,dispatch_token)
 VALUES(op->>'id',i.id,t.id,t.user_id,t.org_id,p_domain,i.args_hash,p_intent,p_execution_token,gen_random_uuid())
 ON CONFLICT(operation_id) DO UPDATE SET native_invocation_id=EXCLUDED.native_invocation_id,owner_task_id=EXCLUDED.owner_task_id,
   args_hash=EXCLUDED.args_hash,intent=EXCLUDED.intent,execution_token=EXCLUDED.execution_token,dispatch_token=EXCLUDED.dispatch_token,
   dispatch=NULL,response=NULL,phase='prepared',updated_at=now()
 RETURNING * INTO r;
 RETURN jsonb_build_object('operation_id',r.operation_id,'native_id',i.id,'dispatch_token',r.dispatch_token);
END $$;
REVOKE ALL ON FUNCTION public.prepare_agent_task_native_write(uuid,uuid,text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.prepare_agent_task_native_write(uuid,uuid,text,text,text,jsonb) TO everydayai;

CREATE FUNCTION public.record_agent_task_native_write(p_operation_id text,p_dispatch_token uuid,p_phase text,p_value jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE r public.agent_task_native_writes%ROWTYPE; t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; checked jsonb;
BEGIN
 -- Dispatch requires the original live Actor fence, even if cancellation or
 -- lease expiry happened after argument validation. Returned facts may settle
 -- later without authorizing a new send. Keep task -> state -> journal order.
 IF p_phase='dispatched' THEN
   SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
   SELECT * INTO t FROM public.tasks WHERE id=r.owner_task_id FOR UPDATE;
   SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=t.id FOR UPDATE;
   IF s.root_task_id IS NULL OR s.state->>'status' IS DISTINCT FROM 'active' THEN
     RAISE EXCEPTION 'TASK_NATIVE_WRITE_DISPATCH_DENIED' USING ERRCODE='42501'; END IF;
   checked:=public.read_agent_task_native_results(t.id,r.execution_token,s.root_task_id,s.revision);
   IF checked->>'outcome' IS DISTINCT FROM 'found' OR NOT EXISTS(
     SELECT 1 FROM jsonb_array_elements(s.state->'operations') op WHERE op->>'id'=p_operation_id
       AND op->>'status'='reserved' AND op->>'owner_task_id'=t.id::text)
     OR NOT EXISTS(SELECT 1 FROM public.tool_invocations i WHERE i.id=r.native_invocation_id AND i.status='running') THEN
     RAISE EXCEPTION 'TASK_NATIVE_WRITE_DISPATCH_DENIED' USING ERRCODE='42501'; END IF;
 END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id FOR UPDATE;
 IF r.operation_id IS NULL OR r.dispatch_token IS DISTINCT FROM p_dispatch_token OR p_value IS NULL
   OR jsonb_typeof(p_value) IS DISTINCT FROM 'object' OR octet_length(p_value::text)>1048576
   OR p_phase IS NULL OR p_phase NOT IN ('dispatched','returned') THEN
   RAISE EXCEPTION 'TASK_NATIVE_WRITE_RECORD_DENIED' USING ERRCODE='42501'; END IF;
 IF r.domain='file' AND p_phase='returned' THEN RAISE EXCEPTION 'TASK_FILE_DOMAIN_SETTLEMENT_REQUIRED' USING ERRCODE='42501'; END IF;
 IF p_phase='returned' AND r.phase='returned' AND r.response=p_value THEN
   RETURN jsonb_build_object('outcome','recorded'); END IF;
 IF r.domain='file' AND r.intent->>'action'='delete' AND p_phase='dispatched' THEN
   IF p_value IS DISTINCT FROM jsonb_build_object('method','file_delete','items',r.intent->'items')
     OR (SELECT count(*) FROM public.agent_task_file_deletions f JOIN public.deleted_files d ON d.id=f.deleted_record_id
         WHERE f.native_invocation_id=r.native_invocation_id AND f.phase='backed_up' AND NOT d.purged AND d.purge_after>now())
          <>jsonb_array_length(r.intent->'items') THEN
     RAISE EXCEPTION 'TASK_FILE_DELETE_BACKUPS_REQUIRED' USING ERRCODE='42501'; END IF;
   UPDATE public.agent_task_file_deletions SET armed=true WHERE native_invocation_id=r.native_invocation_id;
 END IF;
 IF p_phase='dispatched' AND r.phase='prepared' THEN
   UPDATE public.agent_task_native_writes SET phase='dispatched',dispatch=p_value,updated_at=now() WHERE operation_id=p_operation_id;
 ELSIF p_phase='returned' AND r.phase='dispatched' THEN
   UPDATE public.agent_task_native_writes SET phase='returned',response=p_value,updated_at=now() WHERE operation_id=p_operation_id;
 ELSE RAISE EXCEPTION 'TASK_NATIVE_WRITE_TRANSITION_DENIED' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.record_agent_task_native_write(text,uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_agent_task_native_write(text,uuid,text,jsonb) TO everydayai;

CREATE FUNCTION public.read_agent_task_native_write(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; item jsonb; r public.agent_task_native_writes%ROWTYPE;
BEGIN
 checked:=public.read_agent_task_effect_result(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 item:=checked->'record';
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id
   AND native_invocation_id=(item->>'id')::uuid AND owner_task_id=(item->>'task_id')::uuid AND args_hash=item->>'args_hash';
 IF r.operation_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','proof',jsonb_build_object('operation_id',r.operation_id,'native_id',r.native_invocation_id,
   'task_id',item->>'task_id','turn_id',item->>'turn_id','tool_call_id',item->>'tool_call_id','args_hash',r.args_hash,
   'actor_user_id',r.actor_user_id,'org_id',r.org_id,'domain',r.domain,'phase',r.phase,'intent',r.intent,
   'dispatch',r.dispatch,'response',r.response));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_native_write(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_native_write(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
