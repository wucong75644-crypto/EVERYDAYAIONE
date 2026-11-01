-- Read-only view for the authenticated original assistant message. No dispatch.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_task_progress(p_root_task_id uuid,p_message_id uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
   RETURN jsonb_build_object('outcome','not_found');
 END IF;
 SELECT * INTO s FROM public.agent_task_states
   WHERE root_task_id=p_root_task_id
     AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
     AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid;
 IF NOT FOUND OR s.state#>>'{execution_identity,context_scope}' IS DISTINCT FROM 'user'
   OR s.state#>>'{execution_identity,workspace_owner_id}' IS DISTINCT FROM s.actor_user_id::text
   OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=s.actor_user_id AND status::text='active')
   OR NOT EXISTS(SELECT 1 FROM public.conversations WHERE id=s.conversation_id AND user_id=s.actor_user_id
       AND org_id IS NOT DISTINCT FROM s.org_id)
   OR (s.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
       WHERE m.org_id=s.org_id AND m.user_id=s.actor_user_id AND m.status='active' AND o.status='active'))
   OR NOT EXISTS(SELECT 1 FROM public.tasks t JOIN public.messages m ON m.id=t.assistant_message_id
       WHERE m.id=p_message_id AND m.role='assistant' AND m.conversation_id=s.conversation_id
         AND t.type='chat' AND t.conversation_id=s.conversation_id AND t.user_id=s.actor_user_id
         AND t.org_id IS NOT DISTINCT FROM s.org_id
         AND (t.id=s.root_task_id OR t.id=s.owner_task_id
           OR t.delivery_context->>'logical_task_id'=s.root_task_id::text)) THEN
   RETURN jsonb_build_object('outcome','not_found');
 END IF;
 RETURN jsonb_build_object('outcome','found','state',s.state);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_progress(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_progress(uuid,uuid) TO everydayai;
RESET ROLE;
