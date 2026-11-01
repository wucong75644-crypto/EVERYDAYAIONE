-- Separate optional work-plan presentation from native execution receipts.
-- Read-only view for the authenticated original assistant message. No dispatch.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.read_agent_task_progress(p_root_task_id uuid,p_message_id uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; plan_task public.tasks%ROWTYPE; cp public.conversation_turn_checkpoints%ROWTYPE;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
   RETURN jsonb_build_object('outcome','not_found');
 END IF;
 SELECT * INTO s FROM public.agent_task_states
   WHERE root_task_id=p_root_task_id
     AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
     AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid;
 IF NOT FOUND THEN
   SELECT * INTO plan_task FROM public.tasks WHERE id=p_root_task_id AND type='chat'
     AND user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
     AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid;
   IF NOT FOUND OR plan_task.assistant_message_id IS DISTINCT FROM p_message_id
     OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=plan_task.user_id AND status::text='active')
     OR NOT EXISTS(SELECT 1 FROM public.conversations WHERE id=plan_task.conversation_id AND user_id=plan_task.user_id
          AND org_id IS NOT DISTINCT FROM plan_task.org_id)
     OR NOT EXISTS(SELECT 1 FROM public.messages WHERE id=p_message_id AND role='assistant' AND conversation_id=plan_task.conversation_id)
     OR (plan_task.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
          WHERE m.org_id=plan_task.org_id AND m.user_id=plan_task.user_id AND m.status='active' AND o.status='active')) THEN
     RETURN jsonb_build_object('outcome','not_found');
   END IF;
   SELECT * INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=plan_task.id
      AND conversation_id=plan_task.conversation_id AND turn_id=plan_task.turn_id AND status<>'invalid';
   IF NOT FOUND OR cp.state ? '_task_execution'
      OR cp.state->>'_task_direct_plan_owner' IS DISTINCT FROM plan_task.id::text
      OR jsonb_typeof(cp.state->'_task_direct_plan') IS DISTINCT FROM 'object' THEN
     RETURN jsonb_build_object('outcome','not_found');
   END IF;
   RETURN jsonb_build_object('outcome','found','plan_only',true,
       'root_task_id',plan_task.id,'actor_user_id',plan_task.user_id,'org_id',plan_task.org_id,
       'task_status',plan_task.status,'checkpoint_status',cp.status,'revision',cp.version,
       'working_plan',cp.state->'_task_direct_plan');
 END IF;
 IF s.state#>>'{execution_identity,context_scope}' IS DISTINCT FROM 'user'
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
