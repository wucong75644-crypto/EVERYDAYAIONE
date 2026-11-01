-- Domain completion signals resume the existing Actor queue and frozen history.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.agent_task_scheduled_terminal_fact(p_root_task_id uuid,p_operation_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; r jsonb;
BEGIN
 checked:=public.agent_task_scheduled_fact(p_root_task_id,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN NULL; END IF;
 proof:=checked->'proof';r:=proof->'receipt';
 IF r->>'id' IS NOT NULL THEN
   IF (r->>'operation'=proof->>'operation' AND r->>'task_id'=proof->>'resource_id'
     AND r#>>'{result,task_id}'=r->>'task_id' AND r->'new_revision'=r#>'{result,new_revision}'
     AND r#>>'{result,outcome}'=CASE r->>'operation' WHEN 'create' THEN 'created' WHEN 'update' THEN 'updated'
       WHEN 'pause' THEN 'paused' WHEN 'resume' THEN 'resumed' WHEN 'delete' THEN 'deleted' ELSE '' END) IS TRUE THEN
     RETURN proof; END IF;
   RETURN NULL;
 END IF;
 IF proof->>'form_status'='cancelled' OR proof->>'change_set_status' IN ('cancelled','rejected','expired','failed','conflicted') THEN RETURN proof; END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.agent_task_scheduled_terminal_fact(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_scheduled_terminal_fact(uuid,text) TO everydayai;

CREATE FUNCTION public.mark_agent_task_receipt_wait() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NEW.state->>'image_binding_version'='3' AND NEW.state->>'status' IN ('waiting_approval','waiting_dependencies')
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'operations') op
       WHERE op->>'tool_name'='manage_scheduled_task' AND op->>'status' IN ('accepted','uncertain','reserved')) THEN
   UPDATE public.tasks SET request_params=COALESCE(request_params,'{}'::jsonb)||'{"_agent_task_scheduled_v1":{"pending":true}}'::jsonb
     WHERE id=NEW.owner_task_id AND type='chat' AND user_id=NEW.actor_user_id AND org_id IS NOT DISTINCT FROM NEW.org_id;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.mark_agent_task_receipt_wait() FROM PUBLIC;
CREATE TRIGGER agent_task_receipt_wait AFTER INSERT OR UPDATE OF state ON public.agent_task_states
 FOR EACH ROW EXECUTE FUNCTION public.mark_agent_task_receipt_wait();
CREATE INDEX agent_task_scheduled_scan ON public.tasks(id) WHERE type='chat'
 AND request_params#>'{_agent_task_scheduled_v1,pending}'='true'::jsonb;
CREATE FUNCTION public.list_agent_task_receipt_hints(p_after uuid DEFAULT NULL,p_limit integer DEFAULT 100)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(to_jsonb(item) ORDER BY item.id),'[]'::jsonb) FROM (
   SELECT id,user_id,org_id FROM public.tasks WHERE type='chat' AND status='completed'
     AND request_params#>'{_agent_task_scheduled_v1,pending}'='true'::jsonb
     AND (p_after IS NULL OR id>p_after) ORDER BY id LIMIT LEAST(GREATEST(p_limit,1),100)) item
