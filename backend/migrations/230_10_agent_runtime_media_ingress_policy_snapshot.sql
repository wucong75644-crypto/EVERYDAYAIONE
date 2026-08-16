-- 230.10: bind direct media ingress actions to the frozen tool policy.
SET LOCAL ROLE everydayai_owner;

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
    prepared_task tasks%ROWTYPE;
    session_result JSONB;
    runtime_result JSONB;
    safe_arguments JSONB;
    readiness JSONB;
    runtime_action agent_actions%ROWTYPE;
    runtime_session agent_runtime_sessions%ROWTYPE;
    prepared_binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
    prepared_result JSONB;
    tool_fact JSONB;
    effective_toolset_hash TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_tool_name NOT IN ('generate_image','generate_video')
       OR jsonb_typeof(p_arguments)<>'object'
       OR NULLIF(btrim(p_idempotency_key),'') IS NULL
       OR p_task_id IS NULL OR p_input_message_id IS NULL
       OR p_output_message_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO prepared_task FROM tasks
     WHERE id=p_task_id AND conversation_id=p_conversation_id
       AND user_id=p_user_id AND org_id IS NOT DISTINCT FROM p_org_id
       AND type::TEXT=(CASE WHEN p_tool_name='generate_image' THEN 'image' ELSE 'video' END)
       AND input_message_id=p_input_message_id
       AND assistant_message_id=p_output_message_id
     FOR UPDATE;
    IF prepared_task.id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TASK_SCOPE_MISMATCH' USING ERRCODE='42501';
    END IF;
    IF COALESCE((prepared_task.delivery_context->>'runtime')::BOOLEAN,FALSE) THEN
        IF prepared_task.delivery_context->>'runtime_owner' IS DISTINCT FROM 'action_loop'
           OR COALESCE(pg_input_is_valid(prepared_task.delivery_context->>'runtime_action_id','uuid'),FALSE) IS NOT TRUE
           OR COALESCE(pg_input_is_valid(prepared_task.delivery_context->>'runtime_run_id','uuid'),FALSE) IS NOT TRUE THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TASK_SCOPE_MISMATCH' USING ERRCODE='42501';
        END IF;
        SELECT * INTO prepared_binding
          FROM agent_runtime_prepared_media_action_bindings
         WHERE task_id=prepared_task.id
           AND action_id=(prepared_task.delivery_context->>'runtime_action_id')::UUID;
        IF prepared_binding.action_id IS NOT NULL THEN
            RETURN jsonb_build_object(
                'outcome','already_exists','runtime_owned',TRUE,
                'action_id',prepared_task.delivery_context->>'runtime_action_id',
                'run_id',prepared_task.delivery_context->>'runtime_run_id',
                'readiness_revision',prepared_task.delivery_context->>'runtime_media_readiness_revision');
        END IF;
    END IF;
    readiness:=_agent_runtime_media_owner_readiness_v1();
    IF (readiness->>'ready')::BOOLEAN IS NOT TRUE THEN
        RETURN jsonb_build_object('outcome','media_not_ready','runtime_owned',FALSE,
            'readiness_revision',(readiness->>'state_version')::BIGINT);
    END IF;
    safe_arguments:=jsonb_strip_nulls(CASE WHEN p_tool_name='generate_image'
        THEN jsonb_build_object('prompt',p_arguments->'prompt','model',p_arguments->'model',
            'aspect_ratio',p_arguments->'aspect_ratio','resolution',p_arguments->'resolution',
            'output_format',p_arguments->'output_format')
        ELSE jsonb_build_object('prompt',p_arguments->'prompt','model',p_arguments->'model',
            'aspect_ratio',p_arguments->'aspect_ratio','n_frames',p_arguments->'n_frames',
            'remove_watermark',p_arguments->'remove_watermark') END);
    IF NULLIF(btrim(safe_arguments->>'prompt'),'') IS NULL
       AND p_tool_name<>'generate_video' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT item.value, fact.effective_toolset_hash
      INTO tool_fact, effective_toolset_hash
      FROM agent_runtime_effective_toolset_facts fact
      CROSS JOIN LATERAL jsonb_array_elements(fact.toolset_document->'tools') item(value)
     WHERE fact.agent_key=p_agent_definition_id
       AND fact.definition_revision=p_agent_definition_revision
       AND fact.catalog_revision=p_catalog_revision
       AND fact.scope_kind=p_scope_kind
       AND fact.channel=COALESCE(NULLIF(prepared_task.delivery_context->>'channel',''),'web')
       AND fact.gate_state='enabled' AND fact.enabled_for_new_ingress AND fact.recoverable
       AND item.value->>'canonical_name'=p_tool_name
     LIMIT 1;
    IF tool_fact IS NULL OR effective_toolset_hash IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TOOL_FACT_MISSING' USING ERRCODE='55000';
    END IF;
    session_result:=ensure_agent_runtime_session(
        p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
        p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision);
    IF session_result->>'outcome' NOT IN ('created','already_exists') THEN
        RETURN session_result||jsonb_build_object('runtime_owned',FALSE);
    END IF;
    runtime_result:=submit_agent_runtime_chat_action_v1(
        p_conversation_id,p_org_id,p_user_id,p_task_id::TEXT,
        p_input_message_id::TEXT,p_task_id::TEXT,1,p_tool_name,safe_arguments,
        p_model_id,p_model_provider,p_model_revision,p_catalog_revision,
        p_policy_revision,'runtime_media_generation:'||p_tool_name,1,
        jsonb_strip_nulls(jsonb_build_object(
            'source','media_ingress','task_id',p_task_id,
            'input_message_id',p_input_message_id,'output_message_id',p_output_message_id,
            'turn_id',p_turn_id,'provider','kie','provider_revision','kie-runtime-media-v1',
            'capability','media.provider.submit','capability_revision',tool_fact->>'schema_hash',
            'capability_requirements',tool_fact->'capability_requirements',
            'safety_level',tool_fact->>'safety_level','side_effect',tool_fact->>'side_effect',
            'authorization_requirement',tool_fact->>'authorization_requirement',
            'schema_hash',tool_fact->>'schema_hash','executor_type',tool_fact->>'executor_type',
            'executor_revision',(tool_fact->>'executor_revision')::INTEGER,
            'catalog_revision',p_catalog_revision,'effective_toolset_hash',effective_toolset_hash)),
        jsonb_build_object('source','media_ingress','task_id',p_task_id,
            'input_message_id',p_input_message_id,'output_message_id',p_output_message_id,
            'turn_id',p_turn_id),p_idempotency_key);
    IF runtime_result->>'outcome' IN ('created','already_exists') THEN
        UPDATE tasks SET delivery_context=delivery_context||jsonb_build_object(
            'actor',FALSE,'runtime',TRUE,'runtime_owner','action_loop',
            'runtime_action_id',runtime_result->>'action_id',
            'runtime_run_id',runtime_result->>'run_id',
            'runtime_media_readiness_revision',(readiness->>'state_version')::BIGINT)
         WHERE id=p_task_id;
        SELECT * INTO runtime_action FROM agent_actions
         WHERE id=(runtime_result->>'action_id')::UUID;
        SELECT * INTO runtime_session FROM agent_runtime_sessions
         WHERE id=runtime_action.session_id;
        IF runtime_action.id IS NULL OR runtime_session.id IS NULL
           OR runtime_action.run_id IS DISTINCT FROM (runtime_result->>'run_id')::UUID THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_BINDING_INVALID' USING ERRCODE='55000';
        END IF;
        prepared_result:=_prepare_agent_runtime_prepared_media_binding_v1(
            jsonb_build_object('action_id',runtime_action.id,'session_id',runtime_session.id,
                'run_id',runtime_action.run_id,'model_step_id',runtime_action.model_step_id,
                'org_id',runtime_session.org_id,'user_id',runtime_session.user_id,
                'conversation_id',runtime_session.conversation_id,'tool_name',runtime_action.tool_name,
                'source','media_ingress','task_id',p_task_id,
                'input_message_id',p_input_message_id,'output_message_id',p_output_message_id),
            runtime_action.request_hash);
        IF prepared_result->>'outcome' NOT IN ('prepared','already_prepared') THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_BINDING_INVALID' USING ERRCODE='55000';
        END IF;
        RETURN runtime_result||jsonb_build_object('runtime_owned',TRUE,
            'readiness_revision',(readiness->>'state_version')::BIGINT);
    END IF;
    RETURN runtime_result||jsonb_build_object('runtime_owned',FALSE);
END;
$$;

RESET ROLE;
