-- AR-18-A1.2-B1: durable task cancellation intent and atomic facade.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_task_cancel_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,
    submit_command_id UUID NOT NULL REFERENCES agent_session_commands(id)
        ON DELETE RESTRICT,
    run_id UUID REFERENCES agent_runs(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    scope_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    requested_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL
        CHECK (length(BTRIM(idempotency_key)) BETWEEN 1 AND 200),
    request_hash TEXT NOT NULL
        CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    reason_code TEXT NOT NULL DEFAULT 'task_cancel_requested'
        CHECK (reason_code = 'task_cancel_requested'),
    status TEXT NOT NULL DEFAULT 'requested'
        CHECK (status IN ('requested', 'applied')),
    outcome TEXT CHECK (outcome IN (
        'cancelled_before_claim', 'cancelled',
        'already_cancelled', 'terminal_conflict'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    applied_at TIMESTAMPTZ,
    UNIQUE (task_id),
    UNIQUE (submit_command_id),
    UNIQUE (session_id, idempotency_key),
    CHECK (
        (status = 'requested' AND run_id IS NULL
         AND outcome IS NULL AND applied_at IS NULL)
        OR
        (status = 'applied' AND run_id IS NOT NULL
         AND outcome IS NOT NULL AND applied_at IS NOT NULL)
    )
);

CREATE INDEX idx_agent_runtime_task_cancel_intents_status
    ON agent_runtime_task_cancel_intents(status, created_at, id);

ALTER TABLE agent_runtime_task_cancel_intents ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_task_cancel_intents_owner_all
    ON agent_runtime_task_cancel_intents
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_runtime_task_cancel_intents FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _guard_agent_runtime_task_cancel_intent_identity()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.task_id IS DISTINCT FROM OLD.task_id
       OR NEW.message_id IS DISTINCT FROM OLD.message_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.submit_command_id IS DISTINCT FROM OLD.submit_command_id
       OR NEW.org_id IS DISTINCT FROM OLD.org_id
       OR NEW.scope_user_id IS DISTINCT FROM OLD.scope_user_id
       OR NEW.requested_by_user_id IS DISTINCT FROM OLD.requested_by_user_id
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
       OR NEW.reason_code IS DISTINCT FROM OLD.reason_code
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.status = 'applied'
       OR NEW.status IS DISTINCT FROM 'applied'
       OR NEW.run_id IS NULL
       OR NEW.outcome IS NULL
       OR NEW.applied_at IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_INTENT_IMMUTABLE'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER guard_agent_runtime_task_cancel_intent_identity
BEFORE UPDATE ON agent_runtime_task_cancel_intents
FOR EACH ROW EXECUTE FUNCTION
    _guard_agent_runtime_task_cancel_intent_identity();

CREATE FUNCTION _agent_runtime_task_cancel_request_hash(
    p_task_id UUID, p_message_id UUID, p_org_id UUID, p_scope_user_id UUID,
    p_requested_by_user_id UUID,
    p_session_id UUID, p_submit_command_id UUID, p_idempotency_key TEXT
) RETURNS TEXT LANGUAGE SQL IMMUTABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT encode(digest(convert_to(jsonb_build_object(
        'schema_revision', 1,
        'task_id', p_task_id,
        'message_id', p_message_id,
        'org_id', p_org_id,
        'scope_user_id', p_scope_user_id,
        'requested_by_user_id', p_requested_by_user_id,
        'session_id', p_session_id,
        'submit_command_id', p_submit_command_id,
        'idempotency_key', BTRIM(p_idempotency_key),
        'reason_code', 'task_cancel_requested'
    )::TEXT, 'UTF8'), 'sha256'), 'hex')
$$;

CREATE FUNCTION _lock_agent_runtime_task_cancel_intent(p_submit_command_id UUID)
RETURNS agent_runtime_task_cancel_intents
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_intent agent_runtime_task_cancel_intents%ROWTYPE;
BEGIN
    SELECT * INTO v_intent
      FROM agent_runtime_task_cancel_intents
     WHERE submit_command_id = p_submit_command_id
     FOR UPDATE;
    RETURN v_intent;
END;
$$;

CREATE FUNCTION _apply_agent_runtime_task_cancel_intent(
    p_intent agent_runtime_task_cancel_intents,
    p_session agent_runtime_sessions,
    p_command agent_session_commands,
    p_run agent_runs
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_envelope JSONB;
    v_run agent_runs%ROWTYPE := p_run;
    v_run_hash TEXT;
    v_cancel JSONB;
    v_outcome TEXT;
BEGIN
    v_envelope := _agent_command_run_envelope(p_command);
    IF v_envelope IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_COMMAND_INVALID'
            USING ERRCODE = '42501';
    END IF;
    v_run_hash := _agent_run_request_hash(
        p_command.id, v_envelope->>'run_kind',
        v_envelope->'context_receipt', v_envelope->'config_snapshot',
        v_envelope->'capability_snapshot');
    IF v_run.id IS NULL THEN
        INSERT INTO agent_runs(
            session_id, command_id, org_id, user_id, run_kind, status,
            idempotency_key, request_hash, context_receipt,
            config_snapshot, capability_snapshot, state_version,
            terminal_reason, completed_at
        ) VALUES (
            p_session.id, p_command.id, p_session.org_id, p_session.user_id,
            v_envelope->>'run_kind', 'cancelled', p_command.id::TEXT,
            v_run_hash, v_envelope->'context_receipt',
            v_envelope->'config_snapshot', v_envelope->'capability_snapshot',
            1, 'cancelled_before_claim', clock_timestamp()
        ) RETURNING * INTO v_run;
        UPDATE agent_session_commands
           SET result_entity_id = v_run.id
         WHERE id = p_command.id AND result_entity_id IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_RUN_RACE'
                USING ERRCODE = '40001';
        END IF;
        PERFORM append_agent_runtime_event(
            p_session.id, 'run.created', v_run.id, NULL, p_command.id,
            'user', session_user, jsonb_build_object('run_id', v_run.id),
            ARRAY['web_runtime', 'audit']::TEXT[]);
        PERFORM append_agent_runtime_event(
            p_session.id, 'run.cancelled', v_run.id, NULL, p_intent.id,
            'user', session_user,
            jsonb_build_object('reason', 'cancelled_before_claim'),
            ARRAY['web_runtime', 'audit']::TEXT[]);
        v_outcome := 'cancelled_before_claim';
    ELSE
        IF v_run.command_id IS DISTINCT FROM p_command.id
           OR v_run.session_id IS DISTINCT FROM p_session.id
           OR v_run.org_id IS DISTINCT FROM p_session.org_id
           OR v_run.user_id IS DISTINCT FROM p_session.user_id
           OR v_run.request_hash IS DISTINCT FROM v_run_hash THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_RUN_BINDING_MISMATCH'
                USING ERRCODE = '42501';
        END IF;
        v_cancel := cancel_agent_run(
            v_run.id, v_run.state_version, 'task_cancel_requested');
        v_outcome := v_cancel->>'outcome';
        IF v_outcome NOT IN (
            'cancelled', 'already_cancelled', 'terminal_conflict'
        ) THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_OUTCOME_INVALID'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    UPDATE agent_runtime_task_cancel_intents
       SET status = 'applied', run_id = v_run.id, outcome = v_outcome,
           applied_at = clock_timestamp()
     WHERE id = p_intent.id;
    RETURN jsonb_build_object(
        'outcome', v_outcome, 'intent_id', p_intent.id, 'run_id', v_run.id);
END;
$$;

CREATE FUNCTION _assert_agent_runtime_task_cancel_binding(
    p_session agent_runtime_sessions, p_command agent_session_commands,
    p_task tasks, p_task_id UUID, p_message_id UUID, p_org_id UUID,
    p_requested_by_user_id UUID, p_session_id UUID, p_submit_command_id UUID
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_conversation conversations%ROWTYPE;
BEGIN
    SELECT * INTO v_conversation FROM conversations
     WHERE id = p_task.conversation_id;
    IF p_session.id IS NULL OR p_command.id IS NULL OR p_task.id IS NULL
       OR v_conversation.id IS NULL
       OR p_session.conversation_id IS DISTINCT FROM p_task.conversation_id
       OR p_session.org_id IS DISTINCT FROM p_org_id
       OR p_command.org_id IS DISTINCT FROM p_org_id
       OR p_command.user_id IS DISTINCT FROM p_session.user_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.scope_type IS DISTINCT FROM p_session.scope_kind
       OR v_conversation.scope_id IS DISTINCT FROM p_session.scope_id
       OR p_session.scope_kind NOT IN ('user', 'channel')
       OR (
            p_session.scope_kind = 'user'
            AND (
                p_session.user_id IS DISTINCT FROM p_requested_by_user_id
                OR v_conversation.user_id IS DISTINCT FROM p_requested_by_user_id
            )
       )
       OR (
            p_session.scope_kind = 'channel'
            AND (
                p_session.user_id IS NOT NULL
                OR p_command.user_id IS NOT NULL
                OR v_conversation.user_id IS NOT NULL
                OR p_org_id IS NULL
                OR NOT EXISTS (
                    SELECT 1
                      FROM org_members member
                      JOIN organizations organization
                        ON organization.id = member.org_id
                     WHERE member.org_id = p_org_id
                       AND member.user_id = p_requested_by_user_id
                       AND member.status = 'active'
                       AND organization.status = 'active'
                )
            )
       )
       OR p_command.command_type IS DISTINCT FROM 'submit_input'
       OR p_command.request_hash IS DISTINCT FROM md5(jsonb_build_object(
            'command_type', p_command.command_type,
            'payload', p_command.payload
       )::TEXT)
       OR p_task.org_id IS DISTINCT FROM p_org_id
       OR p_task.user_id IS DISTINCT FROM p_requested_by_user_id
       OR p_task.assistant_message_id IS DISTINCT FROM p_message_id
       OR p_task.delivery_context->>'runtime_session_id'
            IS DISTINCT FROM p_session_id::TEXT
       OR p_task.delivery_context->>'runtime_command_id'
            IS DISTINCT FROM p_submit_command_id::TEXT
       OR NOT (p_task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB)
       OR p_command.payload->>'task_id' IS DISTINCT FROM p_task_id::TEXT
       OR p_command.payload->>'output_message_id' IS DISTINCT FROM p_message_id::TEXT
       OR NOT EXISTS (
            SELECT 1 FROM users requested_by
             WHERE requested_by.id = p_requested_by_user_id
               AND requested_by.status::TEXT = 'active'
       )
       OR NOT EXISTS (
            SELECT 1 FROM messages message
             WHERE message.id = p_message_id
               AND message.conversation_id = p_task.conversation_id
               AND message.org_id IS NOT DISTINCT FROM p_org_id
       )
       OR tenant_org_id() IS DISTINCT FROM p_org_id
       OR tenant_actor_user_id() IS DISTINCT FROM p_requested_by_user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_BINDING_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE FUNCTION request_agent_runtime_task_cancel_v1(
    p_task_id UUID, p_message_id UUID, p_org_id UUID, p_user_id UUID,
    p_session_id UUID, p_submit_command_id UUID,
    p_idempotency_key TEXT, p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_session agent_runtime_sessions%ROWTYPE;
    v_command agent_session_commands%ROWTYPE;
    v_claim agent_command_claims%ROWTYPE;
    v_intent agent_runtime_task_cancel_intents%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_hash TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_task_id IS NULL OR p_message_id IS NULL OR p_user_id IS NULL
       OR p_session_id IS NULL OR p_submit_command_id IS NULL
       OR length(BTRIM(COALESCE(p_idempotency_key, ''))) NOT BETWEEN 1 AND 200
       OR p_request_hash IS NULL
       OR p_request_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_TASK_CANCEL_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id FOR UPDATE;
    SELECT * INTO v_command FROM agent_session_commands
     WHERE id = p_submit_command_id AND session_id = p_session_id FOR UPDATE;
    SELECT * INTO v_claim FROM agent_command_claims
     WHERE command_id = p_submit_command_id FOR UPDATE;
    v_intent := _lock_agent_runtime_task_cancel_intent(p_submit_command_id);
    IF v_command.result_entity_id IS NOT NULL THEN
        SELECT * INTO v_run FROM agent_runs
         WHERE id = v_command.result_entity_id FOR UPDATE;
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    PERFORM _assert_agent_runtime_task_cancel_binding(
        v_session, v_command, v_task, p_task_id, p_message_id, p_org_id,
        p_user_id, p_session_id, p_submit_command_id);
    v_hash := _agent_runtime_task_cancel_request_hash(
        p_task_id, p_message_id, p_org_id, v_session.user_id, p_user_id,
        p_session_id, p_submit_command_id, p_idempotency_key);
    IF p_request_hash IS DISTINCT FROM v_hash THEN
        RETURN jsonb_build_object('outcome', 'idempotency_conflict');
    END IF;
    IF v_intent.id IS NOT NULL THEN
        IF v_intent.task_id IS DISTINCT FROM p_task_id
           OR v_intent.message_id IS DISTINCT FROM p_message_id
           OR v_intent.session_id IS DISTINCT FROM p_session_id
           OR v_intent.org_id IS DISTINCT FROM p_org_id
           OR v_intent.scope_user_id IS DISTINCT FROM v_session.user_id
           OR v_intent.requested_by_user_id IS DISTINCT FROM p_user_id
           OR v_intent.idempotency_key IS DISTINCT FROM BTRIM(p_idempotency_key)
           OR v_intent.request_hash IS DISTINCT FROM p_request_hash THEN
            RETURN jsonb_build_object('outcome', 'idempotency_conflict');
        END IF;
        IF v_intent.status = 'applied' THEN
            RETURN jsonb_build_object(
                'outcome', v_intent.outcome, 'intent_id', v_intent.id,
                'run_id', v_intent.run_id);
        END IF;
    ELSE
        INSERT INTO agent_runtime_task_cancel_intents(
            task_id, message_id, session_id, submit_command_id,
            org_id, scope_user_id, requested_by_user_id,
            idempotency_key, request_hash
        ) VALUES (
            p_task_id, p_message_id, p_session_id, p_submit_command_id,
            p_org_id, v_session.user_id, p_user_id,
            BTRIM(p_idempotency_key), p_request_hash
        ) RETURNING * INTO v_intent;
    END IF;
    RETURN _apply_agent_runtime_task_cancel_intent(
        v_intent, v_session, v_command, v_run);
END;
$$;

REVOKE ALL ON TABLE agent_runtime_task_cancel_intents
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai,
    everydayai_agent_runtime_worker, everydayai_agent_model_gateway,
    everydayai_projection_worker, everydayai_authorization_worker,
    everydayai_sandbox_worker;
REVOKE ALL ON FUNCTION
    _guard_agent_runtime_task_cancel_intent_identity(),
    _agent_runtime_task_cancel_request_hash(
        UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT),
    _lock_agent_runtime_task_cancel_intent(UUID),
    _apply_agent_runtime_task_cancel_intent(
        agent_runtime_task_cancel_intents, agent_runtime_sessions,
        agent_session_commands, agent_runs),
    _assert_agent_runtime_task_cancel_binding(
        agent_runtime_sessions,agent_session_commands,tasks,
        UUID,UUID,UUID,UUID,UUID,UUID),
    request_agent_runtime_task_cancel_v1(
        UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai,
    everydayai_agent_runtime_worker, everydayai_agent_model_gateway,
    everydayai_projection_worker, everydayai_authorization_worker,
    everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION request_agent_runtime_task_cancel_v1(
    UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT)
TO everydayai_runtime, everydayai_wecom_runtime;

RESET ROLE;
