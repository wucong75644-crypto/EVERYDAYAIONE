-- AR-17.4-A8 Runtime-owned scheduler CAS facts. Legacy scheduler owners are
-- deliberately not granted access to this contract.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduler_cas_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id TEXT NOT NULL CHECK (length(btrim(task_id)) BETWEEN 1 AND 300),
    org_id UUID,
    user_id UUID,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user', 'channel', 'system')),
    scope_id TEXT NOT NULL CHECK (length(btrim(scope_id)) BETWEEN 1 AND 200),
    run_id UUID NOT NULL,
    action_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    execution_token UUID NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(btrim(idempotency_key)) BETWEEN 1 AND 300),
    operation TEXT NOT NULL CHECK (operation IN (
        'create', 'update', 'delete', 'pause', 'resume', 'list',
        'cancel', 'recover'
    )),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB
        CHECK (jsonb_typeof(payload) = 'object'),
    state TEXT NOT NULL DEFAULT 'active' CHECK (
        state IN ('active', 'cancel_requested', 'cancelled', 'recovered')
    ),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    cancel_requested_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((scope_kind = 'user' AND user_id IS NOT NULL)
        OR (scope_kind = 'channel' AND org_id IS NOT NULL)
        OR scope_kind = 'system'),
    UNIQUE (scope_kind, scope_id, task_id)
);

CREATE INDEX idx_agent_runtime_scheduler_cas_recovery
    ON agent_runtime_scheduler_cas_facts(lease_expires_at, updated_at)
    WHERE state IN ('active', 'cancel_requested');

ALTER TABLE agent_runtime_scheduler_cas_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduler_cas_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_scheduler_cas_facts_owner_all
    ON agent_runtime_scheduler_cas_facts FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);

REVOKE ALL ON TABLE agent_runtime_scheduler_cas_facts
    FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
         everydayai_agent_runtime_worker, everydayai_worker, everydayai_sync,
         everydayai;

CREATE FUNCTION _agent_runtime_scheduler_cas_context(
    p_attempt_id UUID, p_action_id UUID, p_run_id UUID,
    p_org_id UUID, p_user_id UUID, p_scope_kind TEXT, p_scope_id TEXT,
    p_execution_token UUID, p_request_hash TEXT
) RETURNS agent_action_attempts LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_action_attempts%ROWTYPE;
    v_action agent_actions%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_attempt_id IS NULL OR p_action_id IS NULL OR p_run_id IS NULL
       OR p_execution_token IS NULL OR p_scope_kind NOT IN ('user','channel','system')
       OR NULLIF(btrim(p_scope_id), '') IS NULL
       OR p_request_hash IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'RUNTIME_SCHEDULER_CAS_CONTEXT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_attempt FROM agent_action_attempts WHERE id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RUNTIME_SCHEDULER_CAS_CONTEXT_MISMATCH' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id;
    SELECT * INTO v_session FROM agent_runtime_sessions WHERE id = v_attempt.session_id;
    IF v_action.id IS NULL OR v_run.id IS NULL OR v_session.id IS NULL
       OR v_attempt.action_id IS DISTINCT FROM p_action_id
       OR v_attempt.run_id IS DISTINCT FROM p_run_id
       OR v_action.run_id IS DISTINCT FROM p_run_id
       OR v_run.session_id IS DISTINCT FROM v_attempt.session_id
       OR v_attempt.org_id IS DISTINCT FROM p_org_id
       OR v_attempt.user_id IS DISTINCT FROM p_user_id
       OR v_action.org_id IS DISTINCT FROM p_org_id
       OR v_action.user_id IS DISTINCT FROM p_user_id
       OR v_run.org_id IS DISTINCT FROM p_org_id
       OR v_run.user_id IS DISTINCT FROM p_user_id
       OR v_session.scope_kind IS DISTINCT FROM p_scope_kind
       OR v_session.scope_id IS DISTINCT FROM btrim(p_scope_id)
       OR v_attempt.execution_token IS DISTINCT FROM p_execution_token
       OR v_attempt.request_hash IS DISTINCT FROM p_request_hash
       OR v_attempt.status NOT IN ('dispatching', 'accepted', 'unknown') THEN
        RAISE EXCEPTION 'RUNTIME_SCHEDULER_CAS_CONTEXT_MISMATCH' USING ERRCODE = '42501';
    END IF;
    RETURN v_attempt;
END;
$$;

CREATE FUNCTION _agent_runtime_scheduler_cas_payload_safe(p_value JSONB)
RETURNS BOOLEAN LANGUAGE SQL IMMUTABLE
SET search_path = pg_catalog, public AS $$
    SELECT jsonb_typeof(COALESCE(p_value, '{}'::JSONB)) = 'object'
       AND COALESCE(p_value::TEXT, '') !~* '(secret|token|password|credential|api[_-]?key|authorization|cookie|private[_-]?key)';
$$;

