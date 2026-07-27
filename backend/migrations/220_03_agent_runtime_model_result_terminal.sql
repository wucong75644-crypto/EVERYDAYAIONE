-- 220_03: Final ModelAttempt/ModelStep/credits/ModelResult single transaction.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION complete_model_attempt_with_result(
    p_attempt_id UUID, p_run_execution_token UUID,
    p_expected_attempt_version BIGINT, p_expected_step_version BIGINT,
    p_request_hash TEXT, p_response_receipt JSONB, p_response_hash TEXT,
    p_stop_reason TEXT, p_provider_stop_reason TEXT, p_usage JSONB,
    p_actual_credits INTEGER, p_output_kind TEXT, p_text_content TEXT,
    p_structured_content JSONB, p_schema_revision TEXT, p_content_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE;
    v_step agent_model_steps%ROWTYPE;
    v_result agent_model_results%ROWTYPE;
    v_terminal JSONB;
    v_computed_hash TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_attempt FROM agent_model_attempts WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF p_stop_reason NOT IN ('final', 'structured_final')
       OR p_output_kind NOT IN ('text', 'structured')
       OR (p_stop_reason = 'final' AND (
           p_output_kind <> 'text' OR p_text_content IS NULL
           OR p_structured_content IS NOT NULL OR p_schema_revision IS NOT NULL))
       OR (p_stop_reason = 'structured_final' AND (
           p_output_kind <> 'structured' OR p_text_content IS NOT NULL
           OR p_structured_content IS NULL
           OR NULLIF(BTRIM(p_schema_revision), '') IS NULL)) THEN
        RAISE EXCEPTION 'AGENT_MODEL_RESULT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF COALESCE(octet_length(p_text_content), 0) > 4194304
       OR COALESCE(pg_column_size(p_structured_content), 0) > 4194304 THEN
        RAISE EXCEPTION 'AGENT_MODEL_RESULT_TOO_LARGE'
            USING ERRCODE = '22023';
    END IF;
    v_computed_hash := encode(digest(
        convert_to(CASE WHEN p_output_kind = 'text'
            THEN p_text_content ELSE p_structured_content::TEXT END, 'UTF8'),
        'sha256'), 'hex');
    IF v_computed_hash IS DISTINCT FROM p_content_hash THEN
        RETURN jsonb_build_object('outcome', 'content_hash_conflict');
    END IF;
    v_terminal := _complete_model_attempt_without_actions(
        p_attempt_id, p_run_execution_token, v_attempt.execution_token,
        p_expected_attempt_version, p_expected_step_version, p_request_hash,
        p_response_receipt, p_response_hash, p_stop_reason,
        p_provider_stop_reason, p_usage, p_actual_credits);
    IF v_terminal->>'outcome' NOT IN ('completed', 'already_completed') THEN
        RETURN v_terminal;
    END IF;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = v_attempt.model_step_id;
    INSERT INTO agent_model_results(
        model_step_id, run_id, session_id, org_id, user_id, output_kind,
        text_content, structured_content, schema_revision, content_hash
    ) VALUES (
        v_step.id, v_step.run_id, v_step.session_id, v_step.org_id,
        v_step.user_id, p_output_kind, p_text_content,
        p_structured_content, p_schema_revision, p_content_hash
    ) ON CONFLICT (model_step_id) DO NOTHING
    RETURNING * INTO v_result;
    IF v_result.id IS NULL THEN
        SELECT * INTO v_result FROM agent_model_results
         WHERE model_step_id = v_step.id;
    END IF;
    IF v_result.output_kind IS DISTINCT FROM p_output_kind
       OR v_result.text_content IS DISTINCT FROM p_text_content
       OR v_result.structured_content IS DISTINCT FROM p_structured_content
       OR v_result.schema_revision IS DISTINCT FROM p_schema_revision
       OR v_result.content_hash IS DISTINCT FROM p_content_hash THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    RETURN v_terminal || jsonb_build_object(
        'model_result_id', v_result.id,
        'content_hash', v_result.content_hash);
END;
$$;

CREATE FUNCTION get_agent_model_result(p_model_step_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_result agent_model_results%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_result FROM agent_model_results
     WHERE model_step_id = p_model_step_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object('outcome', 'found', 'result', to_jsonb(v_result));
END;
$$;

REVOKE ALL ON FUNCTION
    complete_model_attempt_with_result(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, JSONB,
        INTEGER, TEXT, TEXT, JSONB, TEXT, TEXT),
    get_agent_model_result(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    complete_model_attempt_with_result(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, JSONB,
        INTEGER, TEXT, TEXT, JSONB, TEXT, TEXT),
    get_agent_model_result(UUID)
TO everydayai_worker;

RESET ROLE;
