/* 228.08f1: merge ordinary Web prepared-image Actions by stable batch slots. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regclass('public.agent_runtime_media_projection_results') IS NULL
       OR to_regprocedure(
           'submit_agent_runtime_media_image_batch_v1(uuid,uuid,uuid,text,text,uuid,'
           'text,text,uuid,uuid,uuid,text,text,text,text,text,text,jsonb)'
       ) IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_228_08D_REQUIRED'
            USING ERRCODE='55000';
    END IF;
    IF to_regclass('public.agent_runtime_prepared_image_batch_slots') IS NOT NULL
       OR to_regprocedure(
           '_merge_agent_runtime_prepared_image_batch_projection_v1(uuid,jsonb)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PROJECTION_IDENTITY_CONFLICT'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM tasks task
          JOIN messages message ON message.id=task.assistant_message_id
          JOIN agent_runtime_prepared_media_action_bindings binding
            ON binding.task_id=task.id
         WHERE task.type::TEXT='image'
           AND task.batch_id IS NOT NULL
           AND task.delivery_context->>'channel'='web'
           AND message.generation_params->>'type'='image'
         GROUP BY task.org_id,task.user_id,task.conversation_id,
                  task.assistant_message_id,task.batch_id
        HAVING count(*)>1 AND (
            bool_or(COALESCE(binding.projection_revision,0)>0)
            OR bool_or(task.status::TEXT IN ('completed','failed','cancelled'))
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PROJECTION_RECONCILE_REQUIRED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE TABLE agent_runtime_prepared_image_batch_slots(
    action_id UUID PRIMARY KEY
        REFERENCES agent_runtime_prepared_media_action_bindings(action_id)
        ON DELETE RESTRICT,
    task_id UUID NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    output_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    batch_id TEXT NOT NULL CHECK(length(btrim(batch_id)) BETWEEN 1 AND 200),
    slot_id UUID NOT NULL UNIQUE,
    slot_index INTEGER NOT NULL CHECK(slot_index BETWEEN 0 AND 9),
    slot_status TEXT NOT NULL CHECK(slot_status IN(
        'pending','accepted','unknown','completed','failed','cancelled'
    )),
    slot_revision BIGINT NOT NULL CHECK(slot_revision>=0),
    content_part JSONB NOT NULL CHECK(jsonb_typeof(content_part)='object'),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(output_message_id,batch_id,slot_index),
    CHECK(slot_id=action_id)
);
ALTER TABLE agent_runtime_prepared_image_batch_slots ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_prepared_image_batch_slots_owner_all
    ON agent_runtime_prepared_image_batch_slots FOR ALL TO everydayai_owner
    USING(TRUE) WITH CHECK(TRUE);
ALTER TABLE agent_runtime_prepared_image_batch_slots FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _merge_agent_runtime_prepared_image_batch_projection_v1(
    p_event_id UUID,p_content_part JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    event agent_runtime_events%ROWTYPE;
    binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
    task tasks%ROWTYPE;
    message messages%ROWTYPE;
    slot agent_runtime_prepared_image_batch_slots%ROWTYPE;
    batch_size INTEGER;
    bound_count INTEGER;
    index_count INTEGER;
    terminal_count INTEGER;
    completed_count INTEGER;
    total_credits BIGINT;
    new_status TEXT;
    error_code TEXT;
    candidate JSONB;
    merged_content JSONB;
    merged_message_status TEXT;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    SELECT * INTO event FROM agent_runtime_events WHERE id=p_event_id;
    SELECT * INTO binding FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=event.action_id;
    SELECT * INTO task FROM tasks WHERE id=binding.task_id;
    SELECT * INTO message FROM messages WHERE id=binding.output_message_id;
    IF event.id IS NULL OR binding.action_id IS NULL OR task.id IS NULL
       OR binding.media_kind<>'image' OR task.type::TEXT<>'image'
       OR task.batch_id IS NULL OR task.delivery_context->>'channel'<>'web'
       OR message.generation_params->>'type'<>'image' THEN
        RETURN jsonb_build_object('outcome','not_batch');
    END IF;
    IF event.action_id IS DISTINCT FROM binding.action_id
       OR event.session_id IS DISTINCT FROM binding.session_id
       OR event.run_id IS DISTINCT FROM binding.run_id
       OR event.org_id IS DISTINCT FROM binding.org_id
       OR event.user_id IS DISTINCT FROM binding.user_id
       OR task.user_id IS DISTINCT FROM binding.user_id
       OR task.org_id IS DISTINCT FROM binding.org_id
       OR task.conversation_id IS DISTINCT FROM binding.conversation_id
       OR task.assistant_message_id IS DISTINCT FROM binding.output_message_id
       OR message.conversation_id IS DISTINCT FROM binding.conversation_id
       OR message.org_id IS DISTINCT FROM binding.org_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PROJECTION_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    SELECT count(*),count(sibling_binding.action_id),
           count(DISTINCT sibling.image_index)
      INTO batch_size,bound_count,index_count
      FROM tasks sibling
      LEFT JOIN agent_runtime_prepared_media_action_bindings sibling_binding
        ON sibling_binding.task_id=sibling.id
     WHERE sibling.org_id IS NOT DISTINCT FROM task.org_id
       AND sibling.user_id=task.user_id
       AND sibling.conversation_id=task.conversation_id
       AND sibling.assistant_message_id=task.assistant_message_id
       AND sibling.batch_id=task.batch_id
       AND sibling.type::TEXT='image';
    IF batch_size=1 THEN RETURN jsonb_build_object('outcome','not_batch'); END IF;
    IF batch_size NOT BETWEEN 2 AND 10 OR bound_count<>batch_size
       OR index_count<>batch_size OR EXISTS(
           SELECT 1 FROM tasks sibling
            WHERE sibling.org_id IS NOT DISTINCT FROM task.org_id
              AND sibling.user_id=task.user_id
              AND sibling.conversation_id=task.conversation_id
              AND sibling.assistant_message_id=task.assistant_message_id
              AND sibling.batch_id=task.batch_id
              AND (sibling.type::TEXT<>'image'
                   OR sibling.image_index NOT BETWEEN 0 AND 9
                   OR sibling.delivery_context @> '{"actor":false,"runtime":true,"channel":"web"}'::JSONB
                      IS NOT TRUE)
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
             jsonb_build_object(
                 'type','image','url',NULL,'slot_id',sibling_binding.action_id,
                 'slot_index',sibling.image_index,'slot_status','pending',
                 'slot_revision',0
             )
        FROM tasks sibling
        JOIN agent_runtime_prepared_media_action_bindings sibling_binding
          ON sibling_binding.task_id=sibling.id
       WHERE sibling.org_id IS NOT DISTINCT FROM task.org_id
         AND sibling.user_id=task.user_id
         AND sibling.conversation_id=task.conversation_id
         AND sibling.assistant_message_id=task.assistant_message_id
         AND sibling.batch_id=task.batch_id
       ORDER BY sibling.image_index,sibling.id
    ON CONFLICT(action_id) DO NOTHING;

    new_status:=CASE event.event_type
        WHEN 'action.accepted' THEN 'accepted'
        WHEN 'action.unknown' THEN 'unknown'
        WHEN 'action.completed' THEN 'completed'
        WHEN 'action.failed' THEN 'failed'
        WHEN 'action.rejected' THEN 'failed'
        WHEN 'action.cancelled' THEN 'cancelled'
        ELSE 'pending'
    END;
    IF new_status='completed' THEN
        candidate:=CASE
            WHEN jsonb_typeof(task.result->'content_parts')='array'
                 AND jsonb_array_length(task.result->'content_parts')=1
            THEN task.result->'content_parts'->0
            ELSE p_content_part
        END;
        IF jsonb_typeof(candidate) IS DISTINCT FROM 'object'
           OR NULLIF(btrim(candidate->>'url'),'') IS NULL THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_CONTENT_REQUIRED'
                USING ERRCODE='55000';
        END IF;
        candidate:=candidate||jsonb_build_object('type','image');
    ELSIF new_status IN ('failed','cancelled') THEN
        error_code:=COALESCE(
            NULLIF(task.result->>'error_code',''),NULLIF(task.error_message,''),
            CASE new_status WHEN 'cancelled' THEN 'action_cancelled' ELSE 'action_failed' END
        );
        candidate:=CASE WHEN jsonb_typeof(task.result)='object'
            THEN task.result ELSE '{}'::JSONB END;
        candidate:=candidate||jsonb_build_object(
            'type','image','url',NULL,'failed',TRUE,
            'error_code',error_code,'error',error_code
        );
    END IF;

    UPDATE agent_runtime_prepared_image_batch_slots current_slot SET
        slot_status=new_status,slot_revision=event.sequence,
        content_part=CASE WHEN candidate IS NULL
            THEN current_slot.content_part ELSE candidate END,
        state_version=current_slot.state_version+1,updated_at=clock_timestamp()
     WHERE current_slot.action_id=binding.action_id
       AND event.sequence>current_slot.slot_revision
       AND current_slot.slot_status NOT IN ('completed','failed','cancelled')
       AND (
           current_slot.slot_status='pending'
           OR new_status=current_slot.slot_status
           OR (
               current_slot.slot_status='unknown'
               AND new_status='accepted'
           )
           OR new_status IN ('completed','failed','cancelled')
       )
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
           jsonb_agg(
               batch_slot.content_part||jsonb_build_object(
                   'slot_id',batch_slot.slot_id,'slot_index',batch_slot.slot_index,
                   'slot_status',batch_slot.slot_status,
                   'slot_revision',batch_slot.slot_revision
               ) ORDER BY batch_slot.slot_index
           )
      INTO terminal_count,completed_count,total_credits,merged_content
      FROM agent_runtime_prepared_image_batch_slots batch_slot
      JOIN agent_runtime_prepared_media_action_bindings sibling_binding
        ON sibling_binding.action_id=batch_slot.action_id
     WHERE batch_slot.output_message_id=message.id
       AND batch_slot.batch_id=task.batch_id;
    merged_message_status:=CASE
        WHEN terminal_count<batch_size THEN 'pending'
        WHEN completed_count>0 THEN 'completed'
        ELSE 'failed'
    END;
    UPDATE messages SET content=merged_content::TEXT,
        status=merged_message_status::message_status,
        credits_cost=CASE WHEN terminal_count=batch_size
            THEN total_credits::INTEGER ELSE credits_cost END
     WHERE id=message.id;
    RETURN jsonb_build_object(
        'outcome','merged','slot_id',slot.slot_id,'slot_index',slot.slot_index,
        'slot_status',slot.slot_status,'slot_revision',slot.slot_revision,
        'content_part',slot.content_part||jsonb_build_object(
            'slot_id',slot.slot_id,'slot_index',slot.slot_index,
            'slot_status',slot.slot_status,'slot_revision',slot.slot_revision
        ),'terminal_count',terminal_count,'total_count',batch_size,
        'message_status',merged_message_status
    );
END;
$$;

CREATE FUNCTION _project_agent_runtime_prepared_image_batch_result_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE merged JSONB;
BEGIN
    IF NEW.action_id IS NULL THEN RETURN NEW; END IF;
    merged:=_merge_agent_runtime_prepared_image_batch_projection_v1(
        NEW.event_id,NEW.content_part
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
CREATE TRIGGER agent_runtime_prepared_image_batch_result_v1
BEFORE INSERT ON agent_runtime_media_projection_results
FOR EACH ROW EXECUTE FUNCTION _project_agent_runtime_prepared_image_batch_result_v1();

REVOKE ALL ON TABLE agent_runtime_prepared_image_batch_slots
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION
    _merge_agent_runtime_prepared_image_batch_projection_v1(UUID,JSONB),
    _project_agent_runtime_prepared_image_batch_result_v1()
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;

RESET ROLE;
