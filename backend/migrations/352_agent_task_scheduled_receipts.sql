-- Reuse the original ChangeSet and scheduled-task commit ledger. No new scheduler.
SET LOCAL ROLE everydayai;
CREATE TABLE public.agent_task_scheduled_bindings (
 operation_id text PRIMARY KEY CHECK(length(operation_id)=64),
 native_invocation_id uuid UNIQUE REFERENCES public.tool_invocations(id) ON DELETE SET NULL,
 owner_task_id uuid NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
 actor_user_id uuid NOT NULL,org_id uuid NOT NULL,
 action text NOT NULL CHECK(action IN ('create','update','pause','resume','delete')),
 args_hash text NOT NULL CHECK(length(args_hash)=64)
);
ALTER TABLE public.agent_task_scheduled_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_scheduled_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_task_scheduled_binding_scope ON public.agent_task_scheduled_bindings
 USING(current_setting('app.access_kind',true)='runtime'
   AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id=NULLIF(current_setting('app.org_id',true),'')::uuid)
 WITH CHECK(current_setting('app.access_kind',true)='runtime'
   AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id=NULLIF(current_setting('app.org_id',true),'')::uuid);
CREATE FUNCTION public.agent_task_scheduled_request_key(p_task_id uuid,p_execution_token uuid,p_call_id text,p_action text,p_args_hash text)
RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; i public.tool_invocations%ROWTYPE; op jsonb; checked jsonb;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=p_task_id FOR UPDATE;
 IF s.root_task_id IS NULL THEN RAISE EXCEPTION 'TASK_SCHEDULED_NATIVE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,s.root_task_id,s.revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' OR s.state->>'image_binding_version' IS DISTINCT FROM '3' THEN
   RAISE EXCEPTION 'TASK_SCHEDULED_NATIVE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO i FROM public.tool_invocations WHERE task_id=t.id AND turn_id=t.turn_id AND conversation_id=t.conversation_id
   AND tool_call_id=p_call_id AND tool_name='manage_scheduled_task' AND status='running';
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'owner_task_id'=t.id::text
   AND v->>'owner_turn_id'=t.turn_id::text AND v->>'invocation_id'=p_call_id AND v->>'tool_name'='manage_scheduled_task'
   AND v->>'capability'='platform.manage_scheduled_task' AND v->>'arguments_sha256'=i.args_hash AND v->>'status'='reserved';
 IF i.id IS NULL OR op IS NULL OR i.args_hash IS DISTINCT FROM p_args_hash OR p_action IS NULL
   OR p_action NOT IN ('create','update','pause','resume','delete') OR t.org_id IS NULL THEN RAISE EXCEPTION 'TASK_SCHEDULED_NATIVE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 INSERT INTO public.agent_task_scheduled_bindings(operation_id,native_invocation_id,owner_task_id,actor_user_id,org_id,action,args_hash)
 VALUES(op->>'id',i.id,t.id,t.user_id,t.org_id,p_action,i.args_hash) ON CONFLICT DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM public.agent_task_scheduled_bindings b WHERE b.operation_id=op->>'id'
   AND b.native_invocation_id=i.id AND b.owner_task_id=t.id AND b.actor_user_id=t.user_id AND b.org_id=t.org_id
   AND b.action=p_action AND b.args_hash=i.args_hash) THEN
   RAISE EXCEPTION 'TASK_SCHEDULED_NATIVE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 RETURN 'agent-task-scheduled:'||(op->>'id');
