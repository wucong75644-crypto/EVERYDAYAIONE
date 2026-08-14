/* 228.08b: Runtime-media WeCom terminal delivery stays inside Projection. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regclass('public.agent_runtime_media_projection_results') IS NULL
       OR to_regprocedure(
           'apply_agent_runtime_media_projection_v1(uuid,uuid,text,jsonb)'
       ) IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_228_06_REQUIRED'
            USING ERRCODE='55000';
    END IF;
    IF to_regclass('public.conversation_deliveries') IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_attribute
            WHERE attrelid='public.conversation_deliveries'::regclass
              AND attname='delivery_kind' AND NOT attisdropped
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_WECOM_DELIVERY_CONTRACT_REQUIRED'
            USING ERRCODE='55000';
    END IF;
    IF to_regprocedure(
           '_project_agent_runtime_media_wecom_delivery_v1()'
       ) IS NOT NULL
       OR EXISTS (
           SELECT 1 FROM pg_trigger
            WHERE tgrelid='public.agent_runtime_media_projection_results'::regclass
              AND tgname='agent_runtime_media_wecom_delivery_v1'
              AND NOT tgisinternal
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_IDENTITY_CONFLICT'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE FUNCTION _project_agent_runtime_media_wecom_delivery_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    v_outbox agent_projection_outbox%ROWTYPE;
    v_event agent_runtime_events%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;
    v_delivery conversation_deliveries%ROWTYPE;
    v_task_id UUID:=NEW.task_id;
    v_candidate_count INTEGER;
    v_expected_action TEXT;
    v_expected_status TEXT;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    IF NEW.projection_kind<>'wecom' THEN
        RETURN NEW;
    END IF;

    SELECT * INTO v_outbox FROM agent_projection_outbox
     WHERE id=NEW.outbox_id;
    SELECT * INTO v_event FROM agent_runtime_events
     WHERE id=NEW.event_id;
    IF v_outbox.id IS NULL OR v_event.id IS NULL
       OR v_outbox.status<>'processing'
       OR v_outbox.event_id IS DISTINCT FROM v_event.id
       OR v_outbox.session_id IS DISTINCT FROM NEW.session_id
       OR v_outbox.org_id IS DISTINCT FROM NEW.org_id
       OR v_outbox.user_id IS DISTINCT FROM NEW.user_id
       OR v_outbox.projection_kind IS DISTINCT FROM NEW.projection_kind
       OR v_event.session_id IS DISTINCT FROM NEW.session_id
       OR v_event.org_id IS DISTINCT FROM NEW.org_id
       OR v_event.user_id IS DISTINCT FROM NEW.user_id
       OR v_event.sequence IS DISTINCT FROM NEW.event_sequence THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_PROJECTION_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    IF v_event.event_type NOT IN (
        'run.completed','run.failed','run.cancelled'
    ) THEN
        RETURN NEW;
    END IF;

    v_expected_action:=CASE v_event.event_type
        WHEN 'run.completed' THEN 'run_completed'
        WHEN 'run.failed' THEN 'run_failed'
        WHEN 'run.cancelled' THEN 'run_cancelled'
    END;
    v_expected_status:=substring(v_event.event_type FROM 5);
    IF v_event.run_id IS NULL OR v_event.action_id IS NOT NULL
       OR NEW.action_id IS NOT NULL
       OR NEW.projection_action NOT IN (v_expected_action,'checkpoint_only') THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_TERMINAL_EVENT_INVALID'
            USING ERRCODE='55000';
    END IF;

    SELECT * INTO v_run FROM agent_runs WHERE id=v_event.run_id;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id=v_event.session_id;
    IF v_run.id IS NULL OR v_session.id IS NULL
       OR v_run.session_id IS DISTINCT FROM v_event.session_id
       OR v_run.org_id IS DISTINCT FROM NEW.org_id
       OR v_run.user_id IS DISTINCT FROM NEW.user_id
       OR v_run.status::TEXT IS DISTINCT FROM v_expected_status
       OR v_session.org_id IS DISTINCT FROM NEW.org_id
       OR v_session.user_id IS DISTINCT FROM NEW.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_RUN_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;

    IF v_task_id IS NULL THEN
        SELECT count(*), (array_agg(candidate.task_id ORDER BY candidate.task_id))[1]
          INTO v_candidate_count,v_task_id
          FROM (
              SELECT binding.task_id
                FROM agent_runtime_media_action_bindings binding
               WHERE binding.run_id=v_event.run_id
              UNION
              SELECT binding.task_id
                FROM agent_runtime_prepared_media_action_bindings binding
               WHERE binding.run_id=v_event.run_id
          ) candidate;
        IF v_candidate_count<>1 THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_TASK_AMBIGUOUS'
                USING ERRCODE='55000';
        END IF;
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id=v_task_id;
    SELECT * INTO v_message FROM messages
     WHERE id=v_task.assistant_message_id;
    IF v_task.id IS NULL OR v_message.id IS NULL
       OR v_task.org_id IS DISTINCT FROM NEW.org_id
       OR v_task.user_id IS DISTINCT FROM NEW.user_id
       OR v_task.conversation_id IS DISTINCT FROM v_session.conversation_id
       OR v_message.id IS DISTINCT FROM v_task.assistant_message_id
       OR v_message.org_id IS DISTINCT FROM NEW.org_id
       OR v_message.conversation_id IS DISTINCT FROM v_task.conversation_id
       OR v_message.role::TEXT<>'assistant'
       OR (NEW.message_id IS NOT NULL
           AND NEW.message_id IS DISTINCT FROM v_message.id)
       OR v_task.status::TEXT IS DISTINCT FROM v_expected_status
       OR v_task.delivery_context @> '{"actor":false,"runtime":true,"channel":"wecom"}'::JSONB
          IS NOT TRUE
       OR NOT (
           EXISTS (
               SELECT 1 FROM agent_runtime_media_action_bindings binding
                WHERE binding.run_id=v_event.run_id
                  AND v_task.id IN (binding.task_id,binding.chat_task_id)
           )
           OR EXISTS (
               SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
                WHERE binding.run_id=v_event.run_id
                  AND binding.task_id=v_task.id
           )
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_TASK_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;

    /* The caller already owns task -> bindings -> message. Take no row locks here. */
    INSERT INTO conversation_deliveries(
        task_id,channel,delivery_kind,target_context
    ) VALUES(
        v_task.id,'wecom','assistant_terminal',v_task.delivery_context
    ) ON CONFLICT (task_id,channel,delivery_kind) DO NOTHING
    RETURNING * INTO v_delivery;
    IF v_delivery.id IS NULL THEN
        SELECT * INTO v_delivery FROM conversation_deliveries
         WHERE task_id=v_task.id AND channel='wecom'
           AND delivery_kind='assistant_terminal';
    END IF;
    IF v_delivery.id IS NULL
       OR v_delivery.target_context IS DISTINCT FROM v_task.delivery_context THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_CONFLICT'
            USING ERRCODE='23505';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER agent_runtime_media_wecom_delivery_v1
AFTER INSERT ON agent_runtime_media_projection_results
FOR EACH ROW
WHEN (NEW.projection_kind='wecom')
EXECUTE FUNCTION _project_agent_runtime_media_wecom_delivery_v1();

REVOKE ALL ON FUNCTION _project_agent_runtime_media_wecom_delivery_v1()
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;

RESET ROLE;
