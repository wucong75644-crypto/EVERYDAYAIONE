-- AR-17.4-A2 additive provider submission/readback/reconcile facts.
-- 227.01-227.03 remain immutable. No secret or provider payload is stored.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_provider_submission_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL UNIQUE REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
    action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user', 'channel', 'system')),
    scope_id TEXT NOT NULL CHECK (length(btrim(scope_id)) BETWEEN 1 AND 200),
    provider TEXT NOT NULL CHECK (length(btrim(provider)) BETWEEN 1 AND 200),
    provider_revision TEXT NOT NULL CHECK (length(btrim(provider_revision)) BETWEEN 1 AND 200),
    external_idempotency_key TEXT NOT NULL CHECK (
        length(btrim(external_idempotency_key)) BETWEEN 1 AND 300
    ),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    execution_token UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'submission_pending', 'submitted', 'readback_confirmed', 'accepted',
        'unknown', 'reconcile_required', 'cancel_requested', 'cancelled', 'failed'
    )),
    provider_task_ref TEXT CHECK (
        provider_task_ref IS NULL OR length(btrim(provider_task_ref)) BETWEEN 1 AND 500
    ),
    status_locator TEXT CHECK (
        status_locator IS NULL OR length(btrim(status_locator)) BETWEEN 1 AND 500
    ),
    provider_receipt_hash TEXT CHECK (
        provider_receipt_hash IS NULL OR provider_receipt_hash ~ '^[0-9a-f]{64}$'
    ),
    readback_hash TEXT CHECK (
        readback_hash IS NULL OR readback_hash ~ '^[0-9a-f]{64}$'
    ),
    ambiguity_evidence JSONB NOT NULL DEFAULT '{}'::JSONB
        CHECK (jsonb_typeof(ambiguity_evidence) = 'object'),
    next_reconcile_at TIMESTAMPTZ,
    cancel_reason TEXT CHECK (cancel_reason IS NULL OR length(btrim(cancel_reason)) BETWEEN 1 AND 200),
    cancel_requested_at TIMESTAMPTZ,
    cancel_confirmed_at TIMESTAMPTZ,
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((scope_kind = 'user' AND user_id IS NOT NULL)
        OR (scope_kind = 'channel' AND org_id IS NOT NULL)
        OR scope_kind = 'system'),
    CHECK (state <> 'accepted' OR provider_task_ref IS NOT NULL),
    CHECK (state <> 'cancelled' OR cancel_confirmed_at IS NOT NULL),
    CHECK (state <> 'unknown' OR ambiguity_evidence <> '{}'::JSONB),
    UNIQUE (scope_kind, scope_id, external_idempotency_key)
);

CREATE INDEX idx_agent_runtime_provider_facts_reconcile
    ON agent_runtime_provider_submission_facts(next_reconcile_at, updated_at)
    WHERE state IN ('accepted', 'unknown', 'reconcile_required', 'cancel_requested');
CREATE INDEX idx_agent_runtime_provider_facts_binding
    ON agent_runtime_provider_submission_facts(org_id, user_id, run_id, action_id, attempt_id);

ALTER TABLE agent_runtime_provider_submission_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_provider_submission_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_provider_submission_facts_owner_all
    ON agent_runtime_provider_submission_facts FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON TABLE agent_runtime_provider_submission_facts
    FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
         everydayai_agent_runtime_worker, everydayai_worker, everydayai_sync, everydayai;