END $$;
REVOKE ALL ON FUNCTION public.agent_task_scheduled_request_key(uuid,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_scheduled_request_key(uuid,uuid,text,text,text) TO everydayai;

CREATE FUNCTION public.agent_task_scheduled_fact(p_logical_task_id uuid,p_operation_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; t public.tasks%ROWTYPE; op jsonb; native jsonb; source public.tasks%ROWTYPE;
 cs public.change_sets%ROWTYPE; receipt public.scheduled_task_change_receipts%ROWTYPE; action text;
 binding public.agent_task_scheduled_bindings%ROWTYPE; key text; form_key text; candidates bigint; form_status text;
BEGIN
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id;
 SELECT * INTO t FROM public.tasks WHERE id=s.owner_task_id;
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
   OR s.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   OR s.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR s.state->>'image_binding_version' IS DISTINCT FROM '3'
   OR t.user_id IS DISTINCT FROM s.actor_user_id OR t.org_id IS DISTINCT FROM s.org_id OR t.conversation_id IS DISTINCT FROM s.conversation_id
   OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=s.actor_user_id AND status::text='active')
   OR NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
       WHERE m.user_id=s.actor_user_id AND m.org_id=s.org_id AND m.status='active' AND o.status='active') THEN
   RAISE EXCEPTION 'TASK_SCHEDULED_FACT_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'id'=p_operation_id;
 IF op->>'tool_name' IS DISTINCT FROM 'manage_scheduled_task' OR op->>'capability' IS DISTINCT FROM 'platform.manage_scheduled_task' THEN
   RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT jsonb_build_object('id',i.id,'task_id',i.task_id,'turn_id',i.turn_id,'tool_call_id',i.tool_call_id,'args_hash',i.args_hash) INTO native
 FROM public.tool_invocations i JOIN public.tasks original ON original.id=i.task_id
 WHERE i.task_id=(op->>'owner_task_id')::uuid AND i.turn_id=(op->>'owner_turn_id')::uuid
   AND i.tool_call_id=op->>'invocation_id' AND i.tool_name='manage_scheduled_task' AND i.args_hash=op->>'arguments_sha256'
   AND i.conversation_id=t.conversation_id AND original.user_id=t.user_id AND original.org_id IS NOT DISTINCT FROM t.org_id
   AND original.conversation_id=t.conversation_id AND original.type='chat'
   AND (original.id=t.id OR original.id=s.root_task_id OR original.delivery_context->>'logical_task_id'=s.root_task_id::text);
 IF native IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO binding FROM public.agent_task_scheduled_bindings b WHERE b.operation_id=p_operation_id
   AND b.native_invocation_id=(native->>'id')::uuid AND b.owner_task_id=(native->>'task_id')::uuid
   AND b.actor_user_id=t.user_id AND b.org_id=t.org_id AND b.args_hash=native->>'args_hash';
 IF binding.operation_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 action:=binding.action;
 SELECT * INTO source FROM public.tasks WHERE id=(native->>'task_id')::uuid;
 key:='agent-task-scheduled:'||p_operation_id;
 form_key:='chat-form:'||t.conversation_id::text||':'||source.assistant_message_id::text||':task_effect_'||p_operation_id;
 SELECT count(*) INTO candidates FROM public.change_sets WHERE org_id=t.org_id AND created_by=t.user_id::text
   AND resource_type='scheduled_task' AND operation=action AND idempotency_key IN (key,form_key);
 IF candidates>1 THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO cs FROM public.change_sets WHERE org_id=t.org_id AND created_by=t.user_id::text
   AND resource_type='scheduled_task' AND operation=action AND idempotency_key IN (key,form_key);
 IF cs.id IS NULL THEN
   SELECT part->>'status' INTO form_status FROM public.messages m,
     jsonb_array_elements(m.content::jsonb) part WHERE m.id=source.assistant_message_id
       AND m.conversation_id=t.conversation_id AND m.org_id IS NOT DISTINCT FROM t.org_id AND m.role='assistant'
       AND part->>'type'='form' AND part->>'form_id'='task_effect_'||p_operation_id;
   IF form_status IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 END IF;
 -- The business commit is authoritative even if the later ChangeSet transition was lost.
 SELECT * INTO receipt FROM public.scheduled_task_change_receipts r WHERE r.org_id=t.org_id AND r.change_set_id=cs.id
   AND r.task_id=cs.resource_id::uuid AND r.operation=cs.operation;
 RETURN jsonb_build_object('outcome','found','proof',jsonb_build_object(
   'task_id',native->>'task_id','turn_id',native->>'turn_id','tool_call_id',native->>'tool_call_id','args_hash',native->>'args_hash',
   'actor_user_id',t.user_id,'org_id',t.org_id,'conversation_id',t.conversation_id,
   'form_status',form_status,'change_set_id',cs.id,'change_set_status',cs.status,'operation',cs.operation,'resource_id',cs.resource_id,
   'receipt',CASE WHEN receipt.id IS NULL THEN null ELSE to_jsonb(receipt) END));
END $$;
REVOKE ALL ON FUNCTION public.agent_task_scheduled_fact(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_scheduled_fact(uuid,text) TO everydayai;

CREATE FUNCTION public.read_agent_task_scheduled_receipt(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb;
BEGIN
 checked:=public.read_agent_task_effect_result(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 RETURN public.agent_task_scheduled_fact(p_logical_task_id,p_operation_id);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_scheduled_receipt(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_scheduled_receipt(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
