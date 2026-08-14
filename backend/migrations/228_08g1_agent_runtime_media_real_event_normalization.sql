/* 228.08g1: normalize real Runtime Action events for ModelLoop video projection. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_agent_runtime_media_model_video_action_projection_v1(agent_runtime_events,jsonb)'
       ) IS NULL
       OR to_regprocedure(
           '_apply_agent_runtime_media_projection_228_06_v1(uuid,uuid,text,jsonb)'
       ) IS NOT NULL
       OR to_regclass(
           'public.agent_runtime_media_normalized_projection_inputs_v1'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_228_08G1_IDENTITY_CONFLICT'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE TABLE agent_runtime_media_normalized_projection_inputs_v1(
    outbox_id UUID PRIMARY KEY
        REFERENCES agent_projection_outbox(id) ON DELETE RESTRICT,
    event_id UUID NOT NULL REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,
    action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    lease_token UUID NOT NULL,
    content_part JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK(content_part IS NULL OR jsonb_typeof(content_part)='object')
);
ALTER TABLE agent_runtime_media_normalized_projection_inputs_v1
    ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_media_normalized_projection_inputs_owner_all
    ON agent_runtime_media_normalized_projection_inputs_v1
    FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
ALTER TABLE agent_runtime_media_normalized_projection_inputs_v1
    FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _agent_runtime_media_normalize_model_video_event_v1(
    p_event agent_runtime_events
) RETURNS agent_runtime_events LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    action agent_actions%ROWTYPE;
    binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
    normalized agent_runtime_events%ROWTYPE;
    logical_type TEXT;
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
    SELECT * INTO action FROM agent_actions
     WHERE id=p_event.correlation_id;
    SELECT * INTO binding FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=p_event.correlation_id;
    IF action.id IS NULL OR binding.action_id IS NULL
       OR action.tool_name<>'generate_video' OR binding.media_kind<>'video'
       OR _agent_runtime_prepared_media_source_v1(action.id)<>'model_loop'
       OR action.session_id IS DISTINCT FROM p_event.session_id
       OR action.run_id IS DISTINCT FROM p_event.run_id
       OR action.model_step_id IS DISTINCT FROM p_event.model_step_id
       OR action.org_id IS DISTINCT FROM p_event.org_id
       OR action.user_id IS DISTINCT FROM p_event.user_id
       OR binding.session_id IS DISTINCT FROM action.session_id
       OR binding.run_id IS DISTINCT FROM action.run_id
       OR binding.model_step_id IS DISTINCT FROM action.model_step_id
       OR binding.org_id IS DISTINCT FROM action.org_id
       OR binding.user_id IS DISTINCT FROM action.user_id
       OR NOT (
           (p_event.event_type='action.requested'
                AND p_event.actor_type='model')
           OR (p_event.event_type='action.cancelled'
                AND p_event.actor_type='system')
           OR (p_event.event_type IN (
                   'action.completed_after_cancel','action.failed_after_cancel'
               ) AND p_event.actor_type='reconciler')
           OR (p_event.event_type NOT IN (
                   'action.requested','action.cancelled',
                   'action.completed_after_cancel','action.failed_after_cancel'
               ) AND p_event.actor_type='executor')
       ) THEN
        RETURN NULL;
    END IF;
    logical_type:=CASE p_event.event_type
        WHEN 'action.provider.accepted' THEN 'action.accepted'
        WHEN 'action.provider.unknown' THEN 'action.unknown'
        WHEN 'action.completed_after_cancel' THEN 'action.completed'
        WHEN 'action.failed_after_cancel' THEN 'action.failed'
        ELSE p_event.event_type
    END;
    normalized:=p_event;
    normalized.action_id:=action.id;
    normalized.event_type:=logical_type;
    RETURN normalized;
END;
$$;

ALTER FUNCTION _agent_runtime_media_projection_action_v1(agent_runtime_events)
    RENAME TO _agent_runtime_media_projection_action_228_06_v1;
CREATE FUNCTION _agent_runtime_media_projection_action_v1(
    p_event agent_runtime_events
) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE normalized agent_runtime_events%ROWTYPE;
BEGIN
    normalized:=_agent_runtime_media_normalize_model_video_event_v1(p_event);
    IF normalized.id IS NOT NULL THEN RETURN 'action_progress'; END IF;
    RETURN _agent_runtime_media_projection_action_228_06_v1(p_event);
END;
$$;

ALTER FUNCTION read_agent_runtime_media_projection_v1(UUID,UUID)
    RENAME TO _read_agent_runtime_media_projection_228_06_v1;
CREATE FUNCTION read_agent_runtime_media_projection_v1(
    p_outbox_id UUID,p_lease_token UUID
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    result JSONB;
    event agent_runtime_events%ROWTYPE;
    normalized agent_runtime_events%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    result:=_read_agent_runtime_media_projection_228_06_v1(
        p_outbox_id,p_lease_token
    );
    IF result->>'outcome'<>'found' THEN RETURN result; END IF;
    SELECT source.* INTO event
      FROM agent_projection_outbox outbox
      JOIN agent_runtime_events source ON source.id=outbox.event_id
     WHERE outbox.id=p_outbox_id;
    normalized:=_agent_runtime_media_normalize_model_video_event_v1(event);
    IF normalized.id IS NOT NULL THEN
        result:=jsonb_set(
            result,'{action_facts}',
            _agent_runtime_media_action_facts_v1(normalized),TRUE
        );
    END IF;
    RETURN result;
END;
$$;

ALTER FUNCTION apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB)
    RENAME TO _apply_agent_runtime_media_projection_228_06_v1;
CREATE FUNCTION apply_agent_runtime_media_projection_v1(
    p_outbox_id UUID,p_lease_token UUID,p_action TEXT,
    p_content_part JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    outbox agent_projection_outbox%ROWTYPE;
    event agent_runtime_events%ROWTYPE;
    normalized agent_runtime_events%ROWTYPE;
    result JSONB;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    SELECT * INTO outbox FROM agent_projection_outbox WHERE id=p_outbox_id;
    SELECT * INTO event FROM agent_runtime_events WHERE id=outbox.event_id;
    normalized:=_agent_runtime_media_normalize_model_video_event_v1(event);
    IF normalized.id IS NULL THEN
        RETURN _apply_agent_runtime_media_projection_228_06_v1(
            p_outbox_id,p_lease_token,p_action,p_content_part
        );
    END IF;
    IF p_action<>'action_progress'
       OR outbox.status<>'processing'
       OR outbox.lease_token IS DISTINCT FROM p_lease_token
       OR outbox.lease_expires_at<=clock_timestamp()
       OR outbox.session_id IS DISTINCT FROM normalized.session_id
       OR outbox.org_id IS DISTINCT FROM normalized.org_id
       OR outbox.user_id IS DISTINCT FROM normalized.user_id
       OR (normalized.event_type='action.completed' AND (
           jsonb_typeof(p_content_part) IS DISTINCT FROM 'object'
           OR pg_column_size(p_content_part)>65536
       ))
       OR (normalized.event_type<>'action.completed'
           AND p_content_part IS NOT NULL) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_NORMALIZED_INPUT_INVALID'
            USING ERRCODE='22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_runtime_media_projection_results
         WHERE outbox_id=p_outbox_id
    ) THEN
        RETURN _apply_agent_runtime_media_projection_228_06_v1(
            p_outbox_id,p_lease_token,p_action,p_content_part
        );
    END IF;
    INSERT INTO agent_runtime_media_normalized_projection_inputs_v1(
        outbox_id,event_id,action_id,session_id,org_id,user_id,
        lease_token,content_part
    ) VALUES(
        outbox.id,event.id,normalized.action_id,normalized.session_id,
        normalized.org_id,normalized.user_id,p_lease_token,p_content_part
    );
    result:=_apply_agent_runtime_media_projection_228_06_v1(
        p_outbox_id,p_lease_token,p_action,p_content_part
    );
    DELETE FROM agent_runtime_media_normalized_projection_inputs_v1
     WHERE outbox_id=p_outbox_id;
    RETURN result;
END;
$$;

CREATE FUNCTION _project_agent_runtime_model_video_normalized_event_v1()
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
    normalized:=_agent_runtime_media_normalize_model_video_event_v1(event);
    IF normalized.id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO input
      FROM agent_runtime_media_normalized_projection_inputs_v1
     WHERE outbox_id=NEW.outbox_id FOR UPDATE;
    IF input.outbox_id IS NULL
       OR input.event_id IS DISTINCT FROM normalized.id
       OR input.action_id IS DISTINCT FROM normalized.action_id
       OR input.session_id IS DISTINCT FROM normalized.session_id
       OR input.org_id IS DISTINCT FROM normalized.org_id
       OR input.user_id IS DISTINCT FROM normalized.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_NORMALIZED_INPUT_MISSING'
            USING ERRCODE='55000';
    END IF;
    projected:=_agent_runtime_media_model_video_action_projection_v1(
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
CREATE TRIGGER agent_runtime_media_model_video_normalized_event_v1
BEFORE INSERT ON agent_runtime_media_projection_results
FOR EACH ROW EXECUTE FUNCTION
    _project_agent_runtime_model_video_normalized_event_v1();

REVOKE ALL ON TABLE agent_runtime_media_normalized_projection_inputs_v1
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION
    _agent_runtime_media_normalize_model_video_event_v1(agent_runtime_events),
    _agent_runtime_media_projection_action_v1(agent_runtime_events),
    _agent_runtime_media_projection_action_228_06_v1(agent_runtime_events),
    _read_agent_runtime_media_projection_228_06_v1(UUID,UUID),
    _apply_agent_runtime_media_projection_228_06_v1(UUID,UUID,TEXT,JSONB),
    _project_agent_runtime_model_video_normalized_event_v1()
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION
    read_agent_runtime_media_projection_v1(UUID,UUID),
    apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_authorization_worker,everydayai_sandbox_worker,
    everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION
    read_agent_runtime_media_projection_v1(UUID,UUID),
    apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB)
TO everydayai_projection_worker;

RESET ROLE;
