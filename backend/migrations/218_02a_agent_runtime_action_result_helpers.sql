-- 218_02a: Owner-only Action result validation and hashing.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_action_result_hash(
    p_result JSONB, p_action_status TEXT,
    p_conversation_id UUID, p_org_id UUID
) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_hash TEXT;
BEGIN
    IF jsonb_typeof(p_result) IS DISTINCT FROM 'object'
       OR p_action_status NOT IN ('completed', 'failed', 'cancelled')
       OR p_result->>'status' NOT IN ('success', 'empty', 'degraded', 'error')
       OR NOT _agent_action_json_is_safe(
           COALESCE(p_result->'external_receipt', '{}'::JSONB))
       OR NOT _agent_action_json_is_safe(
           COALESCE(p_result->'data', '{}'::JSONB))
       OR (p_action_status = 'failed') IS DISTINCT FROM
          (p_result->>'status' = 'error') THEN
        RAISE EXCEPTION 'AGENT_ACTION_RESULT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(
            COALESCE(p_result->'artifact_ids', '[]'::JSONB)
        ) artifact_id
        WHERE NOT EXISTS (
            SELECT 1 FROM conversation_artifacts artifact
            WHERE artifact.id = artifact_id::UUID
              AND artifact.conversation_id = p_conversation_id
              AND artifact.org_id IS NOT DISTINCT FROM p_org_id
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_ACTION_ARTIFACT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    v_hash := encode(sha256(convert_to(jsonb_build_object(
        'status', p_result->>'status',
        'summary', COALESCE(p_result->>'summary', ''),
        'data', p_result->'data',
        'artifact_ids', COALESCE(p_result->'artifact_ids', '[]'::JSONB),
        'usage', COALESCE(p_result->'usage', '{}'::JSONB),
        'cost', COALESCE(p_result->'cost', '{}'::JSONB),
        'external_receipt',
            COALESCE(p_result->'external_receipt', '{}'::JSONB),
        'error_code', p_result->>'error_code'
    )::TEXT, 'UTF8')), 'hex');
    RETURN v_hash;
END;
$$;

CREATE FUNCTION _insert_agent_action_batch(
    p_step agent_model_steps, p_actions JSONB, p_canonical JSONB, p_batch_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_item JSONB; v_action agent_actions%ROWTYPE; v_ids JSONB := '[]';
BEGIN
    FOR v_item IN SELECT item FROM jsonb_array_elements(p_actions) item
        ORDER BY (item->>'index')::INTEGER,
                 btrim(item->>'stable_tool_call_id'), (item->>'action_id')::UUID
    LOOP
        INSERT INTO agent_actions(
            id,session_id,run_id,model_step_id,org_id,user_id,action_index,
            stable_tool_call_id,provider_call_id,tool_name,arguments,
            arguments_hash,request_hash,batch_hash,wave,dependency_ids,blocking,
            policy_decision,policy_snapshot,policy_revision,retry_disposition,status
        ) VALUES (
            (v_item->>'action_id')::UUID,p_step.session_id,p_step.run_id,p_step.id,
            p_step.org_id,p_step.user_id,(v_item->>'index')::INTEGER,
            btrim(v_item->>'stable_tool_call_id'),
            NULLIF(btrim(v_item->>'provider_call_id'),''),
            lower(btrim(v_item->>'tool_name')),v_item->'arguments',
            v_item->>'arguments_hash',(SELECT item->>'request_hash'
                FROM jsonb_array_elements(p_canonical) item
                WHERE item->>'action_id'=v_item->>'action_id'),p_batch_hash,
            COALESCE((v_item->>'wave')::INTEGER,0),
            ARRAY(SELECT value::UUID FROM jsonb_array_elements_text(
                COALESCE(v_item->'dependencies','[]')) value ORDER BY value),
            COALESCE((v_item->>'blocking')::BOOLEAN,TRUE),
            v_item->>'policy_decision',v_item->'policy_snapshot',
            v_item->>'policy_revision',v_item->>'retry_disposition','requested'
        ) RETURNING * INTO v_action;
        UPDATE agent_actions SET status=CASE policy_decision
            WHEN 'preauthorized' THEN 'queued'
            WHEN 'requires_authorization' THEN 'awaiting_authorization'
            ELSE 'rejected' END,state_version=state_version+1,
            completed_at=CASE WHEN policy_decision='rejected'
                THEN clock_timestamp() ELSE NULL END,updated_at=clock_timestamp()
         WHERE id=v_action.id RETURNING * INTO v_action;
        v_ids := v_ids || to_jsonb(v_action.id);
    END LOOP;
    RETURN v_ids;
END;
$$;

CREATE FUNCTION _apply_agent_tool_terminal(
    p_attempt agent_model_attempts, p_step agent_model_steps, p_run agent_runs,
    p_token UUID, p_receipt JSONB, p_response_hash TEXT, p_stop_reason TEXT,
    p_usage JSONB, p_batch_hash TEXT, p_actions JSONB, p_canonical JSONB,
    p_settlement JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_model_attempts%ROWTYPE; v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE; v_action agent_actions%ROWTYPE; v_event JSONB;
    v_sequences JSONB := '[]'; v_ids JSONB; v_blockers INTEGER;
BEGIN
    UPDATE agent_model_attempts SET status='completed',response_receipt=p_receipt,
        response_hash=p_response_hash,usage=p_usage,retry_disposition='forbidden',
        state_version=state_version+1,completed_at=clock_timestamp(),
        updated_at=clock_timestamp()
     WHERE id=p_attempt.id RETURNING * INTO v_attempt;
    UPDATE agent_model_steps SET status='completed',response_receipt=p_receipt,
        stop_reason='tool_calls',provider_stop_reason=p_stop_reason,
        input_tokens=COALESCE((p_usage->>'input_tokens')::BIGINT,0),
        output_tokens=COALESCE((p_usage->>'output_tokens')::BIGINT,0),
        reasoning_tokens=COALESCE((p_usage->>'reasoning_tokens')::BIGINT,0),
        state_version=state_version+1,completed_at=clock_timestamp(),
        updated_at=clock_timestamp()
     WHERE id=p_step.id RETURNING * INTO v_step;
    v_ids := _insert_agent_action_batch(
        v_step,p_actions,p_canonical,p_batch_hash);
    SELECT count(*) INTO v_blockers FROM agent_actions
     WHERE model_step_id=v_step.id AND blocking
       AND status NOT IN ('completed','failed','rejected','cancelled');
    UPDATE agent_runs SET blocking_action_count=blocking_action_count+v_blockers,
        status=CASE WHEN v_blockers>0 THEN 'waiting_actions' ELSE status END,
        execution_token=CASE WHEN v_blockers>0 THEN NULL ELSE execution_token END,
        lease_expires_at=CASE WHEN v_blockers>0 THEN NULL ELSE lease_expires_at END,
        state_version=state_version+CASE WHEN v_blockers>0 THEN 1 ELSE 0 END,
        updated_at=clock_timestamp()
     WHERE id=p_run.id RETURNING * INTO v_run;
    IF v_blockers>0 THEN
        UPDATE agent_run_attempts SET ended_at=clock_timestamp(),outcome='completed'
         WHERE run_id=v_run.id AND execution_token=p_token AND ended_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_RUN_ATTEMPT_CLOSE_MISSING'
            USING ERRCODE='55000'; END IF;
        v_event:=append_agent_runtime_event(v_run.session_id,'run.waiting',v_run.id,
            v_step.id,p_token,'system',session_user,jsonb_build_object(
                'status','waiting_actions','blocking_action_count',
                v_run.blocking_action_count),ARRAY['web_runtime','audit']::TEXT[]);
        v_sequences:=v_sequences||(v_event->'event_sequence');
    END IF;
    v_event:=append_agent_runtime_event(v_step.session_id,'model_step.completed',
        v_step.run_id,v_step.id,p_token,'model',session_user,jsonb_build_object(
            'stop_reason','tool_calls','attempt_id',v_attempt.id,
            'batch_hash',p_batch_hash),ARRAY['web_runtime','audit']::TEXT[]);
    v_sequences:=v_sequences||(v_event->'event_sequence');
    FOR v_action IN SELECT * FROM agent_actions WHERE model_step_id=v_step.id
        ORDER BY action_index,stable_tool_call_id,id LOOP
        v_event:=append_agent_runtime_event(v_action.session_id,'action.requested',
            v_action.run_id,v_action.model_step_id,v_action.id,'model',session_user,
            jsonb_build_object('action_id',v_action.id,'tool_name',
                v_action.tool_name,'status',v_action.status,'blocking',
                v_action.blocking),ARRAY['web_runtime','audit']::TEXT[]);
        v_sequences:=v_sequences||(v_event->'event_sequence');
    END LOOP;
    RETURN jsonb_build_object('outcome','completed','attempt_id',v_attempt.id,
        'model_step_id',v_step.id,'run_id',v_run.id,'run_status',v_run.status,
        'blocking_action_count',v_run.blocking_action_count,'batch_hash',
        p_batch_hash,'action_ids',v_ids,'event_sequences',v_sequences,
        'settlement_outcome',p_settlement->>'outcome');
END;
$$;

REVOKE ALL ON FUNCTION _agent_action_result_hash(JSONB, TEXT, UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION
    _insert_agent_action_batch(agent_model_steps,JSONB,JSONB,TEXT),
    _apply_agent_tool_terminal(
        agent_model_attempts,agent_model_steps,agent_runs,UUID,JSONB,TEXT,TEXT,
        JSONB,TEXT,JSONB,JSONB,JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
RESET ROLE;
