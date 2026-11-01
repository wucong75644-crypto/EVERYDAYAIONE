-- Positive native acceptance proof only. An absent child never grants a retry.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.reconcile_agent_image_operation(
 p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; child public.tasks%ROWTYPE;
 original public.tasks%ROWTYPE; ledger public.tool_invocations%ROWTYPE; m public.messages%ROWTYPE;
 op jsonb; next_op jsonb; next_state jsonb; cp jsonb; saved jsonb; origin jsonb;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
   OR t.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR t.type IS DISTINCT FROM 'chat' OR (t.delivery_context @> '{"actor":true}') IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'TASK_IMAGE_RECONCILIATION_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 IF t.execution_token IS DISTINCT FROM p_execution_token OR t.status<>'running' THEN RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
 IF t.lease_expires_at IS NULL OR t.lease_expires_at<=now() THEN RETURN jsonb_build_object('outcome','lease_expired'); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
   OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members member JOIN public.organizations org ON org.id=member.org_id
      WHERE member.user_id=t.user_id AND member.org_id=t.org_id AND member.status='active' AND org.status='active')) THEN
   RAISE EXCEPTION 'TASK_IMAGE_RECONCILIATION_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 IF s.owner_task_id IS DISTINCT FROM p_task_id OR s.actor_user_id IS DISTINCT FROM t.user_id
   OR s.conversation_id IS DISTINCT FROM t.conversation_id OR s.org_id IS DISTINCT FROM t.org_id THEN
   RAISE EXCEPTION 'TASK_IMAGE_RECONCILIATION_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 IF s.revision<>p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
 IF s.state->>'status'<>'active' THEN RETURN jsonb_build_object('outcome','not_active'); END IF;
 SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=p_operation_id;
 IF op IS NULL OR op->>'tool_name'<>'generate_image' OR op->>'capability'<>'platform.generate_image'
   OR op->>'status' NOT IN ('reserved','uncertain','accepted') THEN
   RAISE EXCEPTION 'TASK_IMAGE_RECONCILIATION_OPERATION_DENIED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO original FROM public.tasks WHERE id=(op->>'owner_task_id')::uuid;
 SELECT * INTO ledger FROM public.tool_invocations WHERE task_id=original.id
   AND turn_id=(op->>'owner_turn_id')::uuid AND tool_call_id=op->>'invocation_id';
 IF original.type IS DISTINCT FROM 'chat' OR original.user_id IS DISTINCT FROM t.user_id
   OR original.org_id IS DISTINCT FROM t.org_id OR original.conversation_id IS DISTINCT FROM t.conversation_id
   OR ledger.tool_name IS DISTINCT FROM 'generate_image' OR ledger.args_hash IS DISTINCT FROM op->>'arguments_sha256'
   OR ledger.conversation_id IS DISTINCT FROM t.conversation_id THEN
   RAISE EXCEPTION 'TASK_IMAGE_RECONCILIATION_LEDGER_DENIED' USING ERRCODE='42501';
 END IF;
 SELECT c.* INTO child FROM public.agent_task_events e JOIN public.tasks c ON c.id=e.child_task_id
   WHERE e.root_task_id=s.root_task_id AND e.operation_id=p_operation_id AND e.actor_user_id=t.user_id
     AND e.org_id IS NOT DISTINCT FROM t.org_id AND c.type='image' AND c.user_id=t.user_id
     AND c.org_id IS NOT DISTINCT FROM t.org_id AND c.conversation_id=t.conversation_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','unresolved'); END IF;
 origin:=child.request_params#>'{_media_request_v1,origin}';
 SELECT * INTO m FROM public.messages WHERE id=child.assistant_message_id;
 IF origin->>'parent_task_id' IS DISTINCT FROM original.id::text
   OR origin->>'tool_call_id' IS DISTINCT FROM op->>'invocation_id'
   OR origin->>'turn_id' IS DISTINCT FROM op->>'owner_turn_id'
   OR origin->>'actor_user_id' IS DISTINCT FROM t.user_id::text
   OR origin->>'org_id' IS DISTINCT FROM t.org_id::text OR origin->>'context_scope' IS DISTINCT FROM 'user'
   OR origin->>'workspace_owner_id' IS DISTINCT FROM s.state#>>'{execution_identity,workspace_owner_id}'
   OR origin->>'conversation_id' IS DISTINCT FROM t.conversation_id::text
   OR m.generation_params->>'task_id' IS DISTINCT FROM child.id::text OR m.role IS DISTINCT FROM 'assistant'
   OR m.org_id IS DISTINCT FROM t.org_id OR m.conversation_id IS DISTINCT FROM t.conversation_id THEN
   RAISE EXCEPTION 'TASK_IMAGE_RECONCILIATION_CHILD_DENIED' USING ERRCODE='42501';
 END IF;
 IF op->>'status'='accepted' AND op->>'receipt_ref' IS DISTINCT FROM 'task:'||child.id::text THEN
   RAISE EXCEPTION 'TASK_IMAGE_RECONCILIATION_RECEIPT_CONFLICT' USING ERRCODE='42501';
 END IF;
 next_op:=op||jsonb_build_object('status','accepted','receipt_ref','task:'||child.id::text);
 next_state:=s.state;
 IF next_op IS DISTINCT FROM op THEN
   next_state:=jsonb_set(jsonb_set(s.state,'{operations}',(SELECT jsonb_agg(CASE WHEN value->>'id'=p_operation_id THEN next_op ELSE value END)
     FROM jsonb_array_elements(s.state->'operations'))),'{revision}',to_jsonb(s.revision+1));
   SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=p_task_id FOR UPDATE;
   IF cp->'_task_execution' IS DISTINCT FROM s.state THEN RAISE EXCEPTION 'TASK_EXECUTION_CHECKPOINT_MISMATCH' USING ERRCODE='42501'; END IF;
   saved:=public.save_agent_task_checkpoint(p_task_id,p_execution_token,'after_tool',jsonb_set(cp,'{_task_execution}',next_state),s.revision);
   IF saved->>'outcome' IS DISTINCT FROM 'saved' THEN RETURN saved; END IF;
 END IF;
 -- Preserve the original unknown invocation result. This separate fact proves
 -- native acceptance; it does not pretend the missing handler return arrived.
 RETURN jsonb_build_object('outcome','reconciled','state',next_state,'operation',next_op,
   'receipt',jsonb_build_object('task_id',child.id,'message_id',m.id,'accepted',true,'completed',false,'submission_state','accepted'));
END $$;
REVOKE ALL ON FUNCTION public.reconcile_agent_image_operation(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reconcile_agent_image_operation(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
