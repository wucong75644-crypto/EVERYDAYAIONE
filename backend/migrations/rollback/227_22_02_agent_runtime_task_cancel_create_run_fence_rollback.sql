-- Restore the 213 direct root Run creation contract when no intent exists.
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_task_cancel_intents) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_ROLLBACK_FACTS_EXIST'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION create_agent_run(
    p_session_id UUID, p_command_id UUID, p_idempotency_key TEXT,
    p_run_kind TEXT, p_context_receipt JSONB,
    p_config_snapshot JSONB, p_capability_snapshot JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_session agent_runtime_sessions%ROWTYPE;
    v_command agent_session_commands%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_idempotency_key TEXT := BTRIM(COALESCE(p_idempotency_key, ''));
    v_request_hash TEXT;
    v_event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id FOR UPDATE;
    SELECT * INTO v_command FROM agent_session_commands
     WHERE id = p_command_id AND session_id = p_session_id FOR UPDATE;
    IF v_session.id IS NULL OR v_command.id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_RUN_PARENT_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    IF p_run_kind NOT IN ('user', 'continuation')
       OR length(v_idempotency_key) NOT BETWEEN 1 AND 200
       OR jsonb_typeof(p_context_receipt) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_config_snapshot) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_capability_snapshot) IS DISTINCT FROM 'object'
       OR pg_column_size(p_context_receipt) > 262144
       OR pg_column_size(p_config_snapshot) > 262144
       OR pg_column_size(p_capability_snapshot) > 262144 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_RUN_INVALID' USING ERRCODE = '22023';
    END IF;
    v_request_hash := md5(jsonb_build_object(
        'command_id', p_command_id,
        'run_kind', p_run_kind,
        'context_receipt', p_context_receipt,
        'config_snapshot', p_config_snapshot,
        'capability_snapshot', p_capability_snapshot
    )::TEXT);
    SELECT * INTO v_run FROM agent_runs
     WHERE session_id = p_session_id AND idempotency_key = v_idempotency_key
     FOR UPDATE;
    IF FOUND THEN
        IF v_run.request_hash IS DISTINCT FROM v_request_hash THEN
            RETURN jsonb_build_object(
                'outcome', 'idempotency_conflict',
                'entity_id', v_run.id
            );
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_exists', 'entity_id', v_run.id,
            'state_version', v_run.state_version
        );
    END IF;
    IF v_command.result_entity_id IS NOT NULL THEN
        SELECT * INTO v_run FROM agent_runs
         WHERE id = v_command.result_entity_id FOR UPDATE;
        IF NOT FOUND OR v_run.command_id IS DISTINCT FROM p_command_id
           OR v_run.request_hash IS DISTINCT FROM v_request_hash THEN
            RETURN jsonb_build_object(
                'outcome', 'idempotency_conflict',
                'entity_id', v_command.result_entity_id
            );
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_exists', 'entity_id', v_run.id,
            'state_version', v_run.state_version
        );
    END IF;
    INSERT INTO agent_runs(
        session_id, command_id, org_id, user_id, run_kind, idempotency_key,
        request_hash, context_receipt, config_snapshot, capability_snapshot
    ) VALUES (
        p_session_id, p_command_id, v_session.org_id, v_session.user_id,
        p_run_kind, v_idempotency_key, v_request_hash,
        p_context_receipt, p_config_snapshot, p_capability_snapshot
    ) RETURNING * INTO v_run;
    UPDATE agent_session_commands SET result_entity_id = v_run.id
     WHERE id = p_command_id AND result_entity_id IS NULL;
    v_event := append_agent_runtime_event(
        p_session_id, 'run.created', v_run.id, NULL, p_command_id,
        'system', session_user, jsonb_build_object('run_id', v_run.id),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'created', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

REVOKE ALL ON FUNCTION create_agent_run(
    UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai,
    everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION create_agent_run(
    UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB)
TO everydayai_agent_runtime_worker;

RESET ROLE;
