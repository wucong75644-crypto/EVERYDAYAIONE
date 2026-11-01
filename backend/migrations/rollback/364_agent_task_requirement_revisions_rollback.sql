DO $$ DECLARE owner name; forced boolean; BEGIN
 SELECT pg_get_userbyid(relowner),relforcerowsecurity INTO owner,forced FROM pg_class WHERE oid='public.agent_task_states'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER TABLE public.agent_task_states NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_states WHERE state ? 'requirements_history') THEN
   RAISE EXCEPTION 'TASK_REQUIREMENT_REVISIONS_NOT_DRAINED'; END IF;
 IF forced THEN ALTER TABLE public.agent_task_states FORCE ROW LEVEL SECURITY; END IF;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; forced boolean; BEGIN
 SELECT pg_get_userbyid(relowner),relforcerowsecurity INTO owner,forced FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER TABLE public.conversation_turn_checkpoints NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.conversation_turn_checkpoints WHERE NULLIF(state->'_task_revision_review','null'::jsonb) IS NOT NULL) THEN
   RAISE EXCEPTION 'TASK_REQUIREMENT_REVIEWS_NOT_DRAINED'; END IF;
 IF forced THEN ALTER TABLE public.conversation_turn_checkpoints FORCE ROW LEVEL SECURITY; END IF;
END $$;
RESET ROLE;
SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_user_change_reviews NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_user_change_reviews) OR EXISTS(SELECT 1 FROM public.agent_task_states WHERE state ? 'requirements_history') THEN
   RAISE EXCEPTION 'TASK_REQUIREMENT_REVISIONS_NOT_DRAINED';
 END IF;
 ALTER TABLE public.agent_task_user_change_reviews FORCE ROW LEVEL SECURITY;
