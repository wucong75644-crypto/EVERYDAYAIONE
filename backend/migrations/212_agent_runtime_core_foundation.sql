-- 212: Additive Agent Runtime Session/Run/ModelStep/Event foundation.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL UNIQUE
        REFERENCES conversations(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user', 'channel')),
    scope_id TEXT NOT NULL,
    created_by_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    agent_definition_id TEXT NOT NULL,
    agent_definition_revision TEXT NOT NULL,
    next_event_sequence BIGINT NOT NULL DEFAULT 1
        CHECK (next_event_sequence > 0),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (scope_kind = 'user' AND user_id IS NOT NULL)
        OR (scope_kind = 'channel' AND org_id IS NOT NULL AND user_id IS NULL)
    )
);

CREATE INDEX idx_agent_runtime_sessions_scope
    ON agent_runtime_sessions(org_id, scope_kind, scope_id);

CREATE TABLE agent_session_commands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    command_type TEXT NOT NULL
        CHECK (command_type IN ('user_turn', 'resume', 'cancel')),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    payload JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    payload_hash TEXT NOT NULL,
    result_entity_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (session_id, idempotency_key)
);

CREATE TABLE agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    command_id UUID NOT NULL REFERENCES agent_session_commands(id)
        ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    run_kind TEXT NOT NULL CHECK (run_kind IN ('user', 'continuation')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'running', 'waiting_actions', 'waiting_interaction',
        'paused', 'completed', 'failed', 'cancelled'
    )),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    context_receipt JSONB NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(context_receipt) = 'object'),
    config_snapshot JSONB NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(config_snapshot) = 'object'),
    capability_snapshot JSONB NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(capability_snapshot) = 'object'),
    blocking_action_count INTEGER NOT NULL DEFAULT 0
        CHECK (blocking_action_count >= 0),
    open_interaction_count INTEGER NOT NULL DEFAULT 0
        CHECK (open_interaction_count >= 0),
    execution_token UUID,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    terminal_reason TEXT,
    result_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (session_id, idempotency_key),
    CHECK (
        (status = 'running' AND execution_token IS NOT NULL
         AND lease_expires_at IS NOT NULL)
        OR (status <> 'running' AND execution_token IS NULL
            AND lease_expires_at IS NULL)
    ),
    CHECK (
        (status IN ('completed', 'failed', 'cancelled')
         AND completed_at IS NOT NULL)
        OR (status NOT IN ('completed', 'failed', 'cancelled')
            AND completed_at IS NULL)
    )
);

CREATE INDEX idx_agent_runs_claim
    ON agent_runs(created_at, id) WHERE status IN ('queued', 'running');
CREATE INDEX idx_agent_runs_session
    ON agent_runs(session_id, created_at DESC);

CREATE TABLE agent_run_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    execution_token UUID NOT NULL UNIQUE,
    worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 200),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    outcome TEXT CHECK (
        outcome IN ('completed', 'lease_lost', 'failed', 'cancelled')
    ),
    UNIQUE (run_id, attempt_number)
);

CREATE INDEX idx_agent_run_attempts_run
    ON agent_run_attempts(run_id, attempt_number DESC);

CREATE TABLE agent_model_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    step_number INTEGER NOT NULL CHECK (step_number > 0),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    prompt_revision TEXT NOT NULL,
    tool_catalog_revision TEXT NOT NULL,
    request_receipt JSONB NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(request_receipt) = 'object'),
    response_receipt JSONB NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(response_receipt) = 'object'),
    stop_reason TEXT CHECK (stop_reason IN (
        'final', 'tool_calls', 'structured_final', 'length',
        'content_filter', 'model_refusal', 'budget_exhausted',
        'cancelled', 'provider_error', 'protocol_error'
    )),
    provider_stop_reason TEXT,
    input_tokens BIGINT NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens BIGINT NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    reasoning_tokens BIGINT NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    terminal_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (run_id, step_number),
    CHECK (
        (status = 'completed' AND stop_reason IS NOT NULL
         AND completed_at IS NOT NULL)
        OR (status IN ('failed', 'cancelled') AND terminal_reason IS NOT NULL
            AND completed_at IS NOT NULL)
        OR (status = 'running' AND completed_at IS NULL)
    )
);

