-- 220_24: Atomically bind authorization facts to one fenced dispatch intent.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE agent_action_attempts
    ALTER COLUMN execution_token DROP NOT NULL,
    ALTER COLUMN lease_expires_at DROP NOT NULL;
CREATE TABLE agent_action_dispatch_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL UNIQUE
        REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
    action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    policy_receipt_id UUID NOT NULL
        REFERENCES agent_policy_receipts(id) ON DELETE RESTRICT,
    grant_id UUID REFERENCES agent_authorization_grants(id) ON DELETE RESTRICT,
    execution_token UUID NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    executor_type TEXT NOT NULL CHECK (
        executor_type = btrim(executor_type)
        AND length(executor_type) BETWEEN 1 AND 200
    ),
    executor_revision INTEGER NOT NULL CHECK (executor_revision > 0),
    policy_revision TEXT NOT NULL CHECK (
        policy_revision = btrim(policy_revision)
        AND length(policy_revision) BETWEEN 1 AND 200
    ),
    external_idempotency_key TEXT NOT NULL UNIQUE CHECK (
        length(external_idempotency_key) BETWEEN 1 AND 300
    ),
    recovery_mode TEXT NOT NULL CHECK (
        recovery_mode IN ('idempotent_replay', 'reconcile_only')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE agent_action_dispatch_intents ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_action_dispatch_intents_owner_all
    ON agent_action_dispatch_intents
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_action_dispatch_intents FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE agent_action_dispatch_intents
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
CREATE FUNCTION _recompute_agent_run_wait_state(p_run_id UUID)
RETURNS agent_runs LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE;
BEGIN
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status IN ('completed', 'failed', 'cancelled') THEN
        RETURN v_run;
    END IF;
    UPDATE agent_runs SET status = CASE
               WHEN open_interaction_count > 0 THEN 'waiting_interaction'
               WHEN blocking_action_count > 0 THEN 'waiting_actions'
               ELSE 'queued' END,
           execution_token = NULL, lease_expires_at = NULL,
           state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    RETURN v_run;
END;
$$;
CREATE FUNCTION _close_agent_authorization_action(
    p_action_id UUID, p_reason TEXT, p_event_type TEXT DEFAULT 'action.rejected'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_action agent_actions%ROWTYPE;
    v_attempt agent_action_attempts%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_event JSONB;
BEGIN
    SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_action.run_id;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE action_id = v_action.id AND status = 'claimed'
     ORDER BY attempt_number DESC LIMIT 1;
    IF v_attempt.id IS NOT NULL THEN
        UPDATE agent_action_attempts SET status = 'cancelled',
               execution_token = NULL, lease_expires_at = NULL,
               reconciliation_token = NULL,
               reconciliation_lease_expires_at = NULL,
               state_version = state_version + 1,
               ended_at = clock_timestamp(), updated_at = clock_timestamp()
         WHERE id = v_attempt.id;
    END IF;
    IF v_action.status NOT IN (
        'awaiting_authorization', 'queued', 'running'
    ) OR EXISTS (
        SELECT 1 FROM agent_action_dispatch_intents
         WHERE action_id = v_action.id
    ) THEN
        RETURN jsonb_build_object('outcome', 'not_closable');
    END IF;
    UPDATE agent_actions SET status = 'rejected',
           terminal_reason = LEFT(p_reason, 200),
           state_version = state_version + 1,
           completed_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = v_action.id RETURNING * INTO v_action;
    IF v_action.blocking THEN
        IF v_run.blocking_action_count <= 0 THEN
            RAISE EXCEPTION 'AGENT_ACTION_BLOCKER_UNDERFLOW'
                USING ERRCODE = '55000';
        END IF;
        UPDATE agent_runs SET
               blocking_action_count = blocking_action_count - 1,
               state_version = state_version + 1,
               updated_at = clock_timestamp()
         WHERE id = v_run.id;
    END IF;
    v_run := _recompute_agent_run_wait_state(v_run.id);
    v_event := append_agent_runtime_event(
        v_action.session_id, p_event_type, v_action.run_id,
        v_action.model_step_id, v_action.id, 'system', session_user,
        jsonb_build_object('action_id', v_action.id, 'reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]);
    IF v_run.status = 'queued' THEN
        PERFORM append_agent_runtime_event(
            v_run.session_id, 'run.resumed', v_run.id,
            v_action.model_step_id, v_action.id, 'system', session_user,
            '{}'::JSONB, ARRAY['web_runtime', 'audit']::TEXT[]);
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'rejected', 'action_id', v_action.id,
        'blocking_action_count', v_run.blocking_action_count,
        'run_status', v_run.status,
        'event_sequence', v_event->'event_sequence');
END;
$$;
CREATE FUNCTION _reject_agent_action_before_dispatch_gate(
    p_action_id UUID, p_attempt_id UUID, p_reason TEXT, p_outcome TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_action agent_actions%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_event JSONB;
BEGIN
    SELECT * INTO v_action FROM agent_actions WHERE id = p_action_id;
    SELECT * INTO v_run FROM agent_runs WHERE id = v_action.run_id;
    UPDATE agent_action_attempts SET status = 'cancelled',
           execution_token = NULL, lease_expires_at = NULL,
           reconciliation_token = NULL,
           reconciliation_lease_expires_at = NULL,
           state_version = state_version + 1,
           ended_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = p_attempt_id AND status = 'claimed';
    UPDATE agent_actions SET status = 'rejected',
           terminal_reason = LEFT(p_reason, 200),
           state_version = state_version + 1,
           completed_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE id = p_action_id AND status = 'running'
     RETURNING * INTO v_action;
    IF FOUND AND v_action.blocking THEN
        IF v_run.blocking_action_count <= 0 THEN
            RAISE EXCEPTION 'AGENT_ACTION_BLOCKER_UNDERFLOW'
                USING ERRCODE = '55000';
        END IF;
        UPDATE agent_runs SET
               blocking_action_count = blocking_action_count - 1,
               status = CASE
                   WHEN blocking_action_count - 1 = 0
                        AND open_interaction_count = 0 THEN 'queued'
                   WHEN open_interaction_count > 0 THEN 'waiting_interaction'
                   ELSE 'waiting_actions' END,
               state_version = state_version + 1,
               updated_at = clock_timestamp()
         WHERE id = v_run.id RETURNING * INTO v_run;
        v_event := append_agent_runtime_event(
            v_action.session_id, 'action.rejected', v_action.run_id,
            v_action.model_step_id, v_action.id, 'system', session_user,
            jsonb_build_object(
                'action_id', v_action.id, 'reason', p_reason),
            ARRAY['web_runtime', 'audit']::TEXT[]);
        IF v_run.status = 'queued' THEN
            PERFORM append_agent_runtime_event(
                v_run.session_id, 'run.resumed', v_run.id,
                v_action.model_step_id, v_action.id, 'system', session_user,
                '{}'::JSONB, ARRAY['web_runtime', 'audit']::TEXT[]);
        END IF;
    END IF;
    RETURN jsonb_build_object(
        'outcome', p_outcome, 'action_id', p_action_id,
        'state_version', (
            SELECT state_version FROM agent_action_attempts
             WHERE id = p_attempt_id),
        'blocking_action_count', v_run.blocking_action_count,
        'run_status', v_run.status,
        'event_sequence', v_event->'event_sequence');
END;
$$;
ALTER FUNCTION record_agent_policy_receipt(
    UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
    TEXT[], TEXT[], TEXT, INTEGER
) RENAME TO _record_agent_policy_receipt_220_22;
REVOKE ALL ON FUNCTION _record_agent_policy_receipt_220_22(
    UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
    TEXT[], TEXT[], TEXT, INTEGER
)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
CREATE FUNCTION record_agent_policy_receipt(
    p_action_id UUID, p_arguments_hash TEXT,
    p_executor_type TEXT, p_executor_revision INTEGER,
    p_policy_revision TEXT, p_decision TEXT, p_grant_id UUID,
    p_effective_scope JSONB, p_reason_codes TEXT[],
    p_obligations TEXT[], p_receipt_hash TEXT,
    p_ttl_seconds INTEGER DEFAULT 300
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_action agent_actions%ROWTYPE;
    v_grant agent_authorization_grants%ROWTYPE;
    v_receipt agent_policy_receipts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_decision NOT IN ('allow', 'require_authorization', 'deny')
       OR p_arguments_hash !~ '^[0-9a-f]{64}$'
       OR p_receipt_hash !~ '^[0-9a-f]{64}$'
       OR p_executor_revision < 1 OR p_ttl_seconds NOT BETWEEN 5 AND 3600
       OR NULLIF(btrim(p_executor_type), '') IS NULL
       OR NULLIF(btrim(p_policy_revision), '') IS NULL
       OR cardinality(p_reason_codes) < 1
       OR jsonb_typeof(p_effective_scope) IS DISTINCT FROM 'object'
       OR NOT _agent_action_json_is_safe(p_effective_scope) THEN
        RAISE EXCEPTION 'AGENT_POLICY_INVALID_RECEIPT'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_action FROM agent_actions
     WHERE id = p_action_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_receipt FROM agent_policy_receipts
     WHERE action_id = p_action_id
       AND arguments_hash = p_arguments_hash
       AND executor_type = p_executor_type
       AND executor_revision = p_executor_revision
       AND policy_revision = p_policy_revision FOR UPDATE;
    IF FOUND THEN
        IF v_receipt.receipt_hash IS DISTINCT FROM p_receipt_hash THEN
            RETURN jsonb_build_object('outcome', 'receipt_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_recorded', 'receipt', to_jsonb(v_receipt));
    END IF;
    IF v_action.arguments_hash IS DISTINCT FROM p_arguments_hash THEN
        RETURN jsonb_build_object('outcome', 'arguments_conflict');
    END IF;
    IF p_grant_id IS NOT NULL THEN
        SELECT * INTO v_grant FROM agent_authorization_grants
         WHERE id = p_grant_id FOR UPDATE;
        IF NOT FOUND OR p_decision <> 'allow'
           OR v_grant.session_id <> v_action.session_id
           OR v_grant.org_id IS DISTINCT FROM v_action.org_id THEN
            RETURN jsonb_build_object('outcome', 'grant_invalid');
        END IF;
    END IF;
    INSERT INTO agent_policy_receipts(
        action_id, session_id, run_id, org_id, user_id, grant_id, decision,
        arguments_hash, executor_type, executor_revision, policy_revision,
        effective_scope, reason_codes, obligations, receipt_hash, expires_at
    ) VALUES (
        v_action.id, v_action.session_id, v_action.run_id,
        v_action.org_id, v_action.user_id, p_grant_id, p_decision,
        p_arguments_hash, btrim(p_executor_type), p_executor_revision,
        btrim(p_policy_revision), p_effective_scope, p_reason_codes,
        COALESCE(p_obligations, '{}'), p_receipt_hash,
        clock_timestamp() + make_interval(secs => p_ttl_seconds)
    ) RETURNING * INTO v_receipt;
    RETURN jsonb_build_object(
        'outcome', 'recorded', 'receipt', to_jsonb(v_receipt));
END;
$$;
CREATE FUNCTION gate_agent_action_dispatch(
    p_attempt_id UUID, p_execution_token UUID,
    p_expected_attempt_version BIGINT, p_request_hash TEXT,
    p_policy_receipt_id UUID, p_executor_type TEXT,
    p_executor_revision INTEGER, p_policy_revision TEXT,
    p_recovery_mode TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_attempt agent_action_attempts%ROWTYPE;
    v_action agent_actions%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_interaction agent_interactions%ROWTYPE;
    v_grant agent_authorization_grants%ROWTYPE;
    v_receipt agent_policy_receipts%ROWTYPE;
    v_use agent_authorization_grant_uses%ROWTYPE;
    v_intent agent_action_dispatch_intents%ROWTYPE;
    v_key TEXT;
    v_reason TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_request_hash !~ '^[0-9a-f]{64}$'
       OR NULLIF(btrim(p_executor_type), '') IS NULL
       OR p_executor_revision < 1
       OR NULLIF(btrim(p_policy_revision), '') IS NULL
       OR p_recovery_mode NOT IN ('idempotent_replay', 'reconcile_only') THEN
        RAISE EXCEPTION 'AGENT_DISPATCH_GATE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_action FROM agent_actions WHERE id = v_attempt.action_id;
    SELECT * INTO v_receipt FROM agent_policy_receipts
     WHERE id = p_policy_receipt_id;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = v_attempt.session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs
     WHERE id = v_attempt.run_id FOR UPDATE;
    SELECT * INTO v_action FROM agent_actions
     WHERE id = v_attempt.action_id FOR UPDATE;
    SELECT * INTO v_attempt FROM agent_action_attempts
     WHERE id = p_attempt_id FOR UPDATE;
    SELECT * INTO v_interaction FROM agent_interactions
     WHERE action_id = v_action.id FOR UPDATE;
    IF v_receipt.grant_id IS NOT NULL THEN
        SELECT * INTO v_grant FROM agent_authorization_grants
         WHERE id = v_receipt.grant_id FOR UPDATE;
        SELECT * INTO v_use FROM agent_authorization_grant_uses
         WHERE action_id = v_action.id FOR UPDATE;
    END IF;
    SELECT * INTO v_receipt FROM agent_policy_receipts
     WHERE id = p_policy_receipt_id FOR UPDATE;
    SELECT * INTO v_intent FROM agent_action_dispatch_intents
     WHERE attempt_id = p_attempt_id FOR UPDATE;
    v_key := 'action:' || v_action.id::TEXT || ':' || p_request_hash;
    IF v_intent.id IS NOT NULL THEN
        IF v_intent.execution_token IS DISTINCT FROM p_execution_token
           OR v_intent.request_hash IS DISTINCT FROM p_request_hash
           OR v_intent.policy_receipt_id IS DISTINCT FROM p_policy_receipt_id
           OR v_intent.executor_type IS DISTINCT FROM btrim(p_executor_type)
           OR v_intent.executor_revision IS DISTINCT FROM p_executor_revision
           OR v_intent.policy_revision IS DISTINCT FROM btrim(p_policy_revision)
           OR v_intent.recovery_mode IS DISTINCT FROM p_recovery_mode THEN
            RETURN jsonb_build_object('outcome', 'dispatch_intent_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_authorized', 'intent_id', v_intent.id,
            'state_version', v_attempt.state_version,
            'external_idempotency_key', v_intent.external_idempotency_key,
            'recovery_mode', v_intent.recovery_mode);
    END IF;
    IF v_attempt.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_attempt.lease_expires_at IS NULL
       OR v_attempt.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_attempt.state_version <> p_expected_attempt_version
       OR v_attempt.status <> 'claimed'
       OR v_action.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_attempt.request_hash IS DISTINCT FROM p_request_hash
       OR v_action.request_hash IS DISTINCT FROM p_request_hash THEN
        RETURN jsonb_build_object('outcome', 'request_hash_conflict');
    END IF;
    IF v_receipt.id IS NULL
       OR v_receipt.action_id <> v_action.id
       OR v_receipt.session_id <> v_session.id
       OR v_receipt.run_id <> v_run.id
       OR v_receipt.arguments_hash <> v_action.arguments_hash
       OR v_receipt.decision <> 'allow' THEN
        v_reason := 'authorization_receipt_conflict';
        RETURN _reject_agent_action_before_dispatch_gate(
            v_action.id, v_attempt.id, v_reason, 'receipt_conflict');
    END IF;
    IF v_receipt.expires_at <= clock_timestamp() THEN
        RETURN _reject_agent_action_before_dispatch_gate(
            v_action.id, v_attempt.id, 'authorization_receipt_expired',
            'receipt_expired');
    END IF;
    IF v_receipt.executor_type <> btrim(p_executor_type)
       OR v_receipt.executor_revision <> p_executor_revision
       OR v_receipt.policy_revision <> btrim(p_policy_revision) THEN
        RETURN _reject_agent_action_before_dispatch_gate(
            v_action.id, v_attempt.id, 'executor_revision_conflict',
            'executor_revision_conflict');
    END IF;
    IF v_receipt.org_id IS DISTINCT FROM v_action.org_id
       OR v_receipt.user_id IS DISTINCT FROM v_action.user_id THEN
        RETURN _reject_agent_action_before_dispatch_gate(
            v_action.id, v_attempt.id, 'authorization_scope_mismatch',
            'scope_mismatch');
    END IF;
    IF v_receipt.grant_id IS NOT NULL THEN
        IF v_grant.id IS NULL OR v_grant.session_id <> v_action.session_id
           OR v_grant.org_id IS DISTINCT FROM v_action.org_id THEN
            RETURN _reject_agent_action_before_dispatch_gate(
                v_action.id, v_attempt.id, 'authorization_grant_invalid',
                'grant_invalid');
        END IF;
        IF v_grant.status = 'revoked' THEN
            RETURN _reject_agent_action_before_dispatch_gate(
                v_action.id, v_attempt.id, 'authorization_revoked',
                'grant_revoked');
        END IF;
        IF v_grant.status = 'expired'
           OR v_grant.expires_at <= clock_timestamp() THEN
            RETURN _reject_agent_action_before_dispatch_gate(
                v_action.id, v_attempt.id, 'authorization_expired',
                'grant_expired');
        END IF;
        IF v_grant.grant_kind = 'action' AND (
            v_grant.action_id <> v_action.id
            OR v_grant.arguments_hash <> v_action.arguments_hash
        ) THEN
            RETURN _reject_agent_action_before_dispatch_gate(
                v_action.id, v_attempt.id, 'authorization_grant_invalid',
                'grant_invalid');
        END IF;
        IF v_use.action_id IS NOT NULL AND (
            v_use.grant_id <> v_grant.id
            OR v_use.arguments_hash <> v_action.arguments_hash
        ) THEN
            RETURN _reject_agent_action_before_dispatch_gate(
                v_action.id, v_attempt.id, 'authorization_grant_replay',
                'grant_replay_conflict');
        END IF;
        INSERT INTO agent_authorization_grant_uses(
            grant_id, action_id, arguments_hash
        ) VALUES (v_grant.id, v_action.id, v_action.arguments_hash)
        ON CONFLICT (action_id) DO NOTHING;
        SELECT * INTO v_use FROM agent_authorization_grant_uses
         WHERE action_id = v_action.id FOR UPDATE;
        IF v_use.grant_id IS DISTINCT FROM v_grant.id
           OR v_use.arguments_hash IS DISTINCT FROM v_action.arguments_hash THEN
            RETURN _reject_agent_action_before_dispatch_gate(
                v_action.id, v_attempt.id, 'authorization_grant_replay',
                'grant_replay_conflict');
        END IF;
    END IF;
    INSERT INTO agent_action_dispatch_intents(
        attempt_id, action_id, policy_receipt_id, grant_id,
        execution_token, request_hash, executor_type, executor_revision,
        policy_revision, external_idempotency_key, recovery_mode
    ) VALUES (
        v_attempt.id, v_action.id, v_receipt.id, v_receipt.grant_id,
        p_execution_token, p_request_hash, btrim(p_executor_type),
        p_executor_revision, btrim(p_policy_revision), v_key, p_recovery_mode
    ) RETURNING * INTO v_intent;
    UPDATE agent_action_attempts SET status = 'dispatching',
           dispatch_phase = 'request_started',
           dispatched_at = clock_timestamp(),
           state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = v_attempt.id RETURNING * INTO v_attempt;
    RETURN jsonb_build_object(
        'outcome', 'dispatch_authorized', 'intent_id', v_intent.id,
        'state_version', v_attempt.state_version,
        'external_idempotency_key', v_intent.external_idempotency_key,
        'recovery_mode', v_intent.recovery_mode);
END;
$$;
CREATE FUNCTION get_agent_action_dispatch_intent(
    p_attempt_id UUID, p_worker_id TEXT
) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT CASE WHEN intent.id IS NULL
        THEN jsonb_build_object('outcome', 'not_found')
        ELSE jsonb_build_object(
            'outcome', 'found', 'intent', to_jsonb(intent))
        END
      FROM (SELECT 1) seed
      LEFT JOIN agent_action_dispatch_intents intent
        ON intent.attempt_id = p_attempt_id
      LEFT JOIN agent_action_attempts attempt ON attempt.id = intent.attempt_id
     WHERE intent.id IS NULL OR attempt.worker_id = btrim(p_worker_id)
$$;
REVOKE ALL ON FUNCTION
    _recompute_agent_run_wait_state(UUID),
    _close_agent_authorization_action(UUID, TEXT, TEXT),
    _reject_agent_action_before_dispatch_gate(UUID, UUID, TEXT, TEXT),
    record_agent_policy_receipt(
        UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
        TEXT[], TEXT[], TEXT, INTEGER),
    gate_agent_action_dispatch(
        UUID, UUID, BIGINT, TEXT, UUID, TEXT, INTEGER, TEXT, TEXT),
    get_agent_action_dispatch_intent(UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE EXECUTE ON FUNCTION
    mark_agent_action_dispatching(UUID, UUID, BIGINT, TEXT)
FROM everydayai_worker;
GRANT EXECUTE ON FUNCTION
    record_agent_policy_receipt(
        UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
        TEXT[], TEXT[], TEXT, INTEGER),
    gate_agent_action_dispatch(
        UUID, UUID, BIGINT, TEXT, UUID, TEXT, INTEGER, TEXT, TEXT),
    get_agent_action_dispatch_intent(UUID, TEXT)
TO everydayai_worker;
RESET ROLE;
