/* 228.08e2: keep ModelLoop video Action and parent Run projections distinct. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
        '_prepare_agent_runtime_model_video_fenced_v1(jsonb,text)'
    ) IS NULL OR to_regprocedure(
        '_project_agent_runtime_media_wecom_delivery_v1()'
    ) IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_228_08E1_08B_REQUIRED'
            USING ERRCODE='55000';
    END IF;
    IF to_regprocedure(
        '_agent_runtime_prepared_media_source_v1(uuid)'
    ) IS NOT NULL OR to_regprocedure(
        '_agent_runtime_media_prepared_action_projection_228_06_v1(agent_runtime_events,jsonb)'
    ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_228_08E2_IDENTITY_CONFLICT'
            USING ERRCODE='55000';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid='agent_runtime_prepared_media_action_bindings'::regclass
           AND relrowsecurity AND relforcerowsecurity
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BINDING_RLS_REQUIRED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE FUNCTION _agent_runtime_prepared_media_source_v1(p_action_id UUID)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
    SELECT CASE
        WHEN action.tool_name='generate_video'
         AND COALESCE(action.policy_snapshot->>'source','model_loop')<>'media_ingress'
        THEN 'model_loop' ELSE 'media_ingress' END
      FROM agent_actions action WHERE action.id=p_action_id
$$;

CREATE FUNCTION _agent_runtime_media_model_video_action_projection_v1(
    p_event agent_runtime_events,p_content_part JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    action agent_actions%ROWTYPE;
    binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
    child_task tasks%ROWTYPE;
    output_message messages%ROWTYPE;
    facts JSONB;
    provider_ref TEXT;
    error_code TEXT;
    slot_status TEXT;
    content_part JSONB;
    message_content JSONB;
    slot JSONB;
    slot_ordinal INTEGER;
    refund JSONB;
BEGIN
    SELECT * INTO action FROM agent_actions WHERE id=p_event.action_id;
    SELECT * INTO binding FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=p_event.action_id;
    IF binding.action_id IS NULL OR binding.media_kind<>'video'
       OR _agent_runtime_prepared_media_source_v1(binding.action_id)<>'model_loop'
       OR action.session_id IS DISTINCT FROM p_event.session_id
       OR action.run_id IS DISTINCT FROM p_event.run_id
       OR binding.session_id IS DISTINCT FROM action.session_id
       OR binding.run_id IS DISTINCT FROM action.run_id
       OR binding.org_id IS DISTINCT FROM action.org_id
       OR binding.user_id IS DISTINCT FROM action.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_PROJECTION_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO child_task FROM tasks WHERE id=binding.task_id FOR UPDATE;
    SELECT * INTO binding FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=p_event.action_id FOR UPDATE;
    SELECT * INTO output_message FROM messages
     WHERE id=binding.output_message_id FOR UPDATE;
    IF child_task.id IS NULL OR output_message.id IS NULL
       OR child_task.user_id IS DISTINCT FROM binding.user_id
       OR child_task.org_id IS DISTINCT FROM binding.org_id
       OR child_task.conversation_id IS DISTINCT FROM binding.conversation_id
       OR child_task.assistant_message_id IS DISTINCT FROM binding.output_message_id
       OR child_task.credit_transaction_id IS DISTINCT FROM binding.credit_transaction_id
       OR child_task.delivery_context @> jsonb_build_object(
            'actor',FALSE,'runtime',TRUE,'runtime_action_id',action.id::TEXT
          ) IS NOT TRUE OR output_message.role::TEXT<>'assistant' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_PROJECTION_TASK_INVALID'
            USING ERRCODE='42501';
    END IF;
    facts:=_agent_runtime_media_action_facts_v1(p_event);
    provider_ref:=NULLIF(btrim((facts->'provider')->>'provider_task_ref'),'');
    IF p_event.event_type='action.accepted' AND provider_ref IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_FACT_REQUIRED'
            USING ERRCODE='55000';
    END IF;
    slot_status:=CASE p_event.event_type
        WHEN 'action.requested' THEN 'pending'
        WHEN 'action.accepted' THEN 'accepted'
        WHEN 'action.unknown' THEN 'unknown'
        WHEN 'action.completed' THEN 'completed'
        WHEN 'action.cancelled' THEN 'cancelled'
        ELSE 'failed' END;
    IF p_event.event_type='action.completed' THEN
        IF (facts->'result'->>'action_id') IS NULL
           OR jsonb_array_length(COALESCE(facts->'result_urls','[]'::JSONB))<>1
           OR jsonb_typeof(p_content_part) IS DISTINCT FROM 'object'
           OR NULLIF(btrim(p_content_part->>'url'),'') IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(facts->'result_urls') url
                WHERE url=p_content_part->>'source_url'
           ) THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_AUTHORITATIVE_RESULT_REQUIRED'
                USING ERRCODE='55000';
        END IF;
        content_part:=p_content_part||jsonb_build_object('type','video');
        UPDATE tasks SET status='completed',credits_locked=0,
            credits_used=binding.unit_credits,
            external_task_id=COALESCE(provider_ref,external_task_id),
            result=jsonb_build_object('video_url',content_part->>'url',
                'content_parts',jsonb_build_array(content_part)),
            error_message=NULL,
            completed_at=COALESCE(action.completed_at,clock_timestamp())
         WHERE id=child_task.id;
        UPDATE credit_transactions SET status='confirmed',
            confirmed_at=clock_timestamp()
         WHERE id=binding.credit_transaction_id AND status='pending';
        IF NOT FOUND AND binding.credit_state='pending' THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CREDIT_TRANSACTION_CONFLICT'
                USING ERRCODE='55000';
        END IF;
        UPDATE agent_runtime_prepared_media_action_bindings
           SET credit_state='confirmed',projection_revision=p_event.sequence,
               state_version=state_version+1,updated_at=clock_timestamp()
         WHERE action_id=p_event.action_id;
    ELSIF p_event.event_type IN (
        'action.failed','action.rejected','action.cancelled'
    ) THEN
        error_code:=COALESCE(facts->'result'->>'error_code',action.terminal_reason,
            CASE WHEN p_event.event_type='action.cancelled'
                 THEN 'action_cancelled' ELSE 'action_failed' END);
        IF binding.credit_state='pending' THEN
            SELECT atomic_refund_credits(binding.credit_transaction_id) INTO refund;
            IF COALESCE((refund->>'refunded')::BOOLEAN,FALSE) IS NOT TRUE THEN
                RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CREDIT_REFUND_CONFLICT'
                    USING ERRCODE='55000';
            END IF;
        END IF;
        content_part:=jsonb_build_object('type','video','url',NULL,'failed',TRUE,
            'error_code',error_code,'error',error_code);
        UPDATE tasks SET status=CASE WHEN p_event.event_type='action.cancelled'
                THEN 'cancelled' ELSE 'failed' END,
            credits_locked=0,credits_used=0,
            external_task_id=COALESCE(provider_ref,external_task_id),
            result=content_part,error_message=error_code,
            completed_at=COALESCE(action.completed_at,clock_timestamp())
         WHERE id=child_task.id;
        UPDATE agent_runtime_prepared_media_action_bindings
           SET credit_state='refunded',projection_revision=p_event.sequence,
               state_version=state_version+1,updated_at=clock_timestamp()
         WHERE action_id=p_event.action_id;
    ELSE
        UPDATE tasks SET status=CASE WHEN p_event.event_type='action.requested'
                THEN 'pending' ELSE 'running' END,
            credits_locked=binding.unit_credits,credits_used=0,
            external_task_id=COALESCE(provider_ref,external_task_id),
            started_at=COALESCE(started_at,clock_timestamp())
         WHERE id=child_task.id;
        UPDATE agent_runtime_prepared_media_action_bindings
           SET projection_revision=p_event.sequence,state_version=state_version+1,
               updated_at=clock_timestamp()
         WHERE action_id=p_event.action_id;
    END IF;
    message_content:=output_message.content::JSONB;
    SELECT part,ordinality::INTEGER INTO slot,slot_ordinal
      FROM jsonb_array_elements(message_content)
           WITH ORDINALITY source(part,ordinality)
     WHERE part->>'slot_id'=binding.action_id::TEXT
       AND (part->>'slot_index')::INTEGER=0;
    IF slot IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_SLOT_NOT_FOUND' USING ERRCODE='55000';
    END IF;
    slot:=COALESCE(content_part,slot)||jsonb_build_object(
        'type','video','slot_id',binding.action_id,'slot_index',0,
        'slot_status',slot_status,'slot_revision',p_event.sequence
    );
    UPDATE messages SET content=jsonb_set(
        message_content,ARRAY[(slot_ordinal-1)::TEXT],slot,FALSE
    )::TEXT WHERE id=output_message.id;
    RETURN jsonb_build_object('projection_action','action_progress',
        'message_id',binding.output_message_id,'task_id',binding.task_id,
        'slot',slot,'content_part',content_part);
END;
$$;

ALTER FUNCTION _agent_runtime_media_prepared_action_projection_v1(
    agent_runtime_events,JSONB
) RENAME TO _agent_runtime_media_prepared_action_projection_228_06_v1;
CREATE FUNCTION _agent_runtime_media_prepared_action_projection_v1(
    p_event agent_runtime_events,p_content_part JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
    IF _agent_runtime_prepared_media_source_v1(p_event.action_id)='model_loop' THEN
        RETURN _agent_runtime_media_model_video_action_projection_v1(
            p_event,p_content_part
        );
    END IF;
    RETURN _agent_runtime_media_prepared_action_projection_228_06_v1(
        p_event,p_content_part
    );
END;
$$;

CREATE FUNCTION _agent_runtime_media_model_video_run_projection_v1(
    p_event agent_runtime_events,p_action TEXT,
    OUT projected_message_id UUID,OUT projected_task_id UUID,
    OUT content_part JSONB
) RETURNS RECORD LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    run agent_runs%ROWTYPE;
    runtime_session agent_runtime_sessions%ROWTYPE;
    command agent_session_commands%ROWTYPE;
    parent_task tasks%ROWTYPE;
    output_message messages%ROWTYPE;
    final_step agent_model_steps%ROWTYPE;
    model_result agent_model_results%ROWTYPE;
    final_part JSONB;
    content JSONB;
    slots JSONB;
    other JSONB;
    child_credits INTEGER;
BEGIN
    SELECT * INTO run FROM agent_runs WHERE id=p_event.run_id;
    SELECT * INTO runtime_session FROM agent_runtime_sessions
     WHERE id=p_event.session_id;
    SELECT * INTO command FROM agent_session_commands WHERE id=run.command_id;
    SELECT * INTO parent_task FROM tasks
     WHERE id=NULLIF(command.payload->>'task_id','')::UUID FOR UPDATE;
    PERFORM action_id FROM agent_runtime_media_action_bindings binding
     WHERE binding.run_id=run.id ORDER BY action_id FOR UPDATE;
    PERFORM action_id FROM agent_runtime_prepared_media_action_bindings binding
     WHERE binding.run_id=run.id ORDER BY action_id FOR UPDATE;
    SELECT * INTO output_message FROM messages
     WHERE id=NULLIF(command.payload->>'output_message_id','')::UUID FOR UPDATE;
    IF run.id IS NULL OR runtime_session.id IS NULL OR command.id IS NULL
       OR parent_task.id IS NULL OR output_message.id IS NULL
       OR run.session_id IS DISTINCT FROM p_event.session_id
       OR run.org_id IS DISTINCT FROM p_event.org_id
       OR run.user_id IS DISTINCT FROM p_event.user_id
       OR parent_task.user_id IS DISTINCT FROM run.user_id
       OR parent_task.org_id IS DISTINCT FROM run.org_id
       OR parent_task.conversation_id IS DISTINCT FROM runtime_session.conversation_id
       OR output_message.id IS DISTINCT FROM parent_task.assistant_message_id
       OR output_message.role::TEXT<>'assistant' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_RUN_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    SELECT COALESCE(sum(task.credits_used),0)::INTEGER INTO child_credits
      FROM tasks task JOIN (
          SELECT binding.task_id
            FROM agent_runtime_media_action_bindings binding
           WHERE binding.output_message_id=output_message.id
          UNION
          SELECT binding.task_id
            FROM agent_runtime_prepared_media_action_bindings binding
           WHERE binding.output_message_id=output_message.id
             AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
      ) child ON child.task_id=task.id;
    IF p_action='run_completed' THEN
        SELECT * INTO final_step FROM agent_model_steps
         WHERE run_id=run.id ORDER BY step_number DESC LIMIT 1;
        SELECT * INTO model_result FROM agent_model_results
         WHERE model_step_id=final_step.id;
        IF run.status<>'completed' OR final_step.id IS NULL
           OR final_step.status<>'completed'
           OR final_step.stop_reason NOT IN ('final','structured_final')
           OR model_result.id IS NULL
           OR model_result.content_hash IS DISTINCT FROM run.result_hash
           OR model_result.content_hash IS DISTINCT FROM encode(digest(
               convert_to(CASE WHEN model_result.output_kind='text'
                   THEN model_result.text_content
                   ELSE model_result.structured_content::TEXT END,'UTF8'),
               'sha256'),'hex') THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MODEL_RESULT_INVALID'
                USING ERRCODE='55000';
        END IF;
        final_part:=CASE WHEN model_result.output_kind='text'
            THEN jsonb_build_object('type','text','text',model_result.text_content)
            ELSE jsonb_build_object('type','data','data',model_result.structured_content)
        END;
        content:=output_message.content::JSONB;
        SELECT COALESCE(jsonb_agg(part ORDER BY ordinality)
                   FILTER (WHERE part->>'slot_id' IS NOT NULL),'[]'::JSONB),
               COALESCE(jsonb_agg(part ORDER BY ordinality)
                   FILTER (WHERE part->>'slot_id' IS NULL),'[]'::JSONB)
          INTO slots,other FROM jsonb_array_elements(content)
               WITH ORDINALITY source(part,ordinality);
        UPDATE messages SET content=(slots||other||jsonb_build_array(final_part))::TEXT,
            status='completed' WHERE id=output_message.id;
        UPDATE tasks SET status='completed',credits_locked=0,
            credits_used=child_credits,
            result=jsonb_build_object('runtime_run_id',run.id,
                'model_result_id',model_result.id,
                'content_hash',model_result.content_hash),
            completed_at=COALESCE(run.completed_at,clock_timestamp())
         WHERE id=parent_task.id;
    ELSE
        UPDATE tasks SET status=CASE p_action
                WHEN 'run_pending' THEN 'pending' WHEN 'run_running' THEN 'running'
                WHEN 'run_waiting' THEN 'running' WHEN 'run_failed' THEN 'failed'
                WHEN 'run_cancelled' THEN 'cancelled' ELSE status END,
            credits_locked=CASE WHEN p_action IN ('run_failed','run_cancelled')
                THEN 0 ELSE credits_locked END,
            credits_used=CASE WHEN p_action IN ('run_failed','run_cancelled')
                THEN child_credits ELSE credits_used END,
            error_message=CASE WHEN p_action='run_failed'
                THEN run.terminal_reason ELSE error_message END,
            completed_at=CASE WHEN p_action IN ('run_failed','run_cancelled')
                THEN COALESCE(run.completed_at,clock_timestamp()) ELSE completed_at END
         WHERE id=parent_task.id;
        IF p_action IN ('run_failed','run_cancelled') THEN
            UPDATE messages SET status='failed' WHERE id=output_message.id;
        END IF;
    END IF;
    projected_message_id:=output_message.id;
    projected_task_id:=parent_task.id;
    content_part:=final_part;
END;
$$;

CREATE FUNCTION _project_agent_runtime_model_video_run_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE event agent_runtime_events%ROWTYPE; expected_action TEXT;
BEGIN
    SELECT * INTO event FROM agent_runtime_events WHERE id=NEW.event_id;
    IF event.action_id IS NOT NULL OR event.run_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
         WHERE binding.run_id=event.run_id
           AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
    ) AND EXISTS (
        SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
         WHERE binding.run_id=event.run_id
           AND _agent_runtime_prepared_media_source_v1(binding.action_id)='media_ingress'
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RUN_OWNER_AMBIGUOUS'
            USING ERRCODE='55000';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
         WHERE binding.run_id=event.run_id
           AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
    ) THEN RETURN NEW; END IF;
    expected_action:=CASE event.event_type
        WHEN 'run.created' THEN 'run_pending' WHEN 'run.claimed' THEN 'run_running'
        WHEN 'run.resumed' THEN 'run_running' WHEN 'run.waiting' THEN 'run_waiting'
        WHEN 'run.completed' THEN 'run_completed' WHEN 'run.failed' THEN 'run_failed'
        WHEN 'run.cancelled' THEN 'run_cancelled' END;
    IF expected_action IS NULL OR NEW.projection_action<>'checkpoint_only' THEN
        RETURN NEW;
    END IF;
    SELECT projected_message_id,projected_task_id,content_part
      INTO NEW.message_id,NEW.task_id,NEW.content_part
      FROM _agent_runtime_media_model_video_run_projection_v1(
          event,expected_action
      ) projected;
    NEW.projection_action:=expected_action;
    RETURN NEW;
END;
$$;
CREATE TRIGGER agent_runtime_media_model_video_run_projection_v1
BEFORE INSERT ON agent_runtime_media_projection_results
FOR EACH ROW EXECUTE FUNCTION _project_agent_runtime_model_video_run_v1();

CREATE FUNCTION _agent_runtime_media_model_video_event_v1(p_event_id UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
    SELECT EXISTS (
        SELECT 1 FROM agent_runtime_events event
        JOIN agent_runtime_prepared_media_action_bindings binding
          ON binding.run_id=event.run_id
       WHERE event.id=p_event_id
         AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
    )
$$;

DROP TRIGGER agent_runtime_media_wecom_delivery_v1
    ON agent_runtime_media_projection_results;
CREATE TRIGGER agent_runtime_media_wecom_delivery_v1
AFTER INSERT ON agent_runtime_media_projection_results
FOR EACH ROW WHEN (
    NEW.projection_kind='wecom'
    AND NOT _agent_runtime_media_model_video_event_v1(NEW.event_id)
) EXECUTE FUNCTION _project_agent_runtime_media_wecom_delivery_v1();

CREATE FUNCTION _project_agent_runtime_model_video_wecom_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    event agent_runtime_events%ROWTYPE;
    outbox agent_projection_outbox%ROWTYPE;
    run agent_runs%ROWTYPE;
    command agent_session_commands%ROWTYPE;
    parent_task tasks%ROWTYPE;
    output_message messages%ROWTYPE;
    delivery conversation_deliveries%ROWTYPE;
    expected_status TEXT;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    SELECT * INTO event FROM agent_runtime_events WHERE id=NEW.event_id;
    IF event.event_type NOT IN ('run.completed','run.failed','run.cancelled') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO outbox FROM agent_projection_outbox WHERE id=NEW.outbox_id;
    SELECT * INTO run FROM agent_runs WHERE id=event.run_id;
    SELECT * INTO command FROM agent_session_commands WHERE id=run.command_id;
    SELECT * INTO parent_task FROM tasks
     WHERE id=NULLIF(command.payload->>'task_id','')::UUID;
    SELECT * INTO output_message FROM messages
     WHERE id=NULLIF(command.payload->>'output_message_id','')::UUID;
    expected_status:=substring(event.event_type FROM 5);
    IF outbox.id IS NULL OR outbox.status<>'processing'
       OR outbox.event_id IS DISTINCT FROM event.id
       OR outbox.session_id IS DISTINCT FROM NEW.session_id
       OR outbox.org_id IS DISTINCT FROM NEW.org_id
       OR outbox.user_id IS DISTINCT FROM NEW.user_id
       OR outbox.projection_kind IS DISTINCT FROM NEW.projection_kind
       OR event.session_id IS DISTINCT FROM NEW.session_id
       OR event.org_id IS DISTINCT FROM NEW.org_id
       OR event.user_id IS DISTINCT FROM NEW.user_id
       OR run.session_id IS DISTINCT FROM event.session_id
       OR run.org_id IS DISTINCT FROM event.org_id
       OR run.user_id IS DISTINCT FROM event.user_id
       OR run.status::TEXT IS DISTINCT FROM expected_status
       OR NEW.projection_action IS DISTINCT FROM 'run_'||expected_status
       OR NEW.task_id IS DISTINCT FROM parent_task.id
       OR NEW.message_id IS DISTINCT FROM output_message.id
       OR parent_task.org_id IS DISTINCT FROM event.org_id
       OR parent_task.user_id IS DISTINCT FROM event.user_id
       OR parent_task.status::TEXT IS DISTINCT FROM expected_status
       OR parent_task.delivery_context @>
          '{"actor":false,"runtime":true,"channel":"wecom"}'::JSONB IS NOT TRUE
       OR output_message.id IS DISTINCT FROM parent_task.assistant_message_id
       OR output_message.org_id IS DISTINCT FROM event.org_id
       OR output_message.conversation_id IS DISTINCT FROM parent_task.conversation_id
       OR output_message.role::TEXT<>'assistant' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_WECOM_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    INSERT INTO conversation_deliveries(
        task_id,channel,delivery_kind,target_context
    ) VALUES(
        parent_task.id,'wecom','assistant_terminal',parent_task.delivery_context
    ) ON CONFLICT (task_id,channel,delivery_kind) DO NOTHING
    RETURNING * INTO delivery;
    IF delivery.id IS NULL THEN
        SELECT * INTO delivery FROM conversation_deliveries
         WHERE task_id=parent_task.id AND channel='wecom'
           AND delivery_kind='assistant_terminal';
    END IF;
    IF delivery.target_context IS DISTINCT FROM parent_task.delivery_context THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_CONFLICT'
            USING ERRCODE='23505';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER agent_runtime_media_model_video_wecom_delivery_v1
AFTER INSERT ON agent_runtime_media_projection_results
FOR EACH ROW WHEN (
    NEW.projection_kind='wecom'
    AND _agent_runtime_media_model_video_event_v1(NEW.event_id)
) EXECUTE FUNCTION _project_agent_runtime_model_video_wecom_v1();

REVOKE ALL ON FUNCTION
    _agent_runtime_prepared_media_source_v1(UUID),
    _agent_runtime_media_model_video_action_projection_v1(agent_runtime_events,JSONB),
    _agent_runtime_media_prepared_action_projection_v1(agent_runtime_events,JSONB),
    _agent_runtime_media_model_video_run_projection_v1(agent_runtime_events,TEXT),
    _project_agent_runtime_model_video_run_v1(),
    _agent_runtime_media_model_video_event_v1(UUID),
    _project_agent_runtime_model_video_wecom_v1()
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;

RESET ROLE;