CREATE TABLE agent_runtime_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version > 0),
    run_id UUID REFERENCES agent_runs(id) ON DELETE RESTRICT,
    model_step_id UUID REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
    correlation_id UUID NOT NULL,
    actor_type TEXT NOT NULL CHECK (
        actor_type IN ('user', 'system', 'worker')
    ),
    actor_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    payload_hash TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (session_id, sequence)
);

CREATE INDEX idx_agent_runtime_events_run
    ON agent_runtime_events(run_id, sequence) WHERE run_id IS NOT NULL;
CREATE INDEX idx_agent_runtime_events_org_time
    ON agent_runtime_events(org_id, occurred_at DESC);

CREATE TABLE agent_projection_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES agent_runtime_events(id)
        ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    projection_kind TEXT NOT NULL CHECK (
        projection_kind IN ('web_runtime', 'wecom', 'audit')
    ),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'delivered', 'dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    checkpoint JSONB NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(checkpoint) = 'object'),
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    delivered_at TIMESTAMPTZ,
    UNIQUE (event_id, projection_kind),
    CHECK (
        (status = 'processing' AND lease_token IS NOT NULL
         AND lease_expires_at IS NOT NULL)
        OR (status <> 'processing' AND lease_token IS NULL
            AND lease_expires_at IS NULL)
    )
);

CREATE INDEX idx_agent_projection_outbox_claim
    ON agent_projection_outbox(next_attempt_at, created_at, id)
    WHERE status IN ('pending', 'processing');

