-- Video facts enter the SAME internal Actor queue, never a synthetic user turn.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.agent_task_video_terminal_fact(p_root_task_id uuid,p_operation_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; op jsonb; native public.tool_invocations%ROWTYPE; receipt public.agent_task_native_writes%ROWTYPE; child public.tasks%ROWTYPE;
BEGIN
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id;
 IF s.root_task_id IS NULL OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
   OR s.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   OR s.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=s.actor_user_id AND status::text='active')
   OR (s.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations org ON org.id=m.org_id
     WHERE m.org_id=s.org_id AND m.user_id=s.actor_user_id AND m.status='active' AND org.status='active')) THEN RETURN NULL; END IF;
 SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=p_operation_id AND value->>'tool_name'='generate_video';
 SELECT * INTO receipt FROM public.agent_task_native_writes WHERE operation_id=p_operation_id AND domain='video';
 SELECT i.* INTO native FROM public.tool_invocations i JOIN public.tasks owner ON owner.id=i.task_id
 WHERE i.id=receipt.native_invocation_id AND i.task_id=(op->>'owner_task_id')::uuid AND i.turn_id=(op->>'owner_turn_id')::uuid
   AND i.tool_call_id=op->>'invocation_id' AND i.args_hash=op->>'arguments_sha256' AND i.tool_name='generate_video'
   AND i.conversation_id=s.conversation_id AND owner.user_id=s.actor_user_id AND owner.org_id IS NOT DISTINCT FROM s.org_id
   AND (owner.id=s.root_task_id OR owner.delivery_context->>'logical_task_id'=s.root_task_id::text);
 IF native.id IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO child FROM public.tasks WHERE id=(receipt.intent->>'child_id')::uuid AND type='video'
   AND user_id=s.actor_user_id AND org_id IS NOT DISTINCT FROM s.org_id AND conversation_id=s.conversation_id
   AND request_params#>>'{_agent_video_request_v1,request_hash}'=receipt.intent->>'request_hash'
   AND request_params#>>'{_agent_video_request_v1,origin,parent_task_id}'=native.task_id::text
   AND request_params#>>'{_agent_video_lifecycle_v1,phase}'='published' AND status IN ('completed','failed','cancelled');
 IF child.id IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('child_task_id',child.id,'status',child.status,'content',child.result_data,
   'output',child.request_params->'_agent_video_output_v1','error',child.error_message);
END $$;
REVOKE ALL ON FUNCTION public.agent_task_video_terminal_fact(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_video_terminal_fact(uuid,text) TO everydayai;
CREATE FUNCTION public.mark_agent_task_video_wait() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NEW.state->>'image_binding_version'='3' AND NEW.state->>'status' IN ('waiting_approval','waiting_dependencies')
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'operations') op
       WHERE op->>'tool_name'='generate_video' AND op->>'status' IN ('accepted','uncertain','reserved')) THEN
   UPDATE public.tasks SET request_params=COALESCE(request_params,'{}'::jsonb)||'{"_agent_task_video_wait_v1":{"pending":true}}'::jsonb
     WHERE id=NEW.owner_task_id AND type='chat' AND user_id=NEW.actor_user_id AND org_id IS NOT DISTINCT FROM NEW.org_id;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.mark_agent_task_video_wait() FROM PUBLIC;
CREATE TRIGGER agent_task_video_wait AFTER INSERT OR UPDATE OF state ON public.agent_task_states
 FOR EACH ROW EXECUTE FUNCTION public.mark_agent_task_video_wait();
CREATE INDEX agent_task_video_scan ON public.tasks(id) WHERE type='chat'
 AND request_params#>'{_agent_task_video_wait_v1,pending}'='true'::jsonb;
CREATE FUNCTION public.list_agent_task_video_hints(p_after uuid DEFAULT NULL,p_limit integer DEFAULT 100)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(to_jsonb(item) ORDER BY item.id),'[]'::jsonb) FROM (
   SELECT id,user_id,org_id FROM public.tasks WHERE type='chat' AND status='completed'
     AND request_params#>'{_agent_task_video_wait_v1,pending}'='true'::jsonb
     AND (p_after IS NULL OR id>p_after) ORDER BY id LIMIT LEAST(GREATEST(p_limit,1),100)) item
$$;
REVOKE ALL ON FUNCTION public.list_agent_task_video_hints(uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.list_agent_task_video_hints(uuid,integer) TO everydayai;

CREATE FUNCTION public.refresh_agent_task_video_events(p_source_task_id uuid)
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
   UPDATE public.tasks SET request_params=request_params-'_agent_task_video_wait_v1' WHERE id=p_source_task_id;
   RETURN jsonb_build_object('outcome','obsolete'); END IF;
 IF owner.status IS DISTINCT FROM 'completed' OR s.state->>'status' NOT IN ('waiting_approval','waiting_dependencies') THEN
   RETURN jsonb_build_object('outcome','waiting'); END IF;
 FOR op IN SELECT v FROM jsonb_array_elements(s.state->'operations') v
   WHERE v->>'tool_name'='generate_video' AND v->>'status' IN ('accepted','reserved','uncertain') LOOP
   fact:=public.agent_task_video_terminal_fact(s.root_task_id,op->>'id');
   IF fact IS NULL THEN CONTINUE; END IF;
   dedupe:='video-receipt:'||(op->>'id')||':'||(s.state->>'requirements_revision')||':'||(fact->>'child_task_id')||':'||(fact->>'status');
   INSERT INTO public.agent_task_events(root_task_id,actor_user_id,org_id,dedupe_key,expected_revision,payload)
   VALUES(s.root_task_id,s.actor_user_id,s.org_id,dedupe,s.revision,
     jsonb_build_object('source_kind','video','operation_id',op->>'id','requirements_revision',s.state->'requirements_revision'))
   ON CONFLICT(root_task_id,dedupe_key) DO NOTHING;
   SELECT id INTO event FROM public.agent_task_events WHERE root_task_id=s.root_task_id
     AND dedupe_key=dedupe AND status='pending';
   IF event IS NOT NULL THEN
     queued:=public.enqueue_agent_task_continuation(s.root_task_id,s.revision,event,'video');
     IF queued->>'outcome' IN ('queued','already_queued') THEN
       UPDATE public.tasks SET request_params=request_params-'_agent_task_video_wait_v1' WHERE id=p_source_task_id;
       RETURN queued; END IF;
   END IF;
 END LOOP;
 RETURN jsonb_build_object('outcome','waiting');
END $$;
REVOKE ALL ON FUNCTION public.refresh_agent_task_video_events(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refresh_agent_task_video_events(uuid) TO everydayai;

RESET ROLE;