END $$;
DO $$ BEGIN IF to_regclass('public.agent_task_native_writes') IS NOT NULL THEN DROP TRIGGER IF EXISTS agent_task_revision_native_dispatch ON public.agent_task_native_writes; END IF; END $$;
DROP FUNCTION public.guard_agent_task_revision_dispatch();
DROP FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text);
ALTER FUNCTION public.begin_agent_task_operation_before364(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) RENAME TO begin_agent_task_operation;
DROP FUNCTION public.check_agent_task_revision_gate(uuid,uuid,uuid,bigint);
DROP FUNCTION public.apply_agent_task_requirement_revision(uuid,uuid,uuid,bigint,jsonb,jsonb);
DROP FUNCTION public.agent_task_revision_projection_valid(jsonb,jsonb,jsonb,jsonb,jsonb);
DROP TRIGGER agent_task_requirement_revision_guard ON public.agent_task_states;
DROP FUNCTION public.guard_agent_task_requirement_revision();
DROP FUNCTION public.read_agent_task_user_change_pending(uuid,uuid,uuid);
DROP FUNCTION public.agent_task_unreviewed_changes(uuid);
DROP FUNCTION public.agent_task_requirement_projection(jsonb);
DROP FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb);
ALTER FUNCTION public.agent_task_method_extension_valid_before364(jsonb,jsonb,jsonb) RENAME TO agent_task_method_extension_valid;
DROP FUNCTION public.agent_task_unprefix_revision_state(jsonb);
DROP TABLE public.agent_task_user_change_reviews;
CREATE OR REPLACE FUNCTION public.enqueue_agent_task_continuation(p_root_task_id uuid,p_expected_revision bigint,p_event_id uuid,p_source_kind text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    s public.agent_task_states%ROWTYPE;
    owner public.tasks%ROWTYPE;
    conv public.conversations%ROWTYPE;
    v_output_id uuid := gen_random_uuid();
    v_task_id uuid := gen_random_uuid();
    selected public.agent_task_events%ROWTYPE;
    params jsonb;
BEGIN
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'TASK_CONTINUATION_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
    SELECT * INTO conv FROM public.conversations WHERE id=s.conversation_id FOR UPDATE;
    SELECT * INTO owner FROM public.tasks WHERE id=s.owner_task_id FOR UPDATE;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id FOR UPDATE;
    SELECT * INTO selected FROM public.agent_task_events WHERE id=p_event_id AND root_task_id=p_root_task_id FOR UPDATE;
    IF NOT FOUND OR p_source_kind IS NULL OR p_source_kind NOT IN ('image','scheduled','video')
       OR (p_source_kind='image' AND selected.child_task_id IS NULL)
       OR (p_source_kind IN ('scheduled','video') AND (selected.child_task_id IS NOT NULL OR selected.payload->>'source_kind' IS DISTINCT FROM p_source_kind))
       OR selected.actor_user_id<>s.actor_user_id
       OR selected.org_id IS DISTINCT FROM s.org_id OR owner.user_id<>s.actor_user_id
       OR owner.org_id IS DISTINCT FROM s.org_id OR conv.user_id<>s.actor_user_id
       OR conv.org_id IS DISTINCT FROM s.org_id OR conv.scope_type IS DISTINCT FROM 'user'
       OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
       OR s.actor_user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR s.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF selected.status IN ('queued','applied') THEN
        RETURN jsonb_build_object('outcome','already_queued','task_id',selected.continuation_task_id);
    END IF;
    IF selected.status<>'pending' THEN RETURN jsonb_build_object('outcome','obsolete'); END IF;
    IF s.revision IS DISTINCT FROM p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
    IF owner.status IS DISTINCT FROM 'completed'
       OR (p_source_kind='image' AND s.state->>'status' IS DISTINCT FROM 'waiting_dependencies')
       OR (p_source_kind IN ('scheduled','video') AND s.state->>'status' NOT IN ('waiting_approval','waiting_dependencies')) THEN
        RETURN jsonb_build_object('outcome','waiting');
    END IF;
    IF EXISTS(SELECT 1 FROM public.agent_task_events e WHERE e.root_task_id=p_root_task_id AND e.status='queued') THEN
        RETURN jsonb_build_object('outcome','waiting'); END IF;
    IF p_source_kind='scheduled' THEN
        IF public.agent_task_scheduled_terminal_fact(p_root_task_id,selected.payload->>'operation_id') IS NULL THEN
            RETURN jsonb_build_object('outcome','waiting'); END IF;
    END IF;
    IF p_source_kind='video' THEN
        IF public.agent_task_video_terminal_fact(p_root_task_id,selected.payload->>'operation_id') IS NULL THEN
            RETURN jsonb_build_object('outcome','waiting'); END IF;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=s.actor_user_id AND status::text='active')
       OR (s.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
           WHERE m.user_id=s.actor_user_id AND m.org_id=s.org_id AND m.status='active' AND o.status='active')) THEN
        RETURN jsonb_build_object('outcome','scope_changed');
    END IF;
    -- New input takes precedence. Preserve the old event; never reinterpret it
    -- as authorization for a new goal or append stale work over a new turn.
    IF EXISTS(SELECT 1 FROM public.tasks later WHERE later.conversation_id=s.conversation_id
       AND later.type='chat' AND later.queue_sequence>GREATEST(owner.queue_sequence,COALESCE((owner.delivery_context->>'agent_resume_sequence')::bigint,owner.queue_sequence))
       AND later.input_message_id<>owner.input_message_id
       AND (later.delivery_context->>'input_kind') IS DISTINCT FROM 'agent_task_continuation')
       OR EXISTS(SELECT 1 FROM public.messages newer JOIN public.messages source ON source.id=owner.input_message_id
         WHERE newer.conversation_id=s.conversation_id AND newer.role='user'
           AND NOT (s.state->'source_message_ids' @> jsonb_build_array(newer.id::text)) AND newer.created_at>GREATEST(source.created_at,COALESCE((owner.delivery_context->>'agent_resume_at')::timestamptz,source.created_at))) THEN
        RETURN jsonb_build_object('outcome','new_input_pending');
    END IF;
    IF p_source_kind='image' AND EXISTS(SELECT 1 FROM public.agent_task_events e JOIN public.tasks child ON child.id=e.child_task_id
        WHERE e.root_task_id=p_root_task_id AND e.status='pending'
          AND (child.status NOT IN ('completed','failed','cancelled')
               OR (child.status='completed' AND child.request_params#>>'{_media_lifecycle_v1,phase}' IS DISTINCT FROM 'published'))) THEN
        RETURN jsonb_build_object('outcome','waiting');
    END IF;
    IF EXISTS(SELECT 1 FROM public.agent_task_events e WHERE e.root_task_id=p_root_task_id AND e.status='pending'
        AND e.payload->'requirements_revision' IS DISTINCT FROM s.state->'requirements_revision') THEN
        RETURN jsonb_build_object('outcome','requirements_changed');
    END IF;
    params := COALESCE(owner.request_params,'{}'::jsonb)-ARRAY['_image_sources_v1','_agent_task_scheduled_v1','_agent_task_video_wait_v1'];
    INSERT INTO public.messages(id,conversation_id,org_id,role,content,status,turn_id,reply_to_message_id)
      VALUES(v_output_id,s.conversation_id,s.org_id,'assistant','[]','pending',owner.turn_id,owner.input_message_id);
    INSERT INTO public.tasks(id,user_id,org_id,conversation_id,type,status,model_id,assistant_message_id,
        input_message_id,turn_id,execution_mode,request_params,delivery_context)
      VALUES(v_task_id,s.actor_user_id,s.org_id,s.conversation_id,'chat','pending',owner.model_id,v_output_id,
        owner.input_message_id,owner.turn_id,owner.execution_mode,params,
        (COALESCE(owner.delivery_context,'{}'::jsonb)-'source_kind')||jsonb_build_object('actor',true,
          'input_kind','agent_task_continuation','logical_task_id',p_root_task_id,'event_id',p_event_id,
          'source_revision',s.revision,'source_owner_task_id',owner.id)
          ||CASE WHEN p_source_kind IN ('scheduled','video') THEN jsonb_build_object('source_kind',p_source_kind) ELSE '{}'::jsonb END);
    INSERT INTO public.task_attachment_refs(org_id,task_id,turn_id,input_message_id,attachment_id,attachment_set_id)
      SELECT r.org_id,v_task_id,r.turn_id,r.input_message_id,r.attachment_id,r.attachment_set_id
        FROM public.task_attachment_refs r WHERE r.task_id=owner.id;
    UPDATE public.agent_task_events SET status='queued',continuation_task_id=v_task_id,expected_revision=s.revision
      WHERE id=p_event_id AND root_task_id=p_root_task_id AND status='pending' AND p_source_kind IN ('scheduled','video');
    UPDATE public.agent_task_events SET status='queued',continuation_task_id=v_task_id,expected_revision=s.revision
      WHERE root_task_id=p_root_task_id AND status='pending' AND child_task_id IS NOT NULL AND p_source_kind='image';
    RETURN jsonb_build_object('outcome','queued','task_id',v_task_id,'message_id',v_output_id);
END $$;
REVOKE ALL ON FUNCTION public.enqueue_agent_task_continuation(uuid,bigint,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_agent_task_continuation(uuid,bigint,uuid,text) TO everydayai;

RESET ROLE;