ALTER TABLE agent_runtime_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_session_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_run_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_model_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_projection_outbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_sessions_owner_all ON agent_runtime_sessions
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_session_commands_owner_all ON agent_session_commands
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runs_owner_all ON agent_runs
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_run_attempts_owner_all ON agent_run_attempts
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_model_steps_owner_all ON agent_model_steps
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_events_owner_all ON agent_runtime_events
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_projection_outbox_owner_all ON agent_projection_outbox
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_runtime_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_session_commands FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_run_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_model_steps FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_events FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_projection_outbox FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _assert_agent_runtime_actor(p_worker BOOLEAN)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF NULLIF(current_setting('app.request_id', TRUE), '') IS NULL
       OR (p_worker AND (
           session_user <> 'everydayai_worker'
           OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'worker'
       ))
       OR (NOT p_worker AND (
           session_user NOT IN ('everydayai_runtime', 'everydayai_wecom_runtime')
           OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'runtime'
       )) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE FUNCTION append_agent_runtime_event(
    p_session_id UUID, p_event_type TEXT, p_run_id UUID,
    p_model_step_id UUID, p_correlation_id UUID, p_actor_type TEXT,
    p_actor_id TEXT, p_payload JSONB, p_projection_kinds TEXT[]
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_session agent_runtime_sessions%ROWTYPE;
    v_event_id UUID;
    v_sequence BIGINT;
    v_kind TEXT;
BEGIN
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id FOR UPDATE;
    IF NOT FOUND OR p_event_type IS NULL OR p_correlation_id IS NULL
       OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR pg_column_size(p_payload) > 262144 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_EVENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    v_sequence := v_session.next_event_sequence;
    UPDATE agent_runtime_sessions
       SET next_event_sequence = next_event_sequence + 1,
           updated_at = clock_timestamp()
     WHERE id = p_session_id;
    INSERT INTO agent_runtime_events(
        session_id, sequence, org_id, user_id, event_type, run_id,
        model_step_id, correlation_id, actor_type, actor_id,
        payload, payload_hash
    ) VALUES (
        p_session_id, v_sequence, v_session.org_id, v_session.user_id,
        p_event_type, p_run_id, p_model_step_id, p_correlation_id,
        p_actor_type, p_actor_id, p_payload, md5(p_payload::TEXT)
    ) RETURNING id INTO v_event_id;
    FOREACH v_kind IN ARRAY COALESCE(p_projection_kinds, ARRAY[]::TEXT[])
    LOOP
        INSERT INTO agent_projection_outbox(
            event_id, session_id, org_id, user_id, projection_kind
        ) VALUES (
            v_event_id, p_session_id, v_session.org_id,
            v_session.user_id, v_kind
        );
    END LOOP;
    RETURN jsonb_build_object(
        'event_id', v_event_id, 'event_sequence', v_sequence
    );
END;
$$;

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
    v_hash TEXT;
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
       OR p_command_type NOT IN ('user_turn', 'resume', 'cancel')
       OR NULLIF(BTRIM(p_idempotency_key), '') IS NULL
       OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
       OR pg_column_size(p_payload) > 262144 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_COMMAND_INVALID'
            USING ERRCODE = '42501';
    END IF;
    v_hash := md5(p_payload::TEXT);
    SELECT * INTO v_command FROM agent_session_commands
     WHERE session_id = p_session_id
       AND idempotency_key = p_idempotency_key FOR UPDATE;
    IF FOUND THEN
        IF v_command.command_type IS DISTINCT FROM p_command_type
           OR v_command.payload_hash IS DISTINCT FROM v_hash THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_COMMAND_CONFLICT'
                USING ERRCODE = '23505';
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_exists', 'entity_id', v_command.id,
            'result_entity_id', v_command.result_entity_id
        );
    END IF;
    INSERT INTO agent_session_commands(
        session_id, org_id, user_id, command_type,
        idempotency_key, payload, payload_hash
    ) VALUES (
        p_session_id, v_session.org_id, v_session.user_id, p_command_type,
        BTRIM(p_idempotency_key), p_payload, v_hash
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
    SELECT * INTO v_run FROM agent_runs
     WHERE session_id = p_session_id AND idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF FOUND THEN
        IF v_run.command_id IS DISTINCT FROM p_command_id
           OR v_run.run_kind IS DISTINCT FROM p_run_kind THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_RUN_CONFLICT'
                USING ERRCODE = '23505';
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_exists', 'entity_id', v_run.id,
            'state_version', v_run.state_version
        );
    END IF;
    IF p_run_kind NOT IN ('user', 'continuation')
       OR NULLIF(BTRIM(p_idempotency_key), '') IS NULL
       OR jsonb_typeof(p_context_receipt) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_config_snapshot) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_capability_snapshot) IS DISTINCT FROM 'object'
       OR pg_column_size(p_context_receipt) > 262144
       OR pg_column_size(p_config_snapshot) > 262144
       OR pg_column_size(p_capability_snapshot) > 262144 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_RUN_INVALID' USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_runs(
        session_id, command_id, org_id, user_id, run_kind, idempotency_key,
        context_receipt, config_snapshot, capability_snapshot
    ) VALUES (
        p_session_id, p_command_id, v_session.org_id, v_session.user_id,
        p_run_kind, BTRIM(p_idempotency_key), p_context_receipt,
        p_config_snapshot, p_capability_snapshot
    ) RETURNING * INTO v_run;
    UPDATE agent_session_commands SET result_entity_id = v_run.id
     WHERE id = p_command_id AND result_entity_id IS NULL;
    v_event := append_agent_runtime_event(
        p_session_id, 'run.created', v_run.id, NULL, p_command_id,
        'worker', session_user, jsonb_build_object('run_id', v_run.id),
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
        'worker', p_worker_id, jsonb_build_object('attempt', v_run.attempt_count),
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

CREATE FUNCTION set_agent_run_waiting(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_waiting_status TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
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
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF p_waiting_status NOT IN (
        'waiting_actions', 'waiting_interaction', 'paused'
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_WAIT_INVALID' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_runs SET status = p_waiting_status,
           execution_token = NULL, lease_expires_at = NULL,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts
       SET ended_at = clock_timestamp(), outcome = 'completed'
     WHERE execution_token = p_execution_token AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.waiting', v_run.id, NULL, p_execution_token,
        'worker', session_user, jsonb_build_object('status', p_waiting_status),
        ARRAY['web_runtime']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'transitioned', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION wake_agent_run(
    p_run_id UUID, p_expected_state_version BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_run.status NOT IN (
        'waiting_actions', 'waiting_interaction', 'paused'
    ) OR (v_run.status = 'waiting_actions' AND v_run.blocking_action_count <> 0)
       OR (v_run.status = 'waiting_interaction'
           AND v_run.open_interaction_count <> 0) THEN
        RETURN jsonb_build_object('outcome', 'not_ready');
    END IF;
    UPDATE agent_runs SET status = 'queued',
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.resumed', v_run.id, NULL, gen_random_uuid(),
        'worker', session_user, '{}'::JSONB, ARRAY['web_runtime']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'transitioned', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION _finish_agent_run(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_status TEXT, p_result_hash TEXT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status IN ('completed', 'failed', 'cancelled') THEN
        IF v_run.status = p_status
           AND v_run.result_hash IS NOT DISTINCT FROM p_result_hash
           AND v_run.terminal_reason IS NOT DISTINCT FROM p_reason THEN
            RETURN jsonb_build_object(
                'outcome', 'already_' || p_status, 'entity_id', v_run.id,
                'state_version', v_run.state_version
            );
        END IF;
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF p_status = 'completed' AND (
        v_run.blocking_action_count <> 0 OR v_run.open_interaction_count <> 0
        OR NOT EXISTS (
            SELECT 1
              FROM agent_model_steps step
             WHERE step.run_id = p_run_id
               AND step.step_number = (
                   SELECT MAX(latest.step_number)
                     FROM agent_model_steps latest
                    WHERE latest.run_id = p_run_id
               )
               AND step.status = 'completed'
               AND step.stop_reason IN ('final', 'structured_final')
        )
    ) THEN RETURN jsonb_build_object('outcome', 'not_ready'); END IF;
    UPDATE agent_runs SET status = p_status, execution_token = NULL,
           lease_expires_at = NULL, completed_at = clock_timestamp(),
           terminal_reason = p_reason, result_hash = p_result_hash,
           state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET ended_at = clock_timestamp(),
           outcome = CASE WHEN p_status = 'completed' THEN 'completed'
                          WHEN p_status = 'cancelled' THEN 'cancelled'
                          ELSE 'failed' END
     WHERE execution_token = p_execution_token AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.' || p_status, v_run.id, NULL,
        p_execution_token, 'worker', session_user,
        jsonb_build_object('reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', p_status, 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION complete_agent_run(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_result_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    RETURN _finish_agent_run(
        p_run_id, p_execution_token, p_expected_state_version,
        'completed', p_result_hash, 'completed'
    );
END;
$$;

CREATE FUNCTION fail_agent_run(
    p_run_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    RETURN _finish_agent_run(
        p_run_id, p_execution_token, p_expected_state_version,
        'failed', NULL, p_error_code
    );
END;
$$;

CREATE FUNCTION cancel_agent_run(
    p_run_id UUID, p_expected_state_version BIGINT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM _assert_agent_runtime_actor(TRUE);
    ELSE
        PERFORM _assert_agent_runtime_actor(FALSE);
    END IF;
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status = 'cancelled' THEN
        IF v_run.terminal_reason IS DISTINCT FROM p_reason THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_cancelled', 'entity_id', v_run.id,
            'state_version', v_run.state_version
        );
    END IF;
    IF v_run.status IN ('completed', 'failed') THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF session_user <> 'everydayai_worker' AND (
        tenant_org_id() IS DISTINCT FROM v_run.org_id
        OR NOT EXISTS (
            SELECT 1 FROM agent_runtime_sessions session
             WHERE session.id = v_run.session_id
               AND (
                   (session.scope_kind = 'user'
                    AND session.user_id = tenant_actor_user_id())
                   OR (
                       session.scope_kind = 'channel'
                       AND EXISTS (
                           SELECT 1 FROM org_members member
                            WHERE member.org_id = session.org_id
                              AND member.user_id = tenant_actor_user_id()
                              AND member.status = 'active'
                       )
                   )
               )
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    UPDATE agent_runs SET status = 'cancelled', execution_token = NULL,
           lease_expires_at = NULL, completed_at = clock_timestamp(),
           terminal_reason = p_reason, state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET ended_at = clock_timestamp(),
           outcome = 'cancelled'
     WHERE run_id = p_run_id AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.cancelled', v_run.id, NULL,
        gen_random_uuid(), CASE WHEN session_user = 'everydayai_worker'
            THEN 'worker' ELSE 'user' END, session_user,
        jsonb_build_object('reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION create_model_step(
    p_run_id UUID, p_execution_token UUID, p_model_id TEXT, p_provider TEXT,
    p_model_revision TEXT, p_prompt_revision TEXT,
    p_tool_catalog_revision TEXT, p_request_receipt JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_run agent_runs%ROWTYPE;
    v_step agent_model_steps%ROWTYPE;
    v_event JSONB;
    v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token
       OR v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF jsonb_typeof(p_request_receipt) IS DISTINCT FROM 'object'
       OR pg_column_size(p_request_receipt) > 262144 THEN
        RAISE EXCEPTION 'AGENT_MODEL_STEP_INVALID' USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_model_steps(
        run_id, session_id, org_id, user_id, step_number, model_id,
        provider, model_revision, prompt_revision, tool_catalog_revision,
        request_receipt
    ) SELECT
        v_run.id, v_run.session_id, v_run.org_id, v_run.user_id,
        COALESCE(MAX(step_number), 0) + 1, p_model_id, p_provider,
        p_model_revision, p_prompt_revision, p_tool_catalog_revision,
        p_request_receipt
      FROM agent_model_steps WHERE run_id = p_run_id
    RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'model_step.created', v_run.id, v_step.id,
        p_execution_token, 'worker', session_user,
        jsonb_build_object('step_number', v_step.step_number),
        ARRAY['audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'created', 'entity_id', v_step.id,
        'state_version', v_step.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION complete_model_step(
    p_step_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_response_receipt JSONB, p_stop_reason TEXT,
    p_provider_stop_reason TEXT, p_input_tokens BIGINT,
    p_output_tokens BIGINT, p_reasoning_tokens BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_event JSONB;
    v_run_id UUID;
    v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT run_id, session_id INTO v_run_id, v_session_id
      FROM agent_model_steps WHERE id = p_step_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = p_step_id FOR UPDATE;
    IF v_step.status = 'completed' THEN
        IF v_step.response_receipt IS DISTINCT FROM p_response_receipt
           OR v_step.stop_reason IS DISTINCT FROM p_stop_reason
           OR v_step.provider_stop_reason IS DISTINCT FROM p_provider_stop_reason
           OR v_step.input_tokens IS DISTINCT FROM p_input_tokens
           OR v_step.output_tokens IS DISTINCT FROM p_output_tokens
           OR v_step.reasoning_tokens IS DISTINCT FROM p_reasoning_tokens THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_completed', 'entity_id', v_step.id,
            'state_version', v_step.state_version
        );
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token
       OR v_run.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_step.status <> 'running'
       OR v_step.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF jsonb_typeof(p_response_receipt) IS DISTINCT FROM 'object'
       OR p_stop_reason NOT IN (
           'final', 'tool_calls', 'structured_final', 'length',
           'content_filter', 'model_refusal', 'budget_exhausted',
           'cancelled', 'provider_error', 'protocol_error'
       ) OR LEAST(p_input_tokens, p_output_tokens, p_reasoning_tokens) < 0 THEN
        RAISE EXCEPTION 'AGENT_MODEL_STEP_COMPLETE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE agent_model_steps SET status = 'completed',
           response_receipt = p_response_receipt,
           stop_reason = p_stop_reason,
           provider_stop_reason = p_provider_stop_reason,
           input_tokens = p_input_tokens, output_tokens = p_output_tokens,
           reasoning_tokens = p_reasoning_tokens,
           state_version = state_version + 1,
           completed_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = p_step_id RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_step.session_id, 'model_step.completed', v_step.run_id, v_step.id,
        p_execution_token, 'worker', session_user,
        jsonb_build_object('stop_reason', p_stop_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'completed', 'entity_id', v_step.id,
        'state_version', v_step.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION fail_model_step(
    p_step_id UUID, p_execution_token UUID, p_expected_state_version BIGINT,
    p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_step agent_model_steps%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_event JSONB;
    v_run_id UUID;
    v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT run_id, session_id INTO v_run_id, v_session_id
      FROM agent_model_steps WHERE id = p_step_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_run_id FOR UPDATE;
    SELECT * INTO v_step FROM agent_model_steps
     WHERE id = p_step_id FOR UPDATE;
    IF v_step.status = 'failed' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_failed', 'entity_id', v_step.id,
            'state_version', v_step.state_version
        );
    END IF;
    IF v_run.status <> 'running'
       OR v_run.execution_token IS DISTINCT FROM p_execution_token
       OR v_step.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_step.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    UPDATE agent_model_steps SET status = 'failed',
           stop_reason = 'provider_error', terminal_reason = p_error_code,
           state_version = state_version + 1,
           completed_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = p_step_id RETURNING * INTO v_step;
    v_event := append_agent_runtime_event(
        v_step.session_id, 'model_step.failed', v_step.run_id, v_step.id,
        p_execution_token, 'worker', session_user,
        jsonb_build_object('error_code', p_error_code),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'failed', 'entity_id', v_step.id,
        'state_version', v_step.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

CREATE FUNCTION claim_agent_projection_outbox(
    p_batch_size INTEGER DEFAULT 50, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_rows JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_CLAIM_INVALID'
            USING ERRCODE = '22023';
    END IF;
    WITH candidates AS (
        SELECT id FROM agent_projection_outbox
         WHERE next_attempt_at <= clock_timestamp()
           AND (
               status = 'pending'
               OR (status = 'processing'
                   AND lease_expires_at <= clock_timestamp())
           )
         ORDER BY next_attempt_at, created_at, id
         FOR UPDATE SKIP LOCKED LIMIT p_batch_size
    ), claimed AS (
        UPDATE agent_projection_outbox outbox SET status = 'processing',
               attempt_count = attempt_count + 1,
               lease_token = gen_random_uuid(),
               lease_expires_at = clock_timestamp()
                   + make_interval(secs => p_lease_seconds),
               updated_at = clock_timestamp()
          FROM candidates WHERE outbox.id = candidates.id
        RETURNING outbox.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_rows FROM claimed;
    RETURN v_rows;
END;
$$;

CREATE FUNCTION complete_agent_projection_outbox(
    p_outbox_id UUID, p_lease_token UUID, p_checkpoint JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_row agent_projection_outbox%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_row FROM agent_projection_outbox
     WHERE id = p_outbox_id FOR UPDATE;
    IF v_row.status = 'delivered' THEN
        RETURN jsonb_build_object('outcome', 'already_completed');
    END IF;
    IF v_row.status <> 'processing'
       OR v_row.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_row.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    UPDATE agent_projection_outbox SET status = 'delivered',
           checkpoint = p_checkpoint, lease_token = NULL,
           lease_expires_at = NULL, delivered_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE id = p_outbox_id;
    RETURN jsonb_build_object('outcome', 'completed');
END;
$$;

CREATE FUNCTION fail_agent_projection_outbox(
    p_outbox_id UUID, p_lease_token UUID, p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_row agent_projection_outbox%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_row FROM agent_projection_outbox
     WHERE id = p_outbox_id FOR UPDATE;
    IF v_row.status <> 'processing'
       OR v_row.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    UPDATE agent_projection_outbox SET
           status = CASE WHEN attempt_count >= 8 THEN 'dead' ELSE 'pending' END,
           next_attempt_at = clock_timestamp()
               + make_interval(secs => LEAST(300, 5 * (2 ^ attempt_count))),
           lease_token = NULL, lease_expires_at = NULL,
           last_error_code = LEFT(p_error_code, 200),
           updated_at = clock_timestamp()
     WHERE id = p_outbox_id;
    RETURN jsonb_build_object('outcome', 'failed');
END;
$$;

REVOKE ALL ON TABLE
    agent_runtime_sessions, agent_session_commands, agent_runs,
    agent_run_attempts, agent_model_steps, agent_runtime_events,
    agent_projection_outbox
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

REVOKE ALL ON FUNCTION
    _assert_agent_runtime_actor(BOOLEAN),
    append_agent_runtime_event(
        UUID, TEXT, UUID, UUID, UUID, TEXT, TEXT, JSONB, TEXT[]
    ),
    _finish_agent_run(UUID, UUID, BIGINT, TEXT, TEXT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

REVOKE ALL ON FUNCTION
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    ),
    submit_session_command(UUID, TEXT, TEXT, JSONB),
    cancel_agent_run(UUID, BIGINT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    ),
    submit_session_command(UUID, TEXT, TEXT, JSONB),
    cancel_agent_run(UUID, BIGINT, TEXT)
TO everydayai_runtime, everydayai_wecom_runtime;

REVOKE ALL ON FUNCTION
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    renew_agent_run(UUID, UUID, INTEGER),
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT),
    wake_agent_run(UUID, BIGINT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT),
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    fail_agent_projection_outbox(UUID, UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    renew_agent_run(UUID, UUID, INTEGER),
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT),
    wake_agent_run(UUID, BIGINT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT),
    cancel_agent_run(UUID, BIGINT, TEXT),
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    fail_agent_projection_outbox(UUID, UUID, TEXT)
TO everydayai_worker;

RESET ROLE;
