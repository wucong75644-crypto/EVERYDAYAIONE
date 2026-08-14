/* 228.08e1: recheck the complete ModelLoop video fence after lock waits. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
        'submit_agent_runtime_media_image_batch_v1(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,text,text,text,text,text,text,jsonb)'
    ) IS NULL OR to_regprocedure(
        '_prepare_agent_runtime_model_video_v1(jsonb,text)'
    ) IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_228_08D_08A_REQUIRED'
            USING ERRCODE='55000';
    END IF;
    IF to_regprocedure(
        '_prepare_agent_runtime_model_video_fenced_v1(jsonb,text)'
    ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_228_08E1_IDENTITY_CONFLICT'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE FUNCTION _prepare_agent_runtime_model_video_fenced_v1(
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
    fresh_context JSONB;
BEGIN
    PERFORM _agent_runtime_media_worker_v1();
    IF jsonb_typeof(p_context) IS DISTINCT FROM 'object'
       OR p_context->>'source' NOT IN ('model_loop','runtime_executor_registry')
       OR p_context->>'tool_name' IS DISTINCT FROM 'generate_video'
       OR NULLIF(p_context->>'action_id','') IS NULL
       OR NULLIF(p_context->>'attempt_id','') IS NULL
       OR NULLIF(btrim(p_context->>'worker_id'),'') IS NULL
       OR NULLIF(p_context->>'owner_token','') IS NULL
       OR COALESCE(p_context->>'expected_attempt_version','') !~ '^[0-9]+$'
       OR COALESCE(p_request_hash,'') !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_FENCE_INPUT_INVALID'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO action FROM agent_actions
     WHERE id=(p_context->>'action_id')::UUID;
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
     WHERE id=(p_context->>'attempt_id')::UUID FOR UPDATE;
    SELECT * INTO command FROM agent_session_commands
     WHERE id=runtime_run.command_id FOR UPDATE;
    PERFORM id FROM tasks
     WHERE id=NULLIF(command.payload->>'task_id','')::UUID FOR UPDATE;
    PERFORM id FROM messages WHERE id IN (
        NULLIF(command.payload->>'input_message_id','')::UUID,
        NULLIF(command.payload->>'output_message_id','')::UUID
    ) ORDER BY id FOR UPDATE;
    PERFORM id FROM users WHERE id=runtime_session.user_id FOR UPDATE;
    PERFORM action_id FROM agent_runtime_prepared_media_action_bindings
     WHERE action_id=action.id FOR UPDATE;
    PERFORM intent.attempt_id
      FROM agent_action_dispatch_intents intent
      JOIN agent_policy_receipts receipt ON receipt.id=intent.policy_receipt_id
     WHERE intent.attempt_id=attempt.id AND intent.action_id=action.id
     FOR UPDATE OF intent,receipt;

    fresh_context:=_agent_runtime_media_attempt_context_v2(
        action.id,attempt.id,p_context->>'worker_id',
        (p_context->>'owner_token')::UUID,
        (p_context->>'expected_attempt_version')::BIGINT,p_request_hash
    );
    IF fresh_context->>'action_id' IS DISTINCT FROM action.id::TEXT
       OR fresh_context->>'attempt_id' IS DISTINCT FROM attempt.id::TEXT
       OR fresh_context->>'session_id' IS DISTINCT FROM runtime_session.id::TEXT
       OR fresh_context->>'run_id' IS DISTINCT FROM runtime_run.id::TEXT
       OR fresh_context->>'model_step_id' IS DISTINCT FROM step.id::TEXT
       OR fresh_context->>'source' NOT IN ('model_loop','runtime_executor_registry')
       OR fresh_context->>'tool_name' IS DISTINCT FROM 'generate_video' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_VIDEO_FENCE_RECHECK_FAILED'
            USING ERRCODE='42501';
    END IF;
    RETURN _prepare_agent_runtime_model_video_v1(fresh_context,p_request_hash);
END;
$$;

CREATE OR REPLACE FUNCTION prepare_agent_runtime_media_dispatch_v1(
    p_action_id UUID,p_attempt_id UUID,p_worker_id TEXT,p_owner_token UUID,
    p_expected_attempt_version BIGINT,p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    context JSONB;
    action agent_actions%ROWTYPE;
    source TEXT;
    manifest JSONB;
    prepared JSONB;
    request_fact JSONB;
BEGIN
    PERFORM _agent_runtime_media_worker_v1();
    SELECT * INTO action FROM agent_actions WHERE id=p_action_id;
    source:=COALESCE(action.policy_snapshot->>'source','model_loop');
    IF action.tool_name='generate_video' AND source<>'media_ingress' THEN
        RETURN _prepare_agent_runtime_model_video_fenced_v1(
            jsonb_build_object(
                'action_id',p_action_id,'attempt_id',p_attempt_id,
                'worker_id',p_worker_id,'owner_token',p_owner_token,
                'expected_attempt_version',p_expected_attempt_version,
                'source',source,'tool_name',action.tool_name
            ),p_request_hash
        );
    END IF;
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
            OR provider_request_canonical_hash=request_fact->>'provider_request_hash');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_REQUEST_CONFLICT'
            USING ERRCODE='23505';
    END IF;
    RETURN jsonb_build_object('outcome',prepared->>'outcome');
END;
$$;

REVOKE ALL ON FUNCTION _prepare_agent_runtime_model_video_fenced_v1(JSONB,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;

RESET ROLE;
