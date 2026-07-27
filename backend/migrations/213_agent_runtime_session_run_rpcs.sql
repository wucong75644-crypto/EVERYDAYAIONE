-- 213: Agent Runtime Session, Command, Run creation and claim RPCs.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION ensure_agent_runtime_session(
    p_conversation_id UUID, p_org_id UUID, p_user_id UUID,
    p_scope_kind TEXT, p_scope_id TEXT, p_created_by_user_id UUID,
    p_agent_definition_id TEXT, p_agent_definition_revision TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    SELECT * INTO v_conversation FROM conversations
     WHERE id = p_conversation_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CONVERSATION_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_conversation.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.user_id IS DISTINCT FROM p_user_id
       OR v_conversation.scope_type IS DISTINCT FROM p_scope_kind
       OR v_conversation.scope_id IS DISTINCT FROM p_scope_id
       OR p_scope_kind NOT IN ('user', 'channel')
       OR (p_scope_kind = 'user'
           AND p_created_by_user_id IS DISTINCT FROM p_user_id)
       OR NULLIF(BTRIM(p_scope_id), '') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SESSION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_created_by_user_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM users
         WHERE id = p_created_by_user_id AND status::TEXT = 'active'
    ) OR (p_user_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM users WHERE id = p_user_id AND status::TEXT = 'active'
    )) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_USER_INACTIVE' USING ERRCODE = '42501';
    END IF;
    IF p_org_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM org_members
         WHERE org_id = p_org_id AND user_id = p_created_by_user_id
           AND status = 'active'
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CREATOR_MEMBERSHIP_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    IF p_org_id IS NOT NULL AND (
        NOT EXISTS (
            SELECT 1 FROM organizations
             WHERE id = p_org_id AND status = 'active'
        )
        OR (p_user_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM org_members
             WHERE org_id = p_org_id AND user_id = p_user_id
               AND status = 'active'
        ))
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_ORG_SCOPE_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    IF tenant_actor_user_id() IS DISTINCT FROM p_created_by_user_id
       OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CALLER_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE conversation_id = p_conversation_id FOR UPDATE;
    IF FOUND THEN
        IF v_session.org_id IS DISTINCT FROM p_org_id
           OR v_session.user_id IS DISTINCT FROM p_user_id
           OR v_session.scope_kind IS DISTINCT FROM p_scope_kind
           OR v_session.scope_id IS DISTINCT FROM p_scope_id THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_SESSION_CONFLICT'
                USING ERRCODE = '23505';
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_exists', 'entity_id', v_session.id,
            'state_version', v_session.state_version
        );
    END IF;
    INSERT INTO agent_runtime_sessions(
        conversation_id, org_id, user_id, scope_kind, scope_id,
        created_by_user_id, agent_definition_id, agent_definition_revision
    ) VALUES (
        p_conversation_id, p_org_id, p_user_id, p_scope_kind, p_scope_id,
        p_created_by_user_id, p_agent_definition_id,
        p_agent_definition_revision
    ) RETURNING * INTO v_session;
    v_event := append_agent_runtime_event(
        v_session.id, 'session.created', NULL, NULL, gen_random_uuid(),
        'user', p_created_by_user_id::TEXT, '{}'::JSONB,
        ARRAY['web_runtime']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'created', 'entity_id', v_session.id,
        'state_version', v_session.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION submit_session_command(
    p_session_id UUID, p_command_type TEXT, p_idempotency_key TEXT,
    p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_session agent_runtime_sessions%ROWTYPE;
    v_command agent_session_commands%ROWTYPE;
    v_idempotency_key TEXT := BTRIM(COALESCE(p_idempotency_key, ''));
    v_request_hash TEXT;
    v_event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SESSION_MISSING' USING ERRCODE = 'P0002';
    END IF;
    IF tenant_org_id() IS DISTINCT FROM v_session.org_id
       OR (
           v_session.scope_kind = 'user'
           AND tenant_actor_user_id() IS DISTINCT FROM v_session.user_id
       )
       OR (
           v_session.scope_kind = 'channel'
           AND NOT EXISTS (
               SELECT 1 FROM org_members
                WHERE org_id = v_session.org_id
                  AND user_id = tenant_actor_user_id()
                  AND status = 'active'
           )
       )
       OR p_command_type NOT IN (
           'submit_input', 'steer', 'cancel', 'approve',
           'reject', 'switch_agent', 'compact'
       )
       OR length(v_idempotency_key) NOT BETWEEN 1 AND 200
       OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR pg_column_size(p_payload) > 262144 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_COMMAND_INVALID'
            USING ERRCODE = '42501';
    END IF;
    v_request_hash := md5(jsonb_build_object(
        'command_type', p_command_type,
        'payload', p_payload
    )::TEXT);
    SELECT * INTO v_command FROM agent_session_commands
     WHERE session_id = p_session_id
       AND idempotency_key = v_idempotency_key FOR UPDATE;
    IF FOUND THEN
        IF v_command.request_hash IS DISTINCT FROM v_request_hash THEN
            RETURN jsonb_build_object(
                'outcome', 'idempotency_conflict',
                'entity_id', v_command.id
            );
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_exists', 'entity_id', v_command.id,
            'result_entity_id', v_command.result_entity_id
        );
    END IF;
    INSERT INTO agent_session_commands(
        session_id, org_id, user_id, command_type,
        idempotency_key, payload, request_hash
    ) VALUES (
        p_session_id, v_session.org_id, v_session.user_id, p_command_type,
        v_idempotency_key, p_payload, v_request_hash
    ) RETURNING * INTO v_command;
    v_event := append_agent_runtime_event(
        p_session_id, 'command.accepted', NULL, NULL, v_command.id,
        'user', v_session.created_by_user_id::TEXT,
        jsonb_build_object('command_id', v_command.id),
        ARRAY['web_runtime']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'created', 'entity_id', v_command.id,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION create_agent_run(
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

CREATE FUNCTION claim_agent_run(
    p_run_id UUID, p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_session_id UUID;
    v_run agent_runs%ROWTYPE;
    v_token UUID := gen_random_uuid();
    v_event JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    IF p_lease_seconds NOT BETWEEN 15 AND 300
       OR p_max_attempts NOT BETWEEN 1 AND 20
       OR NULLIF(BTRIM(p_worker_id), '') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CLAIM_INVALID' USING ERRCODE = '22023';
    END IF;
    IF v_run.status = 'running'
       AND v_run.lease_expires_at > clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'busy');
    END IF;
    IF v_run.status NOT IN ('queued', 'running') THEN
        RETURN jsonb_build_object('outcome', 'invalid_transition');
    END IF;
    IF v_run.attempt_count >= p_max_attempts THEN
        RETURN jsonb_build_object('outcome', 'attempts_exhausted');
    END IF;
    UPDATE agent_run_attempts
       SET ended_at = clock_timestamp(), outcome = 'lease_lost'
     WHERE run_id = p_run_id AND ended_at IS NULL;
    UPDATE agent_runs SET status = 'running', execution_token = v_token,
           lease_expires_at = clock_timestamp()
               + make_interval(secs => p_lease_seconds),
           attempt_count = attempt_count + 1,
           state_version = state_version + 1,
           started_at = COALESCE(started_at, clock_timestamp()),
           updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    INSERT INTO agent_run_attempts(
        run_id, org_id, user_id, attempt_number, execution_token,
        worker_id, lease_expires_at
    ) VALUES (
        v_run.id, v_run.org_id, v_run.user_id, v_run.attempt_count,
        v_token, BTRIM(p_worker_id), v_run.lease_expires_at
    );
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.claimed', v_run.id, NULL, v_token,
        'system', p_worker_id, jsonb_build_object('attempt', v_run.attempt_count),
        ARRAY['audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'claimed', 'entity_id', v_run.id,
        'execution_token', v_token, 'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION renew_agent_run(
    p_run_id UUID, p_execution_token UUID, p_lease_seconds INTEGER DEFAULT 90
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_RENEW_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_runs SET lease_expires_at = clock_timestamp()
               + make_interval(secs => p_lease_seconds),
           updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET lease_expires_at = v_run.lease_expires_at
     WHERE execution_token = p_execution_token AND ended_at IS NULL;
    RETURN jsonb_build_object(
        'outcome', 'renewed', 'entity_id', p_run_id,
        'state_version', v_run.state_version
    );
END;
$$;

REVOKE ALL ON FUNCTION
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    ),
    submit_session_command(UUID, TEXT, TEXT, JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    ),
    submit_session_command(UUID, TEXT, TEXT, JSONB)
TO everydayai_runtime, everydayai_wecom_runtime;

REVOKE ALL ON FUNCTION
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    renew_agent_run(UUID, UUID, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    renew_agent_run(UUID, UUID, INTEGER)
TO everydayai_worker;

RESET ROLE;
