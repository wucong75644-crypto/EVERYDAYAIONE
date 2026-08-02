-- 226_19: additive cancel result hashing and legacy terminal RPC revocation.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _agent_action_result_hash(
    p_result JSONB, p_action_status TEXT,
    p_conversation_id UUID, p_org_id UUID
) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_hash TEXT;
BEGIN
    IF jsonb_typeof(p_result) IS DISTINCT FROM 'object'
       OR p_action_status NOT IN ('completed', 'failed', 'cancelled')
       OR p_result->>'status' NOT IN ('success', 'empty', 'degraded', 'error')
       OR NOT _agent_action_json_is_safe(COALESCE(p_result->'external_receipt', '{}'::JSONB))
       OR NOT _agent_action_json_is_safe(COALESCE(p_result->'data', '{}'::JSONB))
       OR (p_action_status = 'failed') IS DISTINCT FROM (p_result->>'status' = 'error') THEN
        RAISE EXCEPTION 'AGENT_ACTION_RESULT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(COALESCE(p_result->'artifact_ids', '[]'::JSONB)) artifact_id
        WHERE NOT EXISTS (
            SELECT 1 FROM conversation_artifacts artifact
            WHERE artifact.id = artifact_id::UUID
              AND artifact.conversation_id = p_conversation_id
              AND artifact.org_id IS NOT DISTINCT FROM p_org_id
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_ACTION_ARTIFACT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    v_hash := encode(sha256(convert_to(jsonb_build_object(
        'status', p_result->>'status', 'summary', COALESCE(p_result->>'summary', ''),
        'data', p_result->'data', 'artifact_ids', COALESCE(p_result->'artifact_ids', '[]'::JSONB),
        'usage', COALESCE(p_result->'usage', '{}'::JSONB), 'cost', COALESCE(p_result->'cost', '{}'::JSONB),
        'external_receipt', COALESCE(p_result->'external_receipt', '{}'::JSONB),
        'error_code', p_result->>'error_code'
    )::TEXT, 'UTF8')), 'hex');
    RETURN v_hash;
END;
$$;

REVOKE EXECUTE ON FUNCTION finalize_agent_action_provider(UUID,UUID,UUID,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT), complete_agent_child_run_strict(UUID,UUID,UUID,TEXT,INTEGER,JSONB), cancel_agent_child_run_strict(UUID,UUID,UUID,TEXT,TEXT), complete_agent_child_run(UUID,UUID,INTEGER,JSONB), cancel_agent_child_run(UUID,UUID,TEXT) FROM PUBLIC, everydayai_agent_runtime_worker, everydayai_worker, everydayai_runtime, everydayai_wecom_runtime, everydayai_sync, everydayai;
RESET ROLE;
