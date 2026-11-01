-- Extend existing Actor controls to the gaps between its execution windows.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.mark_agent_task_owner() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
    UPDATE public.tasks SET request_params=COALESCE(request_params,'{}'::jsonb)||jsonb_build_object('_agent_task_root',NEW.root_task_id)
      WHERE id=NEW.owner_task_id AND request_params->>'_agent_task_root' IS DISTINCT FROM NEW.root_task_id::text;
    RETURN NEW;
END $$;
CREATE TRIGGER agent_task_owner_marker AFTER INSERT OR UPDATE OF owner_task_id ON public.agent_task_states
    FOR EACH ROW EXECUTE FUNCTION public.mark_agent_task_owner();

CREATE FUNCTION public.transition_agent_task_control(p_root uuid,p_action text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; cp jsonb; next_state jsonb; target text;
BEGIN
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root FOR UPDATE;
    IF NOT FOUND OR s.actor_user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR s.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
       OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
        RAISE EXCEPTION 'TASK_CONTROL_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id FOR UPDATE;
    IF cp->'_task_execution' IS DISTINCT FROM s.state THEN RAISE EXCEPTION 'TASK_CONTROL_CHECKPOINT_MISSING'; END IF;
    IF s.state->>'status'='cancelled' THEN RETURN jsonb_build_object('outcome','already_cancelled'); END IF;
    IF s.state->>'status' IN ('completed','failed') THEN RETURN jsonb_build_object('outcome','terminal'); END IF;
    IF p_action='pause' THEN
        IF s.state->>'status'='paused' THEN RETURN jsonb_build_object('outcome','already_paused'); END IF;
        cp:=cp||jsonb_build_object('_agent_task_resume_status',s.state->>'status'); target:='paused';
    ELSIF p_action='cancel' THEN target:='cancelled'; cp:=cp-'_agent_task_resume_status';
    ELSIF p_action='resume' THEN
        IF s.state->>'status'<>'paused' THEN RETURN jsonb_build_object('outcome','already_pending'); END IF;
        target:=cp->>'_agent_task_resume_status';
        IF target NOT IN ('active','waiting_dependencies','waiting_input','waiting_approval','partial') OR target IS NULL THEN
            RAISE EXCEPTION 'TASK_CONTROL_RESUME_STATE_MISSING';
        END IF;
        cp:=cp-'_agent_task_resume_status';
    ELSE RAISE EXCEPTION 'TASK_CONTROL_ACTION_INVALID'; END IF;
    next_state:=jsonb_set(jsonb_set(s.state,'{status}',to_jsonb(target)),'{revision}',to_jsonb(s.revision+1));
    UPDATE public.agent_task_states SET state=next_state,revision=s.revision+1,updated_at=now() WHERE root_task_id=p_root;
    UPDATE public.conversation_turn_checkpoints SET state=jsonb_set(cp,'{_task_execution}',next_state),version=version+1,updated_at=now()
        WHERE task_id=s.owner_task_id;
    IF target='cancelled' THEN
        UPDATE public.agent_task_events SET status='obsolete' WHERE root_task_id=p_root AND status IN ('pending','queued');
    END IF;
    RETURN jsonb_build_object('outcome',CASE p_action WHEN 'resume' THEN 'enqueued' ELSE target END,'revision',s.revision+1);
END $$;

CREATE FUNCTION public.sync_agent_task_owner_control() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE root uuid;
BEGIN
    IF NEW.type<>'chat' OR NEW.status IS NOT DISTINCT FROM OLD.status
       OR NOT (NEW.status IN ('paused','cancelled') OR (OLD.status='paused' AND NEW.status='pending')) THEN RETURN NEW; END IF;
    SELECT root_task_id INTO root FROM public.agent_task_states WHERE owner_task_id=NEW.id;
    IF root IS NOT NULL THEN
        PERFORM public.transition_agent_task_control(root,CASE NEW.status WHEN 'paused' THEN 'pause' WHEN 'cancelled' THEN 'cancel' ELSE 'resume' END);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER agent_task_owner_control AFTER UPDATE OF status ON public.tasks
    FOR EACH ROW EXECUTE FUNCTION public.sync_agent_task_owner_control();

CREATE FUNCTION public.control_agent_task(p_root_task_id uuid,p_user_id uuid,p_org_id uuid,p_action text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; owner public.tasks%ROWTYPE; queued public.tasks%ROWTYPE; v_result jsonb;
BEGIN
    IF p_action NOT IN ('pause','cancel','resume') OR p_user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR p_org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
       OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
        RAISE EXCEPTION 'TASK_CONTROL_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id AND actor_user_id=p_user_id AND org_id IS NOT DISTINCT FROM p_org_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'TASK_CONTROL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
    PERFORM 1 FROM public.conversations WHERE id=s.conversation_id AND user_id=p_user_id AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=p_user_id AND status::text='active')
       OR (p_org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
           WHERE m.user_id=p_user_id AND m.org_id=p_org_id AND m.status='active' AND o.status='active')) THEN
        RAISE EXCEPTION 'TASK_CONTROL_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO owner FROM public.tasks WHERE id=s.owner_task_id FOR UPDATE;
    SELECT task.* INTO queued FROM public.agent_task_events e JOIN public.tasks task ON task.id=e.continuation_task_id
       WHERE e.root_task_id=p_root_task_id AND e.status='queued' ORDER BY task.queue_sequence LIMIT 1 FOR UPDATE OF task;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id FOR UPDATE;
    IF s.owner_task_id<>owner.id THEN RETURN jsonb_build_object('outcome','retry'); END IF;
    IF p_action='resume' AND s.state->>'status'='paused' THEN
        UPDATE public.tasks SET delivery_context=delivery_context||jsonb_build_object('agent_resume_at',clock_timestamp(),
            'agent_resume_sequence',(SELECT COALESCE(max(queue_sequence),0) FROM public.tasks WHERE conversation_id=s.conversation_id)) WHERE id=owner.id;
    END IF;
    -- Existing active/paused Actor windows keep their original snapshot/fence protocol.
    IF owner.status IN ('running','pending','paused') THEN
        RETURN jsonb_build_object('outcome','delegate','task',to_jsonb(owner));
    END IF;
    IF s.state->>'status' NOT IN ('waiting_dependencies','waiting_approval','waiting_input','paused','cancelled') THEN RETURN jsonb_build_object('outcome','terminal'); END IF;
    v_result:=public.transition_agent_task_control(p_root_task_id,p_action);
    IF p_action='resume' AND v_result->>'outcome'='enqueued' THEN
        -- Explicit resume authorizes the old goal after all input already present.
        UPDATE public.tasks SET delivery_context=delivery_context||jsonb_build_object('agent_resume_at',clock_timestamp(),
            'agent_resume_sequence',(SELECT COALESCE(max(queue_sequence),0) FROM public.tasks WHERE conversation_id=s.conversation_id)) WHERE id=owner.id;
        IF queued.id IS NOT NULL THEN
            UPDATE public.agent_task_events SET expected_revision=(v_result->>'revision')::bigint WHERE continuation_task_id=queued.id AND status='queued';
            UPDATE public.tasks SET status='pending',execution_token=NULL,lease_expires_at=NULL,terminal_reason='resume_requested',error_message=NULL,
                delivery_context=delivery_context||jsonb_build_object('source_revision',(v_result->>'revision')::bigint)
              WHERE id=queued.id;
        END IF;
    ELSIF p_action IN ('pause','cancel') AND queued.id IS NOT NULL AND v_result->>'outcome' IN ('paused','cancelled') THEN
        -- Ownership has not transferred: this window cannot have dispatched model/tools.
        UPDATE public.tasks SET status=CASE p_action WHEN 'pause' THEN 'paused' ELSE 'cancelled' END,
            execution_token=NULL,lease_expires_at=NULL,terminal_reason=CASE p_action WHEN 'pause' THEN 'user_paused' ELSE 'user_cancelled' END WHERE id=queued.id;
        IF p_action='cancel' THEN UPDATE public.messages SET status='interrupted' WHERE id=queued.assistant_message_id AND context_revision IS NULL; END IF;
        UPDATE public.conversations SET active_serial_task_id=NULL WHERE id=s.conversation_id AND active_serial_task_id=queued.id;
    END IF;
    RETURN v_result||jsonb_build_object('logical_task_id',p_root_task_id,'task_id',COALESCE(queued.id,owner.id),
        'conversation_id',s.conversation_id,'assistant_message_id',COALESCE(queued.assistant_message_id,owner.assistant_message_id),
        'client_task_id',CASE WHEN queued.id IS NOT NULL THEN queued.client_task_id ELSE owner.client_task_id END,
        'external_task_id',CASE WHEN queued.id IS NOT NULL THEN queued.external_task_id ELSE owner.external_task_id END,
        'task_slot_id',CASE WHEN queued.id IS NOT NULL THEN queued.request_params->>'_task_slot_id' ELSE owner.request_params->>'_task_slot_id' END);
END $$;
REVOKE ALL ON FUNCTION public.mark_agent_task_owner(),public.sync_agent_task_owner_control(),
    public.transition_agent_task_control(uuid,text),public.control_agent_task(uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.transition_agent_task_control(uuid,text),public.control_agent_task(uuid,uuid,uuid,text) TO everydayai;
RESET ROLE;
