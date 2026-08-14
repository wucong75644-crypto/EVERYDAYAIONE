/* Roll back only the 228.08e1 post-lock fencing wrapper. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
        '_agent_runtime_prepared_media_source_v1(uuid)'
    ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08E2_MUST_ROLL_BACK_FIRST'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

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

DROP FUNCTION _prepare_agent_runtime_model_video_fenced_v1(JSONB,TEXT);

RESET ROLE;
