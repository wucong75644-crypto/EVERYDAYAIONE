-- Roll back 228.05 while retaining the complete 228.04 contract.
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_prepared_media_action_bindings) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_05_PREPARED_BINDINGS_IN_USE'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE OR REPLACE FUNCTION submit_agent_runtime_media_action_v1(
    p_conversation_id UUID,p_org_id UUID,p_user_id UUID,
    p_scope_kind TEXT,p_scope_id TEXT,p_created_by_user_id UUID,
    p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
    p_task_id UUID,p_input_message_id UUID,p_output_message_id UUID,
    p_turn_id UUID,p_tool_name TEXT,p_arguments JSONB,p_model_id TEXT,
    p_model_provider TEXT,p_model_revision TEXT,p_catalog_revision TEXT,
    p_policy_revision TEXT,p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    t tasks%ROWTYPE;
    s JSONB;
    r JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_tool_name NOT IN ('generate_image','generate_video')
       OR jsonb_typeof(p_arguments) IS DISTINCT FROM 'object'
       OR NULLIF(btrim(p_idempotency_key),'') IS NULL
       OR p_task_id IS NULL OR p_input_message_id IS NULL
       OR p_output_message_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO t FROM tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND OR t.conversation_id IS DISTINCT FROM p_conversation_id
       OR t.user_id IS DISTINCT FROM p_user_id
       OR t.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TASK_SCOPE_MISMATCH' USING ERRCODE='42501';
    END IF;
    s:=ensure_agent_runtime_session(
        p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
        p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision
    );
    IF s->>'outcome' NOT IN ('created','already_exists') THEN
        RETURN s||jsonb_build_object('runtime_owned',FALSE);
    END IF;
    r:=submit_agent_runtime_chat_action_v1(
        p_conversation_id,p_org_id,p_user_id,p_task_id::TEXT,
        p_input_message_id::TEXT,p_task_id::TEXT,1,p_tool_name,p_arguments,
        p_model_id,p_model_provider,p_model_revision,p_catalog_revision,
        p_policy_revision,'runtime_media_generation:'||p_tool_name,1,
        jsonb_build_object('source','media_ingress','task_id',p_task_id,
                           'output_message_id',p_output_message_id,'turn_id',p_turn_id),
        jsonb_build_object('source','media_ingress','task_id',p_task_id,
                           'input_message_id',p_input_message_id,
                           'output_message_id',p_output_message_id,'turn_id',p_turn_id),
        p_idempotency_key
    );
    IF r->>'outcome' IN ('created','already_exists') THEN
        UPDATE tasks SET delivery_context=delivery_context||jsonb_build_object(
          'actor',FALSE,'runtime',TRUE,'runtime_action_id',r->>'action_id',
          'runtime_run_id',r->>'run_id') WHERE id=p_task_id;
        RETURN r||jsonb_build_object('runtime_owned',TRUE);
    END IF;
    RETURN r||jsonb_build_object('runtime_owned',FALSE);
END $$;

CREATE OR REPLACE FUNCTION worker_discover_media_tasks(p_limit INTEGER DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE discovered JSONB;
BEGIN
    IF session_user<>'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501';
    END IF;
    IF p_limit IS NULL OR p_limit<1 OR p_limit>500 THEN
        RAISE EXCEPTION 'MEDIA_WORKER_LIMIT_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(task_row)),'[]'::JSONB) INTO discovered
    FROM (
      SELECT task.* FROM tasks task
       WHERE task.status IN ('pending','running') AND task.type IN ('image','video')
         AND COALESCE((task.delivery_context->>'runtime')::BOOLEAN,FALSE) IS FALSE
         AND NOT EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding
                         WHERE binding.task_id=task.id)
         AND (task.org_id IS NULL OR EXISTS (
           SELECT 1 FROM organizations organization
            WHERE organization.id=task.org_id AND organization.status='active'))
       ORDER BY COALESCE(task.last_polled_at,task.created_at),task.id
       LIMIT p_limit
    ) task_row;
    RETURN discovered;
END $$;

REVOKE ALL ON FUNCTION
    prepare_agent_runtime_media_dispatch_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT),
    read_agent_runtime_media_provider_request_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT),
    get_agent_runtime_media_configuration_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT)
FROM everydayai_agent_runtime_worker,PUBLIC;

DROP FUNCTION get_agent_runtime_media_configuration_v1(
    UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT);
DROP FUNCTION read_agent_runtime_media_provider_request_v1(
    UUID,UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION prepare_agent_runtime_media_dispatch_v1(
    UUID,UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION _prepare_agent_runtime_prepared_media_binding_v1(JSONB,TEXT);
DROP FUNCTION _agent_runtime_kie_provider_request_v1(TEXT,JSONB,JSONB);
DROP FUNCTION _agent_runtime_media_resolved_images_v1(UUID,UUID);
DROP FUNCTION _agent_runtime_media_attempt_context_v2(
    UUID,UUID,TEXT,UUID,BIGINT,TEXT);

DROP TABLE agent_runtime_prepared_media_action_bindings;
DROP TABLE agent_runtime_prepared_media_video_pricing_facts;

RESET ROLE;