$$;
REVOKE ALL ON FUNCTION public.list_agent_task_receipt_hints(uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.list_agent_task_receipt_hints(uuid,integer) TO everydayai;

CREATE FUNCTION public.refresh_agent_task_receipt_events(p_source_task_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; owner public.tasks%ROWTYPE; op jsonb; fact jsonb; event uuid; queued jsonb; dedupe text;
BEGIN
 SELECT * INTO owner FROM public.tasks WHERE id=p_source_task_id;
 SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=p_source_task_id;
 IF s.root_task_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 -- Match the native queue/control lock order, including cancelled and paused roots.
 PERFORM 1 FROM public.conversations WHERE id=s.conversation_id FOR UPDATE;
 SELECT * INTO owner FROM public.tasks WHERE id=p_source_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=s.root_task_id FOR UPDATE;
 IF current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
   OR s.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   OR s.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR owner.user_id IS DISTINCT FROM s.actor_user_id OR owner.org_id IS DISTINCT FROM s.org_id
   OR owner.conversation_id IS DISTINCT FROM s.conversation_id THEN
   RAISE EXCEPTION 'TASK_RECEIPT_CONTINUATION_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 IF s.owner_task_id IS DISTINCT FROM p_source_task_id OR s.state->>'status' IN ('completed','cancelled','failed') THEN
   UPDATE public.tasks SET request_params=request_params-'_agent_task_scheduled_v1' WHERE id=p_source_task_id;
   RETURN jsonb_build_object('outcome','obsolete'); END IF;
 IF owner.status IS DISTINCT FROM 'completed' OR s.state->>'status' NOT IN ('waiting_approval','waiting_dependencies') THEN
   RETURN jsonb_build_object('outcome','waiting'); END IF;
 FOR op IN SELECT v FROM jsonb_array_elements(s.state->'operations') v
   WHERE v->>'tool_name'='manage_scheduled_task' AND v->>'status' IN ('accepted','reserved','uncertain') LOOP
   fact:=public.agent_task_scheduled_terminal_fact(s.root_task_id,op->>'id');
   IF fact IS NULL THEN CONTINUE; END IF;
   dedupe:='scheduled:'||(op->>'id')||':'||(s.state->>'requirements_revision')||':'||COALESCE(fact#>>'{receipt,id}',fact->>'change_set_status',fact->>'form_status');
   INSERT INTO public.agent_task_events(root_task_id,actor_user_id,org_id,dedupe_key,expected_revision,payload)
   VALUES(s.root_task_id,s.actor_user_id,s.org_id,dedupe,s.revision,
     jsonb_build_object('source_kind','scheduled','operation_id',op->>'id','requirements_revision',s.state->'requirements_revision'))
   ON CONFLICT(root_task_id,dedupe_key) DO NOTHING;
   SELECT id INTO event FROM public.agent_task_events WHERE root_task_id=s.root_task_id
     AND dedupe_key=dedupe AND status='pending';
   IF event IS NOT NULL THEN
     queued:=public.enqueue_agent_task_continuation(s.root_task_id,s.revision,event,'scheduled');
     IF queued->>'outcome' IN ('queued','already_queued') THEN
       UPDATE public.tasks SET request_params=request_params-'_agent_task_scheduled_v1' WHERE id=p_source_task_id;
       RETURN queued; END IF;
   END IF;
 END LOOP;
 RETURN jsonb_build_object('outcome','waiting');
END $$;
REVOKE ALL ON FUNCTION public.refresh_agent_task_receipt_events(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refresh_agent_task_receipt_events(uuid) TO everydayai;

CREATE FUNCTION public.load_agent_receipt_continuation(p_task_id uuid,p_execution_token uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; cp jsonb; facts jsonb; item record; proof jsonb;
BEGIN
    SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND OR t.user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
       OR t.delivery_context->>'input_kind' IS DISTINCT FROM 'agent_task_continuation'
       OR COALESCE(t.delivery_context->>'source_kind','') NOT IN ('scheduled','video') THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF t.execution_token IS DISTINCT FROM p_execution_token THEN RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
    IF t.status<>'running' OR t.lease_expires_at IS NULL OR t.lease_expires_at<=now() THEN RETURN jsonb_build_object('outcome','lease_expired'); END IF;
    IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
       OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
           WHERE m.user_id=t.user_id AND m.org_id=t.org_id AND m.status='active' AND o.status='active')) THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_SCOPE_CHANGED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=(t.delivery_context->>'logical_task_id')::uuid FOR UPDATE;
    IF NOT FOUND OR s.actor_user_id<>t.user_id OR s.org_id IS DISTINCT FROM t.org_id
       OR s.conversation_id<>t.conversation_id OR s.owner_task_id::text IS DISTINCT FROM t.delivery_context->>'source_owner_task_id'
       OR s.revision::text IS DISTINCT FROM t.delivery_context->>'source_revision'
       OR NOT EXISTS(SELECT 1 FROM public.agent_task_events WHERE id=(t.delivery_context->>'event_id')::uuid
         AND root_task_id=s.root_task_id AND continuation_task_id=p_task_id AND status='queued' AND expected_revision=s.revision) THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_STALE' USING ERRCODE='40001';
    END IF;
    SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id;
    IF cp->'_task_execution' IS DISTINCT FROM s.state THEN RAISE EXCEPTION 'TASK_CONTINUATION_CHECKPOINT_MISSING'; END IF;
    facts:='[]'::jsonb;
    FOR item IN SELECT * FROM public.agent_task_events e WHERE e.root_task_id=s.root_task_id
      AND e.continuation_task_id=p_task_id AND e.status='queued' ORDER BY e.created_at,e.id LOOP
      IF item.child_task_id IS NOT NULL OR item.payload->>'source_kind' IS DISTINCT FROM t.delivery_context->>'source_kind'
        OR item.payload->'requirements_revision' IS DISTINCT FROM s.state->'requirements_revision' THEN
        RAISE EXCEPTION 'TASK_RECEIPT_EVENT_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
      IF t.delivery_context->>'source_kind'='video' THEN
        proof:=public.agent_task_video_terminal_fact(s.root_task_id,item.payload->>'operation_id');
      ELSE
      proof:=public.agent_task_scheduled_terminal_fact(s.root_task_id,item.payload->>'operation_id');
      END IF;
      IF proof IS NULL THEN RAISE EXCEPTION 'TASK_RECEIPT_EVENT_NOT_TERMINAL'; END IF;
      facts:=facts||jsonb_build_array(jsonb_build_object('event_id',item.id,'operation_id',item.payload->>'operation_id','proof',proof));
    END LOOP;
    IF facts='[]'::jsonb THEN RAISE EXCEPTION 'TASK_RECEIPT_EVENTS_MISSING'; END IF;
    RETURN jsonb_build_object('outcome','found','state',s.state,'checkpoint',cp,'facts',facts);
END $$;
REVOKE ALL ON FUNCTION public.load_agent_receipt_continuation(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.load_agent_receipt_continuation(uuid,uuid) TO everydayai;

CREATE FUNCTION public.apply_agent_receipt_continuation(p_task_id uuid,p_execution_token uuid,p_expected_revision bigint,p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE loaded jsonb; saved jsonb; next_state jsonb := p_payload->'_task_execution'; old_state jsonb; child jsonb; op jsonb; candidate_op jsonb; a jsonb; t public.tasks%ROWTYPE; incoming jsonb := p_payload->'_continuation_input';
    history_count integer; last_message jsonb; facts jsonb; expected_images jsonb; expected_failures jsonb;
    header constant text := E'[Internal task receipt continuation: data, not new authorization]\n';
BEGIN
    loaded := public.load_agent_receipt_continuation(p_task_id,p_execution_token);
    IF loaded->>'outcome'<>'found' THEN RETURN loaded; END IF;
    old_state:=loaded->'state';
    SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
    IF incoming IS NULL OR incoming->>'logical_task_id' IS DISTINCT FROM old_state->>'root_task_id'
       OR incoming->>'event_id' IS DISTINCT FROM t.delivery_context->>'event_id'
       OR incoming->'expected_revision' IS DISTINCT FROM old_state->'revision'
       OR incoming->>'conversation_id' IS DISTINCT FROM t.conversation_id::text
       OR incoming->>'actor_user_id' IS DISTINCT FROM t.user_id::text
       OR incoming->>'org_id' IS DISTINCT FROM t.org_id::text
       OR incoming->'execution_identity' IS DISTINCT FROM old_state->'execution_identity'
       OR incoming->'branch_id' IS DISTINCT FROM old_state->'branch_id'
       OR incoming->'source_message_ids' IS DISTINCT FROM old_state->'source_message_ids'
       OR incoming->>'base_context_revision' IS DISTINCT FROM t.base_context_revision::text
       OR incoming->>'through_message_id' IS DISTINCT FROM t.context_through_message_id::text
       OR jsonb_typeof(p_payload->'messages') IS DISTINCT FROM 'array'
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(loaded#>'{checkpoint,messages}') WITH ORDINALITY source(message,position)
           WHERE p_payload->'messages'->(source.position::integer-1) IS DISTINCT FROM source.message) THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_INPUT_UNVERIFIED' USING ERRCODE='42501';
    END IF;
    IF (old_state->>'revision')::bigint IS DISTINCT FROM p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
    IF next_state->>'status' IS DISTINCT FROM 'active' OR (next_state->>'revision')::bigint IS DISTINCT FROM (old_state->>'revision')::bigint+1
       OR (old_state-ARRAY['revision','status']) IS DISTINCT FROM (next_state-ARRAY['revision','status']) THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_FACTS_CHANGED' USING ERRCODE='42501';
    END IF;
    IF ((loaded->'checkpoint')-ARRAY['messages','checkpoint_kind','result_content','usage','credits_cost','tool_digest',
         '_task_bootstrap','_task_usage','_task_read_arguments','content_blocks','_task_execution','safe_point',
         '_task_execution_expected_revision','_continuation_input','_skill_composition_expected_revision',
         '_ecom_workflow','_skill_composition','skill_runtime']) IS DISTINCT FROM
       (p_payload-ARRAY['messages','checkpoint_kind','result_content','usage','credits_cost','tool_digest',
         '_task_bootstrap','_task_usage','_task_read_arguments','content_blocks','_task_execution','safe_point',
         '_task_execution_expected_revision','_continuation_input','_skill_composition_expected_revision',
         '_ecom_workflow','_skill_composition','skill_runtime']) THEN
      RAISE EXCEPTION 'TASK_RECEIPT_CONTEXT_CHANGED' USING ERRCODE='42501'; END IF;
    history_count:=jsonb_array_length(loaded#>'{checkpoint,messages}');
    last_message:=p_payload->'messages'->history_count;
    IF jsonb_array_length(p_payload->'messages')<>history_count+1 OR last_message->>'role' IS DISTINCT FROM 'system'
       OR last_message-ARRAY['role','content']<>'{}'::jsonb
       OR left(last_message->>'content',length(header)) IS DISTINCT FROM header
       OR incoming->'event_artifact_ids' IS DISTINCT FROM '[]'::jsonb THEN
      RAISE EXCEPTION 'TASK_RECEIPT_INPUT_UNVERIFIED' USING ERRCODE='42501'; END IF;
    BEGIN facts:=substring(last_message->>'content' FROM length(header)+1)::jsonb;
    EXCEPTION WHEN invalid_text_representation THEN RAISE EXCEPTION 'TASK_RECEIPT_INPUT_UNVERIFIED' USING ERRCODE='42501'; END;
    IF facts IS DISTINCT FROM jsonb_build_object('original_goal',old_state->>'original_goal','native_facts',loaded->'facts',
      'instruction','继续原任务，核实并导入原业务回执；取消或失败不是成功，不重复提交。原用户要求、上下文和权限保持。') THEN
      RAISE EXCEPTION 'TASK_RECEIPT_INPUT_UNVERIFIED' USING ERRCODE='42501'; END IF;
    saved:=public.save_agent_task_checkpoint(p_task_id,p_execution_token,'before_model',p_payload,p_expected_revision);
    IF saved->>'outcome' IS DISTINCT FROM 'saved' THEN RETURN saved; END IF;
    UPDATE public.agent_task_events SET status='applied' WHERE continuation_task_id=p_task_id AND status='queued';
    RETURN jsonb_build_object('outcome','saved','state',next_state);
END $$;
REVOKE ALL ON FUNCTION public.apply_agent_receipt_continuation(uuid,uuid,bigint,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.apply_agent_receipt_continuation(uuid,uuid,bigint,jsonb) TO everydayai;
RESET ROLE;