CREATE FUNCTION _agent_runtime_provider_submission_context(
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
        RAISE EXCEPTION 'RUNTIME_PROVIDER_FACT_CONTEXT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id;
    SELECT * INTO v_session FROM agent_runtime_sessions WHERE id = v_attempt.session_id;
    IF NOT FOUND OR v_attempt.action_id IS DISTINCT FROM p_action_id
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
        RAISE EXCEPTION 'RUNTIME_PROVIDER_FACT_CONTEXT_MISMATCH' USING ERRCODE = '42501';
    END IF;
    RETURN v_attempt;
END;
$$;

CREATE FUNCTION _agent_runtime_provider_evidence_safe(p_value JSONB)
RETURNS BOOLEAN LANGUAGE SQL IMMUTABLE
SET search_path = pg_catalog, public AS $$
    SELECT jsonb_typeof(COALESCE(p_value, '{}'::JSONB)) = 'object'
       AND COALESCE(p_value::TEXT, '') !~* '(secret|token|password|credential|api[_-]?key|authorization|cookie|private[_-]?key)';
$$;

CREATE FUNCTION create_agent_runtime_provider_submission(
    p_attempt_id UUID, p_action_id UUID, p_run_id UUID,
    p_org_id UUID, p_user_id UUID, p_scope_kind TEXT, p_scope_id TEXT,
    p_execution_token UUID, p_request_hash TEXT, p_provider TEXT,
    p_provider_revision TEXT, p_external_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_action_attempts%ROWTYPE;
    v_fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    v_attempt := _agent_runtime_provider_submission_context(
        p_attempt_id, p_action_id, p_run_id, p_org_id, p_user_id,
        p_scope_kind, p_scope_id, p_execution_token, p_request_hash);
    IF NULLIF(btrim(p_provider), '') IS NULL
       OR NULLIF(btrim(p_provider_revision), '') IS NULL
       OR NULLIF(btrim(p_external_idempotency_key), '') IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_FACT_IDENTITY_INVALID' USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_runtime_provider_submission_facts(
        attempt_id, action_id, run_id, org_id, user_id, scope_kind, scope_id,
        provider, provider_revision, external_idempotency_key, request_hash,
        execution_token, state
    ) VALUES (
        p_attempt_id, p_action_id, p_run_id, p_org_id, p_user_id,
        p_scope_kind, btrim(p_scope_id), btrim(p_provider), btrim(p_provider_revision),
        btrim(p_external_idempotency_key), p_request_hash, p_execution_token,
        'submission_pending'
    ) ON CONFLICT DO NOTHING RETURNING * INTO v_fact;
    IF v_fact.id IS NOT NULL THEN
        RETURN jsonb_build_object('outcome','created','submission_id',v_fact.id,
            'state',v_fact.state,'state_version',v_fact.state_version);
    END IF;
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE attempt_id = p_attempt_id
        OR (scope_kind = p_scope_kind AND scope_id = btrim(p_scope_id)
            AND external_idempotency_key = btrim(p_external_idempotency_key))
     FOR UPDATE;
    IF v_fact.id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_FACT_IDEMPOTENCY_RACE' USING ERRCODE = '40001';
    END IF;
    IF v_fact.action_id IS DISTINCT FROM p_action_id
       OR v_fact.run_id IS DISTINCT FROM p_run_id
       OR v_fact.org_id IS DISTINCT FROM p_org_id
       OR v_fact.user_id IS DISTINCT FROM p_user_id
       OR v_fact.request_hash IS DISTINCT FROM p_request_hash
       OR v_fact.provider IS DISTINCT FROM btrim(p_provider)
       OR v_fact.provider_revision IS DISTINCT FROM btrim(p_provider_revision)
       OR v_fact.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome','idempotency_conflict');
    END IF;
    RETURN jsonb_build_object('outcome','already_applied','submission_id',v_fact.id,
        'state',v_fact.state,'state_version',v_fact.state_version,
        'provider_task_ref',v_fact.provider_task_ref);
END;
$$;

CREATE FUNCTION record_agent_runtime_provider_submitted(
    p_submission_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_expected_state_version BIGINT, p_provider_task_ref TEXT,
    p_status_locator TEXT DEFAULT NULL, p_provider_receipt_hash TEXT DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE id = p_submission_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_fact.execution_token IS DISTINCT FROM p_execution_token
       OR v_fact.request_hash IS DISTINCT FROM p_request_hash
       OR v_fact.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF v_fact.state NOT IN ('submission_pending','submitted')
       OR NULLIF(btrim(p_provider_task_ref), '') IS NULL
       OR (p_provider_receipt_hash IS NOT NULL AND p_provider_receipt_hash !~ '^[0-9a-f]{64}$') THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    UPDATE agent_runtime_provider_submission_facts SET
        state='submitted', provider_task_ref=btrim(p_provider_task_ref),
        status_locator=NULLIF(btrim(p_status_locator), ''),
        provider_receipt_hash=p_provider_receipt_hash,
        state_version=state_version+1, updated_at=clock_timestamp()
     WHERE id = p_submission_id RETURNING * INTO v_fact;
    RETURN jsonb_build_object('outcome','submitted','submission_id',v_fact.id,
        'state',v_fact.state,'state_version',v_fact.state_version);
END;
$$;

CREATE FUNCTION record_agent_runtime_provider_unknown(
    p_submission_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_expected_state_version BIGINT, p_ambiguity_evidence JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NOT _agent_runtime_provider_evidence_safe(p_ambiguity_evidence)
       OR p_ambiguity_evidence = '{}'::JSONB THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_UNKNOWN_EVIDENCE_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE id = p_submission_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_fact.execution_token IS DISTINCT FROM p_execution_token
       OR v_fact.request_hash IS DISTINCT FROM p_request_hash
       OR v_fact.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF v_fact.state NOT IN ('submission_pending','submitted','accepted','unknown','reconcile_required') THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    UPDATE agent_runtime_provider_submission_facts SET
        state='unknown', ambiguity_evidence=p_ambiguity_evidence,
        next_reconcile_at=clock_timestamp(), state_version=state_version+1,
        updated_at=clock_timestamp()
     WHERE id = p_submission_id RETURNING * INTO v_fact;
    RETURN jsonb_build_object('outcome','unknown','submission_id',v_fact.id,
        'state',v_fact.state,'state_version',v_fact.state_version);
END;
$$;

CREATE FUNCTION request_agent_runtime_provider_cancel(
    p_submission_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_expected_state_version BIGINT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE id = p_submission_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_fact.execution_token IS DISTINCT FROM p_execution_token
       OR v_fact.request_hash IS DISTINCT FROM p_request_hash
       OR v_fact.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF v_fact.state NOT IN ('submission_pending','submitted','accepted','unknown','reconcile_required','cancel_requested') THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    UPDATE agent_runtime_provider_submission_facts SET
        state='cancel_requested', cancel_reason=NULLIF(left(btrim(p_reason),200),''),
        cancel_requested_at=COALESCE(cancel_requested_at,clock_timestamp()),
        next_reconcile_at=clock_timestamp(), state_version=state_version+1,
        updated_at=clock_timestamp()
     WHERE id = p_submission_id RETURNING * INTO v_fact;
    RETURN jsonb_build_object('outcome','cancel_requested','submission_id',v_fact.id,
        'state',v_fact.state,'state_version',v_fact.state_version);
END;
$$;

CREATE FUNCTION record_agent_runtime_provider_readback(
    p_submission_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_expected_state_version BIGINT, p_provider_state TEXT,
    p_readback_hash TEXT, p_provider_task_ref TEXT DEFAULT NULL,
    p_status_locator TEXT DEFAULT NULL, p_evidence JSONB DEFAULT '{}'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fact agent_runtime_provider_submission_facts%ROWTYPE; v_state TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_provider_state NOT IN ('accepted','completed','failed','cancelled','unknown')
       OR p_readback_hash !~ '^[0-9a-f]{64}$'
       OR NOT _agent_runtime_provider_evidence_safe(p_evidence) THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_READBACK_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE id = p_submission_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_fact.execution_token IS DISTINCT FROM p_execution_token
       OR v_fact.request_hash IS DISTINCT FROM p_request_hash
       OR v_fact.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF v_fact.state NOT IN ('submitted','accepted','unknown','reconcile_required','cancel_requested') THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    v_state := CASE p_provider_state
        WHEN 'completed' THEN 'readback_confirmed'
        WHEN 'accepted' THEN 'accepted'
        WHEN 'failed' THEN 'failed'
        WHEN 'cancelled' THEN 'cancelled'
        ELSE 'unknown' END;
    IF p_provider_state = 'cancelled' AND v_fact.state <> 'cancel_requested' THEN
        RETURN jsonb_build_object('outcome','cancel_not_requested');
    END IF;
    IF p_provider_state = 'unknown' AND p_evidence = '{}'::JSONB THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_UNKNOWN_EVIDENCE_REQUIRED' USING ERRCODE = '22023';
    END IF;
    UPDATE agent_runtime_provider_submission_facts SET
        state=v_state, provider_task_ref=COALESCE(NULLIF(btrim(p_provider_task_ref),''),provider_task_ref),
        status_locator=COALESCE(NULLIF(btrim(p_status_locator),''),status_locator),
        readback_hash=p_readback_hash,
        ambiguity_evidence=CASE WHEN p_provider_state='unknown' THEN p_evidence ELSE ambiguity_evidence END,
        cancel_confirmed_at=CASE WHEN p_provider_state='cancelled' THEN clock_timestamp() ELSE cancel_confirmed_at END,
        next_reconcile_at=CASE WHEN p_provider_state IN ('accepted','unknown') THEN clock_timestamp() ELSE NULL END,
        state_version=state_version+1, updated_at=clock_timestamp()
     WHERE id = p_submission_id RETURNING * INTO v_fact;
    RETURN jsonb_build_object('outcome','readback','submission_id',v_fact.id,
        'state',v_fact.state,'state_version',v_fact.state_version);
END;
$$;

CREATE FUNCTION reconcile_agent_runtime_provider_submission(
    p_submission_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_expected_state_version BIGINT, p_resolution TEXT,
    p_readback_hash TEXT, p_evidence JSONB DEFAULT '{}'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fact agent_runtime_provider_submission_facts%ROWTYPE; v_state TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_resolution NOT IN ('readback_confirmed','accepted','failed','cancelled','unknown')
       OR (p_readback_hash IS NOT NULL AND p_readback_hash !~ '^[0-9a-f]{64}$')
       OR NOT _agent_runtime_provider_evidence_safe(p_evidence) THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_RECONCILIATION_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE id = p_submission_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_fact.execution_token IS DISTINCT FROM p_execution_token
       OR v_fact.request_hash IS DISTINCT FROM p_request_hash
       OR v_fact.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF v_fact.state NOT IN ('accepted','unknown','reconcile_required','cancel_requested','submitted') THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    IF p_resolution = 'cancelled' AND v_fact.state <> 'cancel_requested' THEN
        RETURN jsonb_build_object('outcome','cancel_not_requested');
    END IF;
    IF p_resolution = 'readback_confirmed' AND p_readback_hash IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_READBACK_HASH_REQUIRED' USING ERRCODE = '22023';
    END IF;
    IF p_resolution = 'unknown' AND p_evidence = '{}'::JSONB THEN
        v_state := 'reconcile_required';
    ELSE
        v_state := p_resolution;
    END IF;
    UPDATE agent_runtime_provider_submission_facts SET
        state=v_state, readback_hash=COALESCE(p_readback_hash,readback_hash),
        ambiguity_evidence=CASE WHEN p_resolution='unknown' THEN p_evidence ELSE ambiguity_evidence END,
        cancel_confirmed_at=CASE WHEN p_resolution='cancelled' THEN clock_timestamp() ELSE cancel_confirmed_at END,
        next_reconcile_at=CASE WHEN v_state IN ('accepted','reconcile_required','cancel_requested') THEN clock_timestamp() ELSE NULL END,
        state_version=state_version+1, updated_at=clock_timestamp()
     WHERE id = p_submission_id RETURNING * INTO v_fact;
    RETURN jsonb_build_object('outcome','reconciled','submission_id',v_fact.id,
        'state',v_fact.state,'state_version',v_fact.state_version);
END;
$$;

CREATE FUNCTION read_agent_runtime_provider_submission(
    p_submission_id UUID, p_attempt_id UUID, p_action_id UUID, p_run_id UUID,
    p_org_id UUID, p_user_id UUID, p_scope_kind TEXT, p_scope_id TEXT,
    p_execution_token UUID, p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fact agent_runtime_provider_submission_facts%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_provider_submission_context(
        p_attempt_id,p_action_id,p_run_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
        p_execution_token,p_request_hash);
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE id=p_submission_id AND attempt_id=p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    RETURN jsonb_build_object('outcome','readback','submission_id',v_fact.id,
        'state',v_fact.state,'state_version',v_fact.state_version,
        'provider',v_fact.provider,'provider_revision',v_fact.provider_revision,
        'provider_task_ref',v_fact.provider_task_ref,'status_locator',v_fact.status_locator,
        'provider_receipt_hash',v_fact.provider_receipt_hash,'readback_hash',v_fact.readback_hash,
        'next_reconcile_at',v_fact.next_reconcile_at,
        'cancel_requested_at',v_fact.cancel_requested_at,'cancel_confirmed_at',v_fact.cancel_confirmed_at);
END;
$$;

REVOKE ALL ON FUNCTION _agent_runtime_provider_submission_context(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT),
 _agent_runtime_provider_evidence_safe(JSONB),
 create_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT),
 record_agent_runtime_provider_submitted(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT),
 record_agent_runtime_provider_unknown(UUID,UUID,TEXT,BIGINT,JSONB),
 request_agent_runtime_provider_cancel(UUID,UUID,TEXT,BIGINT,TEXT),
 record_agent_runtime_provider_readback(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB),
 reconcile_agent_runtime_provider_submission(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,JSONB),
 read_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT)
 FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION create_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT),
 record_agent_runtime_provider_submitted(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT),
 record_agent_runtime_provider_unknown(UUID,UUID,TEXT,BIGINT,JSONB),
 request_agent_runtime_provider_cancel(UUID,UUID,TEXT,BIGINT,TEXT),
 record_agent_runtime_provider_readback(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB),
 reconcile_agent_runtime_provider_submission(UUID,UUID,TEXT,BIGINT,TEXT,TEXT,JSONB),
 read_agent_runtime_provider_submission(UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT)
 TO everydayai_agent_runtime_worker, everydayai_worker;

RESET ROLE;