CREATE FUNCTION mutate_agent_runtime_scheduler_cas(
    p_attempt_id UUID, p_action_id UUID, p_run_id UUID,
    p_org_id UUID, p_user_id UUID, p_scope_kind TEXT, p_scope_id TEXT,
    p_task_id TEXT, p_expected_version BIGINT, p_operation TEXT,
    p_payload JSONB, p_request_hash TEXT, p_execution_token UUID,
    p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_current agent_runtime_scheduler_cas_facts%ROWTYPE;
    v_new agent_runtime_scheduler_cas_facts%ROWTYPE;
    v_state TEXT := CASE WHEN p_operation = 'cancel' THEN 'cancel_requested' ELSE 'active' END;
BEGIN
    PERFORM _agent_runtime_scheduler_cas_context(
        p_attempt_id, p_action_id, p_run_id, p_org_id, p_user_id,
        p_scope_kind, p_scope_id, p_execution_token, p_request_hash);
    IF NULLIF(btrim(p_task_id), '') IS NULL OR p_expected_version IS NULL
       OR p_expected_version < 0 OR p_operation NOT IN (
           'create','update','delete','pause','resume','list','cancel','recover')
       OR NULLIF(btrim(p_idempotency_key), '') IS NULL
       OR NOT _agent_runtime_scheduler_cas_payload_safe(p_payload) THEN
        RAISE EXCEPTION 'RUNTIME_SCHEDULER_CAS_REQUEST_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_current FROM agent_runtime_scheduler_cas_facts
     WHERE scope_kind = p_scope_kind AND scope_id = btrim(p_scope_id)
       AND task_id = btrim(p_task_id) FOR UPDATE;
    IF NOT FOUND THEN
        IF p_expected_version <> 0 OR p_operation <> 'create' THEN
            RETURN jsonb_build_object('outcome','cas_conflict');
        END IF;
        INSERT INTO agent_runtime_scheduler_cas_facts(
            task_id, org_id, user_id, scope_kind, scope_id, run_id, action_id,
            attempt_id, request_hash, execution_token, idempotency_key,
            operation, payload, state, state_version, lease_expires_at
        ) VALUES (
            btrim(p_task_id), p_org_id, p_user_id, p_scope_kind, btrim(p_scope_id),
            p_run_id, p_action_id, p_attempt_id, p_request_hash, p_execution_token,
            btrim(p_idempotency_key), p_operation, COALESCE(p_payload, '{}'::JSONB),
            v_state, 1, clock_timestamp() + interval '120 seconds'
        ) RETURNING * INTO v_new;
        RETURN jsonb_build_object('outcome','created','task_id',v_new.task_id,
            'state',v_new.state,'state_version',v_new.state_version,
            'execution_token',v_new.execution_token);
    END IF;
    IF v_current.idempotency_key = btrim(p_idempotency_key)
       AND v_current.state_version = p_expected_version + 1 THEN
        RETURN jsonb_build_object('outcome','already_applied','task_id',v_current.task_id,
            'state',v_current.state,'state_version',v_current.state_version,
            'execution_token',v_current.execution_token);
    END IF;
    IF v_current.state_version <> p_expected_version THEN
        RETURN jsonb_build_object('outcome','cas_conflict','state_version',v_current.state_version);
    END IF;
    IF v_current.execution_token IS DISTINCT FROM p_execution_token
       AND v_current.lease_expires_at > clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    UPDATE agent_runtime_scheduler_cas_facts SET
        org_id=p_org_id, user_id=p_user_id, run_id=p_run_id, action_id=p_action_id,
        attempt_id=p_attempt_id, request_hash=p_request_hash,
        execution_token=p_execution_token, idempotency_key=btrim(p_idempotency_key),
        operation=p_operation, payload=COALESCE(p_payload, '{}'::JSONB), state=v_state,
        state_version=state_version+1,
        lease_expires_at=clock_timestamp() + interval '120 seconds',
        cancel_requested_at=CASE WHEN p_operation='cancel'
            THEN COALESCE(cancel_requested_at, clock_timestamp()) ELSE cancel_requested_at END,
        updated_at=clock_timestamp()
     WHERE id=v_current.id RETURNING * INTO v_new;
    RETURN jsonb_build_object('outcome','updated','task_id',v_new.task_id,
        'state',v_new.state,'state_version',v_new.state_version,
        'execution_token',v_new.execution_token);
END;
$$;

CREATE FUNCTION recover_agent_runtime_scheduler_cas(
    p_task_id TEXT, p_scope_kind TEXT, p_scope_id TEXT,
    p_expected_version BIGINT, p_execution_token UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_current agent_runtime_scheduler_cas_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_current FROM agent_runtime_scheduler_cas_facts
     WHERE task_id=btrim(p_task_id) AND scope_kind=p_scope_kind
       AND scope_id=btrim(p_scope_id) FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_current.state_version <> p_expected_version
       OR v_current.lease_expires_at > clock_timestamp()
       OR p_execution_token IS NULL THEN
        RETURN jsonb_build_object('outcome','cas_conflict');
    END IF;
    UPDATE agent_runtime_scheduler_cas_facts SET
        state='recovered', execution_token=p_execution_token,
        state_version=state_version+1,
        lease_expires_at=clock_timestamp()+interval '120 seconds',
        updated_at=clock_timestamp()
     WHERE id=v_current.id RETURNING * INTO v_current;
    RETURN jsonb_build_object('outcome','recovered','task_id',v_current.task_id,
        'state_version',v_current.state_version,'execution_token',v_current.execution_token);
END;
$$;

REVOKE ALL ON FUNCTION _agent_runtime_scheduler_cas_context(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT),
    _agent_runtime_scheduler_cas_payload_safe(JSONB),
    mutate_agent_runtime_scheduler_cas(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,JSONB,TEXT,UUID,TEXT),
    recover_agent_runtime_scheduler_cas(TEXT,TEXT,TEXT,BIGINT,UUID)
    FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
         everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION mutate_agent_runtime_scheduler_cas(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,JSONB,TEXT,UUID,TEXT),
    recover_agent_runtime_scheduler_cas(TEXT,TEXT,TEXT,BIGINT,UUID)
    TO everydayai_agent_runtime_worker;

RESET ROLE;
