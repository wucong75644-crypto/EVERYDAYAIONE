/* 228.08a: Bind ordinary ModelLoop generate_video Actions to Runtime media. */
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION _prepare_agent_runtime_model_video_v1(
    p_context JSONB,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    action agent_actions%ROWTYPE;
    attempt agent_action_attempts%ROWTYPE;
    runtime_session agent_runtime_sessions%ROWTYPE;
    runtime_run agent_runs%ROWTYPE;
    step agent_model_steps%ROWTYPE;
    command agent_session_commands%ROWTYPE;
    chat_task tasks%ROWTYPE;
    child_task tasks%ROWTYPE;
    input_message messages%ROWTYPE;
    output_message messages%ROWTYPE;
    binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
    pricing agent_runtime_prepared_media_video_pricing_facts%ROWTYPE;
    resolved JSONB;
    reference_urls JSONB;
    request_params JSONB;
    provider_request JSONB;
    provider_hash TEXT;
    task_hash TEXT;
    duration_seconds INTEGER;
    final_balance INTEGER;
    transaction_id UUID;
BEGIN
    PERFORM _agent_runtime_media_worker_v1();
    IF jsonb_typeof(p_context) IS DISTINCT FROM 'object'
       OR p_context->>'source' NOT IN ('model_loop','runtime_executor_registry')
       OR p_context->>'tool_name' IS DISTINCT FROM 'generate_video'
       OR COALESCE(p_request_hash,'') !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO action FROM agent_actions
     WHERE id=NULLIF(p_context->>'action_id','')::UUID;
    IF action.id IS NULL THEN
        RETURN jsonb_build_object('outcome','not_found');
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'runtime-model-video:'||action.model_step_id::TEXT,0
    ));
    SELECT * INTO runtime_session FROM agent_runtime_sessions
     WHERE id=action.session_id FOR UPDATE;
    SELECT * INTO runtime_run FROM agent_runs
     WHERE id=action.run_id FOR UPDATE;
    SELECT * INTO step FROM agent_model_steps
     WHERE id=action.model_step_id FOR UPDATE;
    PERFORM id FROM agent_actions WHERE model_step_id=step.id
     ORDER BY action_index,id FOR UPDATE;
    SELECT * INTO action FROM agent_actions WHERE id=action.id;
    SELECT * INTO attempt FROM agent_action_attempts
     WHERE id=NULLIF(p_context->>'attempt_id','')::UUID FOR UPDATE;
    SELECT * INTO command FROM agent_session_commands
     WHERE id=runtime_run.command_id FOR UPDATE;
    SELECT * INTO chat_task FROM tasks
     WHERE id=NULLIF(command.payload->>'task_id','')::UUID FOR UPDATE;
    PERFORM id FROM messages WHERE id IN (
        NULLIF(command.payload->>'input_message_id','')::UUID,
        NULLIF(command.payload->>'output_message_id','')::UUID
    ) ORDER BY id FOR UPDATE;
    SELECT * INTO input_message FROM messages
     WHERE id=NULLIF(command.payload->>'input_message_id','')::UUID;
    SELECT * INTO output_message FROM messages
     WHERE id=NULLIF(command.payload->>'output_message_id','')::UUID;
    SELECT * INTO binding FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=action.id;
    IF runtime_session.id IS NULL OR runtime_run.id IS NULL OR step.id IS NULL
       OR command.id IS NULL OR chat_task.id IS NULL OR input_message.id IS NULL
       OR output_message.id IS NULL OR attempt.id IS NULL
       OR action.id IS DISTINCT FROM (p_context->>'action_id')::UUID
       OR action.session_id IS DISTINCT FROM runtime_session.id
       OR action.run_id IS DISTINCT FROM runtime_run.id
       OR action.model_step_id IS DISTINCT FROM step.id
       OR runtime_run.session_id IS DISTINCT FROM runtime_session.id
       OR step.session_id IS DISTINCT FROM runtime_session.id
       OR step.run_id IS DISTINCT FROM runtime_run.id
       OR command.session_id IS DISTINCT FROM runtime_session.id
       OR runtime_run.command_id IS DISTINCT FROM command.id
       OR action.org_id IS DISTINCT FROM runtime_session.org_id
       OR action.user_id IS DISTINCT FROM runtime_session.user_id
       OR runtime_run.org_id IS DISTINCT FROM runtime_session.org_id
       OR runtime_run.user_id IS DISTINCT FROM runtime_session.user_id
       OR step.org_id IS DISTINCT FROM runtime_session.org_id
       OR step.user_id IS DISTINCT FROM runtime_session.user_id
       OR attempt.action_id IS DISTINCT FROM action.id
       OR attempt.session_id IS DISTINCT FROM runtime_session.id
       OR attempt.run_id IS DISTINCT FROM runtime_run.id
       OR chat_task.user_id IS DISTINCT FROM runtime_session.user_id
       OR chat_task.org_id IS DISTINCT FROM runtime_session.org_id
       OR chat_task.conversation_id IS DISTINCT FROM runtime_session.conversation_id
       OR chat_task.type::TEXT IS DISTINCT FROM 'chat'
       OR chat_task.input_message_id IS DISTINCT FROM input_message.id
       OR chat_task.assistant_message_id IS DISTINCT FROM output_message.id
       OR input_message.conversation_id IS DISTINCT FROM runtime_session.conversation_id
       OR output_message.conversation_id IS DISTINCT FROM runtime_session.conversation_id
       OR input_message.org_id IS DISTINCT FROM runtime_session.org_id
       OR output_message.org_id IS DISTINCT FROM runtime_session.org_id
       OR input_message.role::TEXT IS DISTINCT FROM 'user'
       OR output_message.role::TEXT IS DISTINCT FROM 'assistant'
       OR step.status IS DISTINCT FROM 'completed'
       OR step.stop_reason IS DISTINCT FROM 'tool_calls'
       OR action.tool_name IS DISTINCT FROM 'generate_video'
       OR action.action_index IS DISTINCT FROM 0
       OR action.request_hash IS DISTINCT FROM p_request_hash
       OR (SELECT count(*) FROM agent_actions
            WHERE model_step_id=step.id) IS DISTINCT FROM 1::BIGINT THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    IF jsonb_typeof(action.arguments) IS DISTINCT FROM 'object'
       OR action.arguments - ARRAY['prompt','duration'] <> '{}'::JSONB
       OR NULLIF(btrim(action.arguments->>'prompt'),'') IS NULL
       OR length(action.arguments->>'prompt')>20000
       OR (action.arguments ? 'duration' AND (
           jsonb_typeof(action.arguments->'duration') IS DISTINCT FROM 'number'
           OR (action.arguments->>'duration') !~ '^[0-9]+$'
       )) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_ARGUMENTS_INVALID'
            USING ERRCODE='22023';
    END IF;
    duration_seconds:=COALESCE((action.arguments->>'duration')::INTEGER,10);
    IF duration_seconds NOT IN (10,15) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_DURATION_INVALID'
            USING ERRCODE='22023';
    END IF;
    request_params:=jsonb_build_object(
        'prompt',btrim(action.arguments->>'prompt'),
        'model','sora-2-text-to-video',
        'duration',duration_seconds,
        'aspect_ratio','landscape',
        'remove_watermark',TRUE
    );
    resolved:=_agent_runtime_media_resolved_images_v1(
        runtime_session.id,input_message.id
    );
    reference_urls:=(
        SELECT COALESCE(jsonb_agg(image->'url' ORDER BY
            (image->>'index')::INTEGER),'[]'::JSONB)
          FROM jsonb_array_elements(resolved->'images') image
    );
    provider_request:=_agent_runtime_kie_provider_request_v1(
        'video',request_params,reference_urls
    );
    provider_hash:=encode(digest(convert_to(
        provider_request::TEXT,'UTF8'
    ),'sha256'),'hex');
    task_hash:=encode(digest(convert_to(
        request_params::TEXT,'UTF8'
    ),'sha256'),'hex');
    SELECT * INTO pricing FROM agent_runtime_prepared_media_video_pricing_facts price
     WHERE price.pricing_revision='kie-video-pricing-v1'
       AND price.model_id=provider_request->>'model'
       AND price.duration_seconds=(provider_request#>>'{input,n_frames}')::INTEGER
       AND price.active;
    IF pricing.model_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PRICING_UNAVAILABLE'
            USING ERRCODE='22023';
    END IF;
    IF binding.action_id IS NOT NULL THEN
        SELECT * INTO child_task FROM tasks WHERE id=binding.task_id;
        IF child_task.id IS NULL OR binding.task_id IS DISTINCT FROM action.id
           OR binding.session_id IS DISTINCT FROM runtime_session.id
           OR binding.run_id IS DISTINCT FROM runtime_run.id
           OR binding.model_step_id IS DISTINCT FROM step.id
           OR binding.org_id IS DISTINCT FROM runtime_session.org_id
           OR binding.user_id IS DISTINCT FROM runtime_session.user_id
           OR binding.conversation_id IS DISTINCT FROM runtime_session.conversation_id
           OR binding.input_message_id IS DISTINCT FROM input_message.id
           OR binding.output_message_id IS DISTINCT FROM output_message.id
           OR binding.media_kind IS DISTINCT FROM 'video'
           OR binding.action_request_hash IS DISTINCT FROM p_request_hash
           OR binding.task_request_hash IS DISTINCT FROM task_hash
           OR binding.reference_manifest_hash IS DISTINCT FROM resolved->>'manifest_hash'
           OR binding.provider_request_hash IS DISTINCT FROM provider_hash
           OR binding.pricing_revision IS DISTINCT FROM pricing.pricing_revision
           OR binding.pricing_model_id IS DISTINCT FROM pricing.model_id
           OR binding.pricing_key IS DISTINCT FROM pricing.duration_seconds::TEXT
           OR binding.pricing_fact_hash IS DISTINCT FROM pricing.fact_hash
           OR binding.unit_credits IS DISTINCT FROM pricing.user_credits
           OR child_task.user_id IS DISTINCT FROM runtime_session.user_id
           OR child_task.org_id IS DISTINCT FROM runtime_session.org_id
           OR child_task.conversation_id IS DISTINCT FROM runtime_session.conversation_id
           OR child_task.input_message_id IS DISTINCT FROM input_message.id
           OR child_task.assistant_message_id IS DISTINCT FROM output_message.id
           OR child_task.credit_transaction_id IS DISTINCT FROM
              binding.credit_transaction_id
           OR child_task.delivery_context @> jsonb_build_object(
              'actor',FALSE,'runtime',TRUE,
              'runtime_action_id',action.id::TEXT
           ) IS NOT TRUE THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_BINDING_CONFLICT'
                USING ERRCODE='23505';
        END IF;
        IF attempt.status IN ('accepted','unknown') OR
           action.status IN ('accepted','unknown') THEN RAISE EXCEPTION
           'AGENT_RUNTIME_MODEL_VIDEO_READBACK_ONLY' USING ERRCODE='55000'; END IF;
        RETURN jsonb_build_object('outcome','already_prepared');
    END IF;
    IF attempt.status IN ('accepted','unknown')
       OR action.status IN ('accepted','unknown') THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_READBACK_ONLY'
            USING ERRCODE='55000';
    END IF;
    IF attempt.status NOT IN ('claimed','dispatching')
       OR action.status IS DISTINCT FROM 'running'
       OR EXISTS (SELECT 1 FROM tasks WHERE id=action.id) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_DISPATCH_INVALID'
            USING ERRCODE='42501';
    END IF;
    UPDATE users SET credits=credits-pricing.user_credits,
        updated_at=clock_timestamp()
     WHERE id=runtime_session.user_id AND status::TEXT='active'
       AND credits>=pricing.user_credits
     RETURNING credits INTO final_balance;
    IF final_balance IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INSUFFICIENT_CREDITS'
            USING ERRCODE='P0001';
    END IF;
    transaction_id:=gen_random_uuid();
    INSERT INTO credit_transactions(
        id,task_id,user_id,amount,type,status,reason,org_id
    ) VALUES(
        transaction_id,action.id,runtime_session.user_id,pricing.user_credits,
        'lock','pending','Agent Runtime model video reservation',
        runtime_session.org_id
    );
    INSERT INTO credits_history(
        user_id,change_type,change_amount,balance_after,description,org_id
    ) VALUES(
        runtime_session.user_id,'image_generation_cost'::credits_change_type,
        -pricing.user_credits,final_balance,
        'Agent Runtime model video reservation',runtime_session.org_id
    );
    INSERT INTO tasks(
        id,client_task_id,user_id,org_id,conversation_id,type,status,
        credits_locked,credits_used,model_id,placeholder_message_id,
        assistant_message_id,request_params,placeholder_created_at,
        input_message_id,turn_id,base_context_revision,
        context_through_message_id,execution_mode,delivery_context,
        image_index,batch_id,credit_transaction_id
    ) VALUES(
        action.id,'runtime-model-video:'||action.id,runtime_session.user_id,
        runtime_session.org_id,runtime_session.conversation_id,'video',
        'preparing',pricing.user_credits,0,pricing.model_id,
        output_message.id::TEXT,output_message.id,request_params,
        clock_timestamp(),input_message.id,chat_task.turn_id,
        chat_task.base_context_revision,chat_task.context_through_message_id,
        'serial',jsonb_build_object(
            'channel',COALESCE(runtime_run.capability_snapshot->>'channel','web'),
            'actor',FALSE,'runtime',TRUE,'runtime_owner','action_loop',
            'runtime_session_id',runtime_session.id,
            'runtime_command_id',command.id,'runtime_run_id',runtime_run.id,
            'runtime_action_id',action.id
        ),0,action.batch_hash,transaction_id
    );
    INSERT INTO agent_runtime_prepared_media_action_bindings(
        action_id,task_id,session_id,run_id,model_step_id,org_id,user_id,
        conversation_id,input_message_id,output_message_id,media_kind,
        action_request_hash,task_request_hash,reference_manifest_hash,
        provider_request_hash,pricing_revision,pricing_model_id,pricing_key,
        pricing_fact_hash,unit_credits,credit_transaction_id
    ) VALUES(
        action.id,action.id,runtime_session.id,runtime_run.id,step.id,
        runtime_session.org_id,runtime_session.user_id,
        runtime_session.conversation_id,input_message.id,output_message.id,
        'video',p_request_hash,task_hash,resolved->>'manifest_hash',
        provider_hash,pricing.pricing_revision,pricing.model_id,
        pricing.duration_seconds::TEXT,pricing.fact_hash,
        pricing.user_credits,transaction_id
    );
    UPDATE messages SET
        content=(SELECT COALESCE(jsonb_agg(part ORDER BY ordinality)
            FILTER (WHERE part->>'slot_id' IS DISTINCT FROM action.id::TEXT),
            '[]'::JSONB)
            FROM jsonb_array_elements(output_message.content::JSONB)
                 WITH ORDINALITY source(part,ordinality))
            ||jsonb_build_array(jsonb_build_object(
                'type','video','url',NULL,'slot_id',action.id,
                'slot_index',0,'slot_status','pending','slot_revision',0
            )),
        status='pending',
        generation_params=COALESCE(generation_params,'{}'::JSONB)
            ||jsonb_build_object(
                'runtime_media_prepared',TRUE,
                'runtime_media_batch',jsonb_build_object(
                    'batch_hash',action.batch_hash,'slot_count',1,
                    'projection_revision',0
                )
            )
     WHERE id=output_message.id;
    RETURN jsonb_build_object(
        'outcome','prepared','task_id',action.id,
        'unit_credits',pricing.user_credits
    );
END;
$$;
CREATE OR REPLACE FUNCTION prepare_agent_runtime_media_dispatch_v1(
    p_action_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_owner_token UUID,
    p_expected_attempt_version BIGINT,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    context JSONB;
    manifest JSONB;
    prepared JSONB;
    request_fact JSONB;
BEGIN
    context:=_agent_runtime_media_attempt_context_v2(
        p_action_id,p_attempt_id,p_worker_id,p_owner_token,
        p_expected_attempt_version,p_request_hash
    );
    IF context->>'source'='media_ingress' THEN
        RETURN _prepare_agent_runtime_prepared_media_binding_v1(
            context,p_request_hash
        );
    END IF;
    IF context->>'tool_name'='generate_video' THEN
        RETURN _prepare_agent_runtime_model_video_v1(context,p_request_hash);
    END IF;
    IF context->>'tool_name'<>'generate_image' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BATCH_KIND_INVALID'
            USING ERRCODE='22023';
    END IF;
    manifest:=read_agent_runtime_media_manifest_v1(
        p_action_id,p_attempt_id,p_worker_id,p_owner_token,
        p_expected_attempt_version,p_request_hash
    );
    prepared:=prepare_agent_runtime_media_batch_v1(
        p_action_id,p_attempt_id,p_worker_id,p_owner_token,
        p_expected_attempt_version,p_request_hash,
        manifest->>'reference_manifest_hash'
    );
    request_fact:=read_agent_runtime_media_provider_request_v1(
        p_action_id,p_attempt_id,p_worker_id,p_owner_token,
        p_expected_attempt_version,p_request_hash
    );
    UPDATE agent_runtime_media_action_bindings SET
        provider_request_canonical_hash=request_fact->>'provider_request_hash',
        updated_at=clock_timestamp()
     WHERE action_id=p_action_id
       AND (provider_request_canonical_hash IS NULL
            OR provider_request_canonical_hash=
               request_fact->>'provider_request_hash');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT'
            USING ERRCODE='23505';
    END IF;
    RETURN jsonb_build_object('outcome',prepared->>'outcome');
END;
$$;
CREATE OR REPLACE FUNCTION read_agent_runtime_media_provider_request_v1(
    p_action_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_owner_token UUID,
    p_expected_attempt_version BIGINT,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    context JSONB;
    action agent_actions%ROWTYPE;
    task tasks%ROWTYPE;
    batch_binding agent_runtime_media_action_bindings%ROWTYPE;
    prepared_binding agent_runtime_prepared_media_action_bindings%ROWTYPE;
    resolved JSONB;
    selected_urls JSONB;
    indexes JSONB;
    provider_request JSONB;
    provider_hash TEXT;
    kind TEXT;
    source TEXT;
BEGIN
    context:=_agent_runtime_media_attempt_context_v2(
        p_action_id,p_attempt_id,p_worker_id,p_owner_token,
        p_expected_attempt_version,p_request_hash
    );
    SELECT * INTO action FROM agent_actions WHERE id=p_action_id;
    SELECT * INTO prepared_binding
      FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=action.id;
    kind:=CASE action.tool_name WHEN 'generate_image' THEN 'image' ELSE 'video' END;
    source:=CASE WHEN context->>'source'='media_ingress'
        THEN 'media_ingress' ELSE 'model_loop' END;
    IF prepared_binding.action_id IS NOT NULL THEN
        SELECT * INTO task FROM tasks WHERE id=prepared_binding.task_id;
        IF task.id IS NULL
           OR task.user_id IS DISTINCT FROM prepared_binding.user_id
           OR task.org_id IS DISTINCT FROM prepared_binding.org_id
           OR task.conversation_id IS DISTINCT FROM prepared_binding.conversation_id
           OR task.input_message_id IS DISTINCT FROM prepared_binding.input_message_id
           OR task.assistant_message_id IS DISTINCT FROM prepared_binding.output_message_id
           OR task.credit_transaction_id IS DISTINCT FROM
              prepared_binding.credit_transaction_id
           OR encode(digest(convert_to(task.request_params::TEXT,'UTF8'),
              'sha256'),'hex') IS DISTINCT FROM prepared_binding.task_request_hash
           OR task.delivery_context @> jsonb_build_object(
              'actor',FALSE,'runtime',TRUE,
              'runtime_action_id',action.id::TEXT
           ) IS NOT TRUE THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_PREPARED_MEDIA_BINDING_CONFLICT'
                USING ERRCODE='23505';
        END IF;
        resolved:=_agent_runtime_media_resolved_images_v1(
            action.session_id,prepared_binding.input_message_id
        );
        IF resolved->>'manifest_hash' IS DISTINCT FROM
           prepared_binding.reference_manifest_hash THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MANIFEST_CONFLICT'
                USING ERRCODE='23505';
        END IF;
        selected_urls:=(SELECT COALESCE(jsonb_agg(
            image->'url' ORDER BY (image->>'index')::INTEGER
        ),'[]'::JSONB) FROM jsonb_array_elements(resolved->'images') image);
    ELSE
        SELECT * INTO batch_binding FROM agent_runtime_media_action_bindings
         WHERE action_id=action.id;
        SELECT * INTO task FROM tasks WHERE id=batch_binding.task_id;
        IF kind<>'image' OR batch_binding.action_id IS NULL OR task.id IS NULL
           OR task.user_id IS DISTINCT FROM batch_binding.user_id
           OR task.org_id IS DISTINCT FROM batch_binding.org_id
           OR task.conversation_id IS DISTINCT FROM batch_binding.conversation_id
           OR task.input_message_id IS DISTINCT FROM batch_binding.input_message_id
           OR task.assistant_message_id IS DISTINCT FROM batch_binding.output_message_id
           OR encode(digest(convert_to(task.request_params::TEXT,'UTF8'),
              'sha256'),'hex') IS DISTINCT FROM batch_binding.provider_request_hash
           OR task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB
              IS NOT TRUE THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BINDING_CONFLICT'
                USING ERRCODE='23505';
        END IF;
        resolved:=_agent_runtime_media_resolved_images_v1(
            action.session_id,batch_binding.input_message_id
        );
        IF resolved->>'manifest_hash' IS DISTINCT FROM
           batch_binding.reference_manifest_hash THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MANIFEST_CONFLICT'
                USING ERRCODE='23505';
        END IF;
        indexes:=COALESCE(action.arguments->'reference_image_indexes','[]'::JSONB);
        IF jsonb_typeof(indexes)<>'array' OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(indexes) index_value
             WHERE jsonb_typeof(index_value)<>'number'
                OR index_value::TEXT !~ '^(0|[1-9][0-9]*)$'
                OR NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(resolved->'images') image
                     WHERE (image->>'index')::INTEGER=
                           (index_value::TEXT)::INTEGER
                )
        ) OR (SELECT count(*) FROM jsonb_array_elements(indexes)) <>
             (SELECT count(DISTINCT value::TEXT)
                FROM jsonb_array_elements(indexes)) THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_REFERENCE_INDEX_INVALID'
                USING ERRCODE='22023';
        END IF;
        selected_urls:=(SELECT COALESCE(jsonb_agg(
            found.candidate->'url' ORDER BY selected.ordinality
        ),'[]'::JSONB)
          FROM jsonb_array_elements(indexes) WITH ORDINALITY
               selected(value,ordinality)
          JOIN LATERAL (
              SELECT candidate FROM jsonb_array_elements(resolved->'images') candidate
               WHERE (candidate->>'index')::INTEGER=
                     (selected.value::TEXT)::INTEGER
          ) found ON TRUE);
    END IF;
    provider_request:=_agent_runtime_kie_provider_request_v1(
        kind,task.request_params,selected_urls
    );
    provider_hash:=encode(digest(convert_to(
        provider_request::TEXT,'UTF8'
    ),'sha256'),'hex');
    IF prepared_binding.action_id IS NOT NULL
       AND provider_hash IS DISTINCT FROM prepared_binding.provider_request_hash THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT'
            USING ERRCODE='23505';
    END IF;
    IF batch_binding.action_id IS NOT NULL
       AND batch_binding.provider_request_canonical_hash IS NOT NULL
       AND provider_hash IS DISTINCT FROM
           batch_binding.provider_request_canonical_hash THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT'
            USING ERRCODE='23505';
    END IF;
    RETURN jsonb_build_object(
        'outcome','found','source',source,'kind',kind,
        'provider_request',provider_request,
        'provider_request_hash',provider_hash
    );
END;
$$;

REVOKE ALL ON FUNCTION _prepare_agent_runtime_model_video_v1(JSONB,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;

RESET ROLE;
