-- 212: Additive Agent Runtime Session/Run/ModelStep/Event foundation.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL UNIQUE
        REFERENCES conversations(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    scope_kind TEXT NOT NULL
        CHECK (scope_kind IN ('user', 'channel', 'system')),
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
        OR scope_kind = 'system'
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
    command_type TEXT NOT NULL CHECK (command_type IN (
        'submit_input', 'steer', 'cancel', 'approve',
        'reject', 'switch_agent', 'compact'
    )),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    payload JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 32),
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
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 32),
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
    UNIQUE (command_id),
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
        outcome IN (
            'completed', 'lease_lost', 'crashed', 'failed', 'cancelled'
        )
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
    status TEXT NOT NULL DEFAULT 'running' CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
    ),
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
        OR (status IN ('pending', 'running') AND completed_at IS NULL)
    )
);

CREATE TABLE agent_runtime_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    scope_kind TEXT NOT NULL
        CHECK (scope_kind IN ('user', 'channel', 'system')),
    scope_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version > 0),
    durability TEXT NOT NULL DEFAULT 'durable'
        CHECK (durability IN ('durable', 'ephemeral_compacted')),
    run_id UUID REFERENCES agent_runs(id) ON DELETE RESTRICT,
    model_step_id UUID REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
    action_id UUID,
    causation_event_id UUID REFERENCES agent_runtime_events(id)
        ON DELETE SET NULL,
    correlation_id UUID NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN (
        'user', 'system', 'model', 'executor', 'reconciler', 'admin'
    )),
    actor_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    payload_hash TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    redaction_revision TEXT NOT NULL DEFAULT 'v1',
    trace_id TEXT,
    span_id TEXT,
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
        session_id, sequence, org_id, user_id, scope_kind, scope_id,
        event_type, run_id, model_step_id, correlation_id,
        actor_type, actor_id, payload, payload_hash
    ) VALUES (
        p_session_id, v_sequence, v_session.org_id, v_session.user_id,
        v_session.scope_kind, v_session.scope_id, p_event_type, p_run_id,
        p_model_step_id, p_correlation_id, p_actor_type, p_actor_id,
        p_payload, md5(p_payload::TEXT)
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
    )
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
