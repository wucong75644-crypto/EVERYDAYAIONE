/* Roll back 228.08a without orphaning Runtime-owned ModelLoop video facts. */
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM agent_runtime_prepared_media_action_bindings binding
          JOIN agent_actions action ON action.id=binding.action_id
         WHERE action.tool_name='generate_video'
           AND COALESCE(action.policy_snapshot->>'source','model_loop')
               <>'media_ingress'
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08A_ACTIVE_MODEL_VIDEO_FACTS'
            USING ERRCODE='55000';
    END IF;
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
BEGIN
    context:=_agent_runtime_media_attempt_context_v2(
        p_action_id,p_attempt_id,p_worker_id,p_owner_token,
        p_expected_attempt_version,p_request_hash
    );
    SELECT * INTO action FROM agent_actions WHERE id=p_action_id;
    kind:=CASE action.tool_name WHEN 'generate_image' THEN 'image' ELSE 'video' END;
    IF context->>'source'='media_ingress' THEN
        SELECT * INTO prepared_binding
          FROM agent_runtime_prepared_media_action_bindings
         WHERE action_id=action.id;
        SELECT * INTO task FROM tasks WHERE id=prepared_binding.task_id;
        IF prepared_binding.action_id IS NULL OR task.id IS NULL
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
        'outcome','found','source',context->>'source','kind',kind,
        'provider_request',provider_request,
        'provider_request_hash',provider_hash
    );
END;
$$;

DROP FUNCTION _prepare_agent_runtime_model_video_v1(JSONB,TEXT);

RESET ROLE;
