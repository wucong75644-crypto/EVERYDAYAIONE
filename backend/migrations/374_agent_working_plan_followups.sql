-- Discover editable plans from completed chat checkpoints without adopting an
-- execution ledger. The current serial Actor lease fences subsequent saves.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_working_plan_candidates(p_task_id uuid,p_execution_token uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE current_task public.tasks%ROWTYPE; candidates jsonb;
BEGIN
 SELECT * INTO current_task FROM public.tasks WHERE id=p_task_id;
 IF SESSION_USER<>'everydayai' OR current_task.id IS NULL
    OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
    OR current_task.user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
    OR current_task.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
    OR current_task.type IS DISTINCT FROM 'chat' OR current_task.status IS DISTINCT FROM 'running'
    OR (current_task.delivery_context @> '{"actor":true}') IS NOT TRUE
    OR current_task.execution_token IS DISTINCT FROM p_execution_token
    OR p_execution_token IS NULL OR current_task.lease_expires_at IS NULL OR current_task.lease_expires_at<=now()
    OR current_task.execution_mode IS DISTINCT FROM 'serial'
    OR NOT EXISTS(SELECT 1 FROM public.conversations c WHERE c.id=current_task.conversation_id
        AND c.scope_type='user' AND c.user_id=current_task.user_id AND c.org_id IS NOT DISTINCT FROM current_task.org_id)
    OR NOT EXISTS(SELECT 1 FROM public.messages m WHERE m.id=current_task.input_message_id AND m.role='user'
        AND m.conversation_id=current_task.conversation_id AND m.turn_id=current_task.turn_id) THEN
   RETURN jsonb_build_object('outcome','not_found');
 END IF;

 WITH versions AS (
   SELECT COALESCE(cp.state->>'_task_direct_plan_id', prior.id::text) AS plan_id,
          prior.id AS source_id, prior.assistant_message_id, prior.queue_sequence, cp.version
   FROM public.tasks prior
   JOIN public.conversation_turn_checkpoints cp ON cp.task_id=prior.id
     AND cp.turn_id=prior.turn_id AND cp.conversation_id=prior.conversation_id
   JOIN public.messages message ON message.id=prior.assistant_message_id
     AND message.conversation_id=prior.conversation_id AND message.role='assistant'
   WHERE prior.conversation_id=current_task.conversation_id AND prior.user_id=current_task.user_id
     AND prior.org_id IS NOT DISTINCT FROM current_task.org_id
     AND prior.type='chat' AND prior.execution_mode='serial' AND prior.status='completed'
     AND prior.queue_sequence<current_task.queue_sequence AND prior.input_message_id<>current_task.input_message_id
     AND cp.status<>'invalid' AND cp.state->>'_task_direct_plan_owner'=prior.id::text
     AND jsonb_typeof(cp.state->'_task_direct_plan')='object'
 ), latest AS (
   SELECT DISTINCT ON (v.plan_id) v.* FROM versions v
   JOIN public.tasks origin ON origin.id::text=v.plan_id AND origin.type='chat'
     AND origin.user_id=current_task.user_id AND origin.conversation_id=current_task.conversation_id
     AND origin.org_id IS NOT DISTINCT FROM current_task.org_id AND origin.queue_sequence<=v.queue_sequence
   ORDER BY v.plan_id,v.queue_sequence DESC
 )
 SELECT COALESCE(jsonb_agg(jsonb_build_object('plan_id',latest.plan_id,'version',latest.source_id::text || ':' || latest.version,
          'working_plan',progress.view->'working_plan') ORDER BY latest.queue_sequence DESC),'[]'::jsonb)
 INTO candidates
 FROM latest
 CROSS JOIN LATERAL (SELECT public.read_agent_task_progress(latest.source_id,latest.assistant_message_id) AS view) progress
 WHERE progress.view @> '{"outcome":"found","plan_only":true}'
   AND progress.view#>'{working_plan,steps}' <> '[]'::jsonb;

 RETURN jsonb_build_object('outcome','found','candidates',candidates);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_working_plan_candidates(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_working_plan_candidates(uuid,uuid) TO everydayai;
RESET ROLE;
