/* 228.08i1: normalize real Runtime image events without mutating event facts. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_agent_runtime_media_normalize_model_video_event_v1(agent_runtime_events)'
       ) IS NULL
       OR to_regclass('public.agent_runtime_media_normalized_projection_inputs_v1')
          IS NULL
       OR to_regclass('public.agent_runtime_prepared_image_batch_slots') IS NULL
       OR to_regprocedure(
           '_agent_runtime_media_normalize_model_video_event_228_08g1_v1'
           '(agent_runtime_events)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_228_08G1_F1_REQUIRED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE FUNCTION _agent_runtime_media_normalize_image_event_v1(
    p_event agent_runtime_events
) RETURNS agent_runtime_events LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    action agent_actions%ROWTYPE;
    prepared agent_runtime_prepared_media_action_bindings%ROWTYPE;
    binding agent_runtime_media_action_bindings%ROWTYPE;
    normalized agent_runtime_events%ROWTYPE;
    source TEXT;
BEGIN
    IF session_user<>'everydayai_projection_worker'
       OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_SCOPE_REQUIRED'
            USING ERRCODE='42501';
    END IF;
    IF p_event.id IS NULL OR p_event.action_id IS NOT NULL
       OR p_event.correlation_id IS NULL OR p_event.event_version<>1
       OR p_event.event_type NOT IN (
           'action.requested','action.accepted','action.unknown',
           'action.completed','action.failed','action.rejected','action.cancelled',
           'action.provider.accepted','action.provider.unknown',
           'action.completed_after_cancel','action.failed_after_cancel'
       ) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO action FROM agent_actions WHERE id=p_event.correlation_id;
    SELECT * INTO prepared FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=p_event.correlation_id;
    SELECT * INTO binding FROM agent_runtime_media_action_bindings
     WHERE action_id=p_event.correlation_id;
    source:=COALESCE(action.policy_snapshot->>'source','model_loop');
    IF action.id IS NULL OR action.tool_name<>'generate_image'
       OR (prepared.action_id IS NULL AND binding.action_id IS NULL)
       OR action.session_id IS DISTINCT FROM p_event.session_id
       OR action.run_id IS DISTINCT FROM p_event.run_id
       OR action.model_step_id IS DISTINCT FROM p_event.model_step_id
       OR action.org_id IS DISTINCT FROM p_event.org_id
       OR action.user_id IS DISTINCT FROM p_event.user_id
       OR NOT (CASE p_event.event_type
           WHEN 'action.requested' THEN p_event.actor_type IN ('model','user')
           WHEN 'action.accepted' THEN p_event.actor_type='executor'
           WHEN 'action.unknown' THEN p_event.actor_type IN ('executor','system')
           WHEN 'action.completed' THEN p_event.actor_type='executor'
           WHEN 'action.failed' THEN p_event.actor_type='executor'
           WHEN 'action.rejected' THEN p_event.actor_type='system'
           WHEN 'action.cancelled' THEN p_event.actor_type IN (
               'system','executor','reconciler'
           )
           WHEN 'action.provider.accepted' THEN p_event.actor_type='executor'
           WHEN 'action.provider.unknown' THEN p_event.actor_type='executor'
           WHEN 'action.completed_after_cancel' THEN
               p_event.actor_type='reconciler'
           WHEN 'action.failed_after_cancel' THEN
               p_event.actor_type='reconciler'
           ELSE FALSE
       END) THEN
        RETURN NULL;
    END IF;
    IF prepared.action_id IS NOT NULL THEN
        IF _agent_runtime_prepared_media_source_v1(action.id)<>'media_ingress'
           OR prepared.media_kind<>'image'
           OR (p_event.event_type='action.requested'
               AND p_event.actor_type<>'user')
           OR prepared.session_id IS DISTINCT FROM action.session_id
           OR prepared.run_id IS DISTINCT FROM action.run_id
           OR prepared.model_step_id IS DISTINCT FROM action.model_step_id
           OR prepared.org_id IS DISTINCT FROM action.org_id
           OR prepared.user_id IS DISTINCT FROM action.user_id
           OR (binding.action_id IS NOT NULL AND (
               binding.session_id IS DISTINCT FROM action.session_id
               OR binding.run_id IS DISTINCT FROM action.run_id
               OR binding.model_step_id IS DISTINCT FROM action.model_step_id
               OR binding.org_id IS DISTINCT FROM action.org_id
               OR binding.user_id IS DISTINCT FROM action.user_id
               OR binding.task_id IS DISTINCT FROM prepared.task_id
           )) THEN
            RETURN NULL;
        END IF;
    ELSIF source NOT IN ('model_loop','runtime_executor_registry')
       OR (p_event.event_type='action.requested'
           AND p_event.actor_type<>'model')
       OR binding.session_id IS DISTINCT FROM action.session_id
       OR binding.run_id IS DISTINCT FROM action.run_id
       OR binding.model_step_id IS DISTINCT FROM action.model_step_id
       OR binding.org_id IS DISTINCT FROM action.org_id
       OR binding.user_id IS DISTINCT FROM action.user_id THEN
        RETURN NULL;
    END IF;
    normalized:=p_event;
    normalized.action_id:=action.id;
    normalized.event_type:=CASE p_event.event_type
        WHEN 'action.provider.accepted' THEN 'action.accepted'
        WHEN 'action.provider.unknown' THEN 'action.unknown'
        WHEN 'action.completed_after_cancel' THEN 'action.completed'
        WHEN 'action.failed_after_cancel' THEN 'action.failed'
        ELSE p_event.event_type END;
    RETURN normalized;
END;
$$;

ALTER FUNCTION _agent_runtime_media_normalize_model_video_event_v1(
    agent_runtime_events
) RENAME TO _agent_runtime_media_normalize_model_video_event_228_08g1_v1;
CREATE FUNCTION _agent_runtime_media_normalize_model_video_event_v1(
    p_event agent_runtime_events
) RETURNS agent_runtime_events LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE normalized agent_runtime_events%ROWTYPE;
BEGIN
    normalized:=_agent_runtime_media_normalize_model_video_event_228_08g1_v1(
        p_event
    );
    IF normalized.id IS NOT NULL THEN RETURN normalized; END IF;
    RETURN _agent_runtime_media_normalize_image_event_v1(p_event);
END;
$$;

CREATE FUNCTION _project_agent_runtime_image_normalized_event_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    event agent_runtime_events%ROWTYPE;
    normalized agent_runtime_events%ROWTYPE;
    input agent_runtime_media_normalized_projection_inputs_v1%ROWTYPE;
    projected JSONB;
    slot JSONB;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    IF NEW.action_id IS NOT NULL OR NEW.projection_action<>'action_progress' THEN
        RETURN NEW;
    END IF;
    SELECT * INTO event FROM agent_runtime_events WHERE id=NEW.event_id;
    normalized:=_agent_runtime_media_normalize_image_event_v1(event);
    IF normalized.id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO input
      FROM agent_runtime_media_normalized_projection_inputs_v1
     WHERE outbox_id=NEW.outbox_id FOR UPDATE;
    IF input.outbox_id IS NULL OR input.event_id IS DISTINCT FROM normalized.id
       OR input.action_id IS DISTINCT FROM normalized.action_id
       OR input.session_id IS DISTINCT FROM normalized.session_id
       OR input.org_id IS DISTINCT FROM normalized.org_id
       OR input.user_id IS DISTINCT FROM normalized.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_NORMALIZED_INPUT_MISSING'
            USING ERRCODE='55000';
    END IF;
    projected:=_agent_runtime_media_action_projection_v1(
        normalized,input.content_part
    );
    slot:=projected->'slot';
    NEW.action_id:=normalized.action_id;
    NEW.projection_action:=projected->>'projection_action';
    NEW.message_id:=NULLIF(projected->>'message_id','')::UUID;
    NEW.task_id:=NULLIF(projected->>'task_id','')::UUID;
    NEW.slot_id:=NULLIF(slot->>'slot_id','')::UUID;
    NEW.slot_index:=NULLIF(slot->>'slot_index','')::INTEGER;
    NEW.slot_status:=NULLIF(slot->>'slot_status','');
    NEW.slot_revision:=NULLIF(slot->>'slot_revision','')::BIGINT;
    NEW.content_part:=COALESCE(slot,projected->'content_part');
    DELETE FROM agent_runtime_media_normalized_projection_inputs_v1
     WHERE outbox_id=NEW.outbox_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER agent_runtime_media_image_normalized_event_v1
BEFORE INSERT ON agent_runtime_media_projection_results
FOR EACH ROW EXECUTE FUNCTION _project_agent_runtime_image_normalized_event_v1();

CREATE FUNCTION _merge_agent_runtime_normalized_prepared_image_batch_v1(
    p_event agent_runtime_events,p_content_part JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
    task tasks%ROWTYPE;
    message messages%ROWTYPE;
    slot agent_runtime_prepared_image_batch_slots%ROWTYPE;
    batch_size INTEGER;
    terminal_count INTEGER;
    completed_count INTEGER;
    total_credits BIGINT;
    new_status TEXT;
    candidate JSONB;
    merged JSONB;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    SELECT * INTO binding FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=p_event.action_id;
    SELECT * INTO task FROM tasks WHERE id=binding.task_id;
    SELECT * INTO message FROM messages WHERE id=binding.output_message_id;
    IF binding.action_id IS NULL OR binding.media_kind<>'image'
       OR task.batch_id IS NULL OR task.delivery_context->>'channel'<>'web'
       OR message.generation_params->>'type'<>'image' THEN
        RETURN jsonb_build_object('outcome','not_batch');
    END IF;
    SELECT count(*) INTO batch_size FROM tasks sibling
     WHERE sibling.org_id IS NOT DISTINCT FROM task.org_id
       AND sibling.user_id=task.user_id
       AND sibling.conversation_id=task.conversation_id
       AND sibling.assistant_message_id=task.assistant_message_id
       AND sibling.batch_id=task.batch_id AND sibling.type::TEXT='image';
    IF batch_size=1 THEN RETURN jsonb_build_object('outcome','not_batch'); END IF;
    IF batch_size NOT BETWEEN 2 AND 10 OR p_event.session_id<>binding.session_id
       OR p_event.run_id<>binding.run_id
       OR p_event.org_id IS DISTINCT FROM binding.org_id
       OR p_event.user_id IS DISTINCT FROM binding.user_id
       OR task.assistant_message_id<>message.id
       OR EXISTS(
           SELECT 1 FROM tasks sibling
           LEFT JOIN agent_runtime_prepared_media_action_bindings sibling_binding
             ON sibling_binding.task_id=sibling.id
            WHERE sibling.org_id IS NOT DISTINCT FROM task.org_id
              AND sibling.user_id=task.user_id
              AND sibling.conversation_id=task.conversation_id
              AND sibling.assistant_message_id=task.assistant_message_id
              AND sibling.batch_id=task.batch_id
              AND (sibling_binding.action_id IS NULL
                   OR sibling.image_index NOT BETWEEN 0 AND 9)
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PROJECTION_FACTS_INVALID'
            USING ERRCODE='55000';
    END IF;
    INSERT INTO agent_runtime_prepared_image_batch_slots(
        action_id,task_id,output_message_id,org_id,user_id,conversation_id,
        batch_id,slot_id,slot_index,slot_status,slot_revision,content_part
    ) SELECT sibling_binding.action_id,sibling.id,sibling.assistant_message_id,
             sibling.org_id,sibling.user_id,sibling.conversation_id,sibling.batch_id,
             sibling_binding.action_id,sibling.image_index,'pending',0,
             jsonb_build_object('type','image','url',NULL,
                 'slot_id',sibling_binding.action_id,'slot_index',sibling.image_index,
                 'slot_status','pending','slot_revision',0)
        FROM tasks sibling
        JOIN agent_runtime_prepared_media_action_bindings sibling_binding
          ON sibling_binding.task_id=sibling.id
       WHERE sibling.org_id IS NOT DISTINCT FROM task.org_id
         AND sibling.user_id=task.user_id
         AND sibling.conversation_id=task.conversation_id
         AND sibling.assistant_message_id=task.assistant_message_id
         AND sibling.batch_id=task.batch_id
    ON CONFLICT(action_id) DO NOTHING;
    new_status:=CASE p_event.event_type
        WHEN 'action.accepted' THEN 'accepted' WHEN 'action.unknown' THEN 'unknown'
        WHEN 'action.completed' THEN 'completed'
        WHEN 'action.cancelled' THEN 'cancelled'
        WHEN 'action.failed' THEN 'failed' WHEN 'action.rejected' THEN 'failed'
        ELSE 'pending' END;
    IF new_status='completed' THEN
        candidate:=CASE WHEN jsonb_typeof(task.result->'content_parts')='array'
            THEN task.result->'content_parts'->0 ELSE p_content_part END;
        IF jsonb_typeof(candidate) IS DISTINCT FROM 'object'
           OR NULLIF(btrim(candidate->>'url'),'') IS NULL THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_CONTENT_REQUIRED'
                USING ERRCODE='55000';
        END IF;
        candidate:=candidate||jsonb_build_object('type','image');
    ELSIF new_status IN ('failed','cancelled') THEN
        candidate:=COALESCE(task.result,'{}'::JSONB)||jsonb_build_object(
            'type','image','url',NULL,'failed',TRUE,
            'error_code',COALESCE(task.error_message,'action_failed'),
            'error',COALESCE(task.error_message,'action_failed'));
    END IF;
    UPDATE agent_runtime_prepared_image_batch_slots current_slot SET
        slot_status=new_status,slot_revision=p_event.sequence,
        content_part=COALESCE(candidate,current_slot.content_part),
        state_version=current_slot.state_version+1,updated_at=clock_timestamp()
     WHERE current_slot.action_id=binding.action_id
       AND p_event.sequence>current_slot.slot_revision
       AND current_slot.slot_status NOT IN ('completed','failed','cancelled')
       AND (current_slot.slot_status='pending'
            OR new_status=current_slot.slot_status
            OR (current_slot.slot_status='unknown' AND new_status='accepted')
            OR new_status IN ('completed','failed','cancelled'))
    RETURNING * INTO slot;
    IF slot.action_id IS NULL THEN
        SELECT * INTO slot FROM agent_runtime_prepared_image_batch_slots
         WHERE action_id=binding.action_id;
    END IF;
    SELECT count(*) FILTER(WHERE batch_slot.slot_status IN(
               'completed','failed','cancelled')),
           count(*) FILTER(WHERE batch_slot.slot_status='completed'),
           COALESCE(sum(sibling_binding.unit_credits) FILTER(
               WHERE batch_slot.slot_status='completed'),0),
           jsonb_agg(batch_slot.content_part||jsonb_build_object(
               'slot_id',batch_slot.slot_id,'slot_index',batch_slot.slot_index,
               'slot_status',batch_slot.slot_status,
               'slot_revision',batch_slot.slot_revision)
               ORDER BY batch_slot.slot_index)
      INTO terminal_count,completed_count,total_credits,merged
      FROM agent_runtime_prepared_image_batch_slots batch_slot
      JOIN agent_runtime_prepared_media_action_bindings sibling_binding
        ON sibling_binding.action_id=batch_slot.action_id
     WHERE batch_slot.output_message_id=message.id
       AND batch_slot.batch_id=task.batch_id;
    UPDATE messages SET content=merged::TEXT,
        status=(CASE WHEN terminal_count<batch_size THEN 'pending'
                     WHEN completed_count>0 THEN 'completed' ELSE 'failed' END
               )::message_status,
        credits_cost=CASE WHEN terminal_count=batch_size
            THEN total_credits::INTEGER ELSE credits_cost END
     WHERE id=message.id;
    RETURN jsonb_build_object('outcome','merged','slot_id',slot.slot_id,
        'slot_index',slot.slot_index,'slot_status',slot.slot_status,
        'slot_revision',slot.slot_revision,'content_part',slot.content_part);
END;
$$;

CREATE FUNCTION _project_agent_runtime_normalized_image_batch_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE event agent_runtime_events%ROWTYPE;
        normalized agent_runtime_events%ROWTYPE; merged JSONB;
BEGIN
    IF NEW.action_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO event FROM agent_runtime_events WHERE id=NEW.event_id;
    normalized:=_agent_runtime_media_normalize_image_event_v1(event);
    IF normalized.id IS NULL OR normalized.action_id<>NEW.action_id THEN RETURN NEW; END IF;
    merged:=_merge_agent_runtime_normalized_prepared_image_batch_v1(
        normalized,NEW.content_part
    );
    IF merged->>'outcome'<>'merged' THEN RETURN NEW; END IF;
    NEW.slot_id:=(merged->>'slot_id')::UUID;
    NEW.slot_index:=(merged->>'slot_index')::INTEGER;
    NEW.slot_status:=merged->>'slot_status';
    NEW.slot_revision:=(merged->>'slot_revision')::BIGINT;
    NEW.content_part:=merged->'content_part';
    RETURN NEW;
END;
$$;
CREATE TRIGGER agent_runtime_media_image_zbatch_result_v1
BEFORE INSERT ON agent_runtime_media_projection_results
FOR EACH ROW EXECUTE FUNCTION _project_agent_runtime_normalized_image_batch_v1();

REVOKE ALL ON FUNCTION
    _agent_runtime_media_normalize_image_event_v1(agent_runtime_events),
    _agent_runtime_media_normalize_model_video_event_v1(agent_runtime_events),
    _project_agent_runtime_image_normalized_event_v1(),
    _merge_agent_runtime_normalized_prepared_image_batch_v1(
        agent_runtime_events,JSONB
    ),
    _project_agent_runtime_normalized_image_batch_v1()
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;

RESET ROLE;
