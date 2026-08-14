/* 228.08i2: derive ModelLoop image/video WeCom outboxes from real Run facts. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_agent_runtime_media_normalize_image_event_v1(agent_runtime_events)'
       ) IS NULL
       OR to_regprocedure(
           '_derive_agent_runtime_model_video_wecom_outbox_v1()'
       ) IS NULL
       OR to_regclass('public.agent_runtime_media_wecom_outbox_facts_v1') IS NULL
       OR to_regclass('public.agent_runtime_media_image_wecom_outbox_facts_v1')
          IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_228_08I1_G2_REQUIRED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE TABLE agent_runtime_media_image_wecom_outbox_facts_v1(
    source_outbox_id UUID PRIMARY KEY
        REFERENCES agent_projection_outbox(id) ON DELETE RESTRICT,
    delivery_outbox_id UUID NOT NULL UNIQUE
        REFERENCES agent_projection_outbox(id) ON DELETE RESTRICT,
    event_id UUID NOT NULL UNIQUE
        REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE RESTRICT,
    anchor_action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    parent_task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    binding_count INTEGER NOT NULL CHECK(binding_count BETWEEN 1 AND 10),
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE agent_runtime_media_image_wecom_outbox_facts_v1
    ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_media_image_wecom_outbox_facts_owner_all
    ON agent_runtime_media_image_wecom_outbox_facts_v1
    FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
ALTER TABLE agent_runtime_media_image_wecom_outbox_facts_v1
    FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _derive_agent_runtime_model_media_wecom_outbox_v2()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    event agent_runtime_events%ROWTYPE;
    run agent_runs%ROWTYPE;
    runtime_session agent_runtime_sessions%ROWTYPE;
    command agent_session_commands%ROWTYPE;
    parent_task tasks%ROWTYPE;
    output_message messages%ROWTYPE;
    delivery agent_projection_outbox%ROWTYPE;
    image_fact agent_runtime_media_image_wecom_outbox_facts_v1%ROWTYPE;
    video_fact agent_runtime_media_wecom_outbox_facts_v1%ROWTYPE;
    anchor_action_id UUID;
    video_anchor_action_id UUID;
    image_count INTEGER:=0;
    video_count INTEGER:=0;
    lane TEXT;
    access_kind TEXT:=current_setting('app.access_kind',TRUE);
BEGIN
    IF NEW.projection_kind<>'web_runtime' THEN RETURN NEW; END IF;
    SELECT * INTO event FROM agent_runtime_events WHERE id=NEW.event_id;
    IF event.event_type NOT IN ('run.completed','run.failed','run.cancelled')
       OR event.action_id IS NOT NULL OR event.run_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO run FROM agent_runs WHERE id=event.run_id;
    IF run.capability_snapshot->>'channel'<>'wecom' THEN RETURN NEW; END IF;
    IF NOT (
        (session_user='everydayai_agent_runtime_worker'
            AND access_kind='agent_runtime')
        OR (session_user IN ('everydayai_runtime','everydayai_wecom_runtime')
            AND access_kind='runtime')
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_OUTBOX_ACTOR_INVALID'
            USING ERRCODE='42501';
    END IF;
    SELECT count(*),(array_agg(binding.action_id ORDER BY binding.action_id))[1]
      INTO image_count,anchor_action_id
      FROM agent_runtime_media_action_bindings binding
      JOIN agent_actions action ON action.id=binding.action_id
      LEFT JOIN agent_runtime_prepared_media_action_bindings prepared
        ON prepared.action_id=binding.action_id
     WHERE binding.run_id=run.id AND prepared.action_id IS NULL
       AND action.tool_name='generate_image'
       AND COALESCE(action.policy_snapshot->>'source','model_loop')
           IN ('model_loop','runtime_executor_registry');
    SELECT count(*),(array_agg(binding.action_id ORDER BY binding.action_id))[1]
      INTO video_count,video_anchor_action_id
      FROM agent_runtime_prepared_media_action_bindings binding
      JOIN agent_actions action ON action.id=binding.action_id
     WHERE binding.run_id=run.id AND binding.media_kind='video'
       AND action.tool_name='generate_video'
       AND _agent_runtime_prepared_media_source_v1(action.id)='model_loop';
    anchor_action_id:=COALESCE(anchor_action_id,video_anchor_action_id);
    IF image_count=0 AND video_count=0 THEN
        IF EXISTS(
            SELECT 1 FROM agent_runtime_media_action_bindings binding
            LEFT JOIN agent_runtime_prepared_media_action_bindings prepared
              ON prepared.action_id=binding.action_id
             WHERE binding.run_id=run.id AND prepared.action_id IS NULL
        ) OR EXISTS(
            SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
             WHERE binding.run_id=run.id
               AND _agent_runtime_prepared_media_source_v1(binding.action_id)
                   ='model_loop'
        ) THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_OUTBOX_SCOPE_INVALID'
                USING ERRCODE='42501';
        END IF;
        RETURN NEW;
    END IF;
    IF image_count>0 AND video_count>0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RUN_OWNER_AMBIGUOUS'
            USING ERRCODE='55000';
    END IF;
    lane:=CASE WHEN image_count>0 THEN 'image' ELSE 'video' END;
    SELECT * INTO runtime_session FROM agent_runtime_sessions
     WHERE id=event.session_id;
    SELECT * INTO command FROM agent_session_commands WHERE id=run.command_id;
    SELECT * INTO parent_task FROM tasks
     WHERE id=NULLIF(command.payload->>'task_id','')::UUID;
    SELECT * INTO output_message FROM messages
     WHERE id=NULLIF(command.payload->>'output_message_id','')::UUID;
    IF runtime_session.id IS NULL OR command.id IS NULL OR parent_task.id IS NULL
       OR output_message.id IS NULL OR anchor_action_id IS NULL
       OR NEW.session_id IS DISTINCT FROM event.session_id
       OR NEW.org_id IS DISTINCT FROM event.org_id
       OR NEW.user_id IS DISTINCT FROM event.user_id
       OR run.session_id IS DISTINCT FROM event.session_id
       OR run.org_id IS DISTINCT FROM event.org_id
       OR run.user_id IS DISTINCT FROM event.user_id
       OR run.status::TEXT IS DISTINCT FROM substring(event.event_type FROM 5)
       OR command.session_id IS DISTINCT FROM event.session_id
       OR command.org_id IS DISTINCT FROM event.org_id
       OR command.user_id IS DISTINCT FROM event.user_id
       OR parent_task.org_id IS DISTINCT FROM event.org_id
       OR parent_task.user_id IS DISTINCT FROM event.user_id
       OR parent_task.conversation_id IS DISTINCT FROM runtime_session.conversation_id
       OR parent_task.assistant_message_id IS DISTINCT FROM output_message.id
       OR parent_task.delivery_context @>
          '{"actor":false,"runtime":true,"channel":"wecom"}'::JSONB IS NOT TRUE
       OR output_message.org_id IS DISTINCT FROM event.org_id
       OR output_message.conversation_id IS DISTINCT FROM parent_task.conversation_id
       OR output_message.role::TEXT<>'assistant' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_OUTBOX_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    IF lane='image' AND (
        image_count NOT BETWEEN 1 AND 10 OR EXISTS(
            SELECT 1 FROM agent_runtime_media_action_bindings binding
            JOIN agent_actions action ON action.id=binding.action_id
            LEFT JOIN agent_runtime_prepared_media_action_bindings prepared
              ON prepared.action_id=binding.action_id
             WHERE binding.run_id=run.id AND (
                 prepared.action_id IS NOT NULL
                 OR action.tool_name<>'generate_image'
                 OR COALESCE(action.policy_snapshot->>'source','model_loop')
                    NOT IN ('model_loop','runtime_executor_registry')
                 OR binding.session_id IS DISTINCT FROM event.session_id
                 OR binding.org_id IS DISTINCT FROM event.org_id
                 OR binding.user_id IS DISTINCT FROM event.user_id
                 OR binding.chat_task_id IS DISTINCT FROM parent_task.id
                 OR binding.output_message_id IS DISTINCT FROM output_message.id
             )
        )
    ) OR lane='video' AND (
        video_count<>1 OR EXISTS(
            SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
             WHERE binding.run_id=run.id AND (
                 binding.media_kind<>'video'
                 OR _agent_runtime_prepared_media_source_v1(binding.action_id)
                    <>'model_loop'
                 OR binding.session_id IS DISTINCT FROM event.session_id
                 OR binding.org_id IS DISTINCT FROM event.org_id
                 OR binding.user_id IS DISTINCT FROM event.user_id
             )
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_OUTBOX_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    INSERT INTO agent_projection_outbox(
        event_id,session_id,org_id,user_id,projection_kind
    ) VALUES(event.id,event.session_id,event.org_id,event.user_id,'wecom')
    ON CONFLICT(event_id,projection_kind) DO NOTHING RETURNING * INTO delivery;
    IF delivery.id IS NULL THEN
        SELECT * INTO delivery FROM agent_projection_outbox
         WHERE event_id=event.id AND projection_kind='wecom';
    END IF;
    IF delivery.session_id IS DISTINCT FROM event.session_id
       OR delivery.org_id IS DISTINCT FROM event.org_id
       OR delivery.user_id IS DISTINCT FROM event.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_OUTBOX_CONFLICT'
            USING ERRCODE='23505';
    END IF;
    IF lane='image' THEN
        INSERT INTO agent_runtime_media_image_wecom_outbox_facts_v1(
            source_outbox_id,delivery_outbox_id,event_id,session_id,run_id,
            anchor_action_id,parent_task_id,binding_count,org_id,user_id
        ) VALUES(NEW.id,delivery.id,event.id,event.session_id,event.run_id,
            anchor_action_id,parent_task.id,image_count,event.org_id,event.user_id)
        ON CONFLICT(source_outbox_id) DO NOTHING RETURNING * INTO image_fact;
        IF image_fact.source_outbox_id IS NULL THEN
            SELECT * INTO image_fact
              FROM agent_runtime_media_image_wecom_outbox_facts_v1
             WHERE source_outbox_id=NEW.id;
        END IF;
        IF image_fact.delivery_outbox_id IS DISTINCT FROM delivery.id
           OR image_fact.run_id IS DISTINCT FROM event.run_id
           OR image_fact.anchor_action_id IS DISTINCT FROM anchor_action_id
           OR image_fact.binding_count IS DISTINCT FROM image_count THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_OUTBOX_FACT_CONFLICT'
                USING ERRCODE='23505';
        END IF;
    ELSE
        INSERT INTO agent_runtime_media_wecom_outbox_facts_v1(
            source_outbox_id,delivery_outbox_id,event_id,session_id,run_id,
            anchor_action_id,org_id,user_id
        ) VALUES(NEW.id,delivery.id,event.id,event.session_id,event.run_id,
            anchor_action_id,event.org_id,event.user_id)
        ON CONFLICT(source_outbox_id) DO NOTHING RETURNING * INTO video_fact;
        IF video_fact.source_outbox_id IS NULL THEN
            SELECT * INTO video_fact FROM agent_runtime_media_wecom_outbox_facts_v1
             WHERE source_outbox_id=NEW.id;
        END IF;
        IF video_fact.delivery_outbox_id IS DISTINCT FROM delivery.id
           OR video_fact.anchor_action_id IS DISTINCT FROM anchor_action_id THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_OUTBOX_FACT_CONFLICT'
                USING ERRCODE='23505';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER agent_runtime_media_model_video_wecom_outbox_v1
    ON agent_projection_outbox;
CREATE TRIGGER agent_runtime_media_model_media_wecom_outbox_v2
AFTER INSERT ON agent_projection_outbox
FOR EACH ROW EXECUTE FUNCTION _derive_agent_runtime_model_media_wecom_outbox_v2();

REVOKE ALL ON TABLE agent_runtime_media_image_wecom_outbox_facts_v1
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION _derive_agent_runtime_model_media_wecom_outbox_v2()
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;

RESET ROLE;
