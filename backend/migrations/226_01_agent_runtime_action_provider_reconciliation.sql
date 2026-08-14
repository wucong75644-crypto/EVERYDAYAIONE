-- 226_01: provider identity, fencing and reconcile-only recovery.
SET LOCAL ROLE everydayai_owner;

ALTER TABLE agent_action_attempts
    ADD COLUMN provider TEXT,
    ADD COLUMN provider_task_ref TEXT,
    ADD COLUMN provider_status_locator TEXT,
    ADD COLUMN provider_idempotency_key TEXT,
    ADD COLUMN callback_correlation TEXT,
    ADD COLUMN provider_request_hash TEXT,
    ADD COLUMN next_reconcile_at TIMESTAMPTZ,
    ADD COLUMN last_provider_status TEXT,
    ADD COLUMN cancel_requested_at TIMESTAMPTZ,
    ADD COLUMN cancel_confirmed_at TIMESTAMPTZ,
    ADD COLUMN late_receipt_hash TEXT;

ALTER TABLE agent_action_attempts ADD CONSTRAINT agent_attempt_provider_pair
    CHECK ((provider IS NULL) = (provider_task_ref IS NULL));
ALTER TABLE agent_action_attempts ADD CONSTRAINT agent_attempt_provider_hash
    CHECK (provider_request_hash IS NULL OR provider_request_hash ~ '^[0-9a-f]{64}$');
CREATE UNIQUE INDEX uq_agent_attempt_provider_idempotency
    ON agent_action_attempts(provider_idempotency_key)
    WHERE provider_idempotency_key IS NOT NULL;
CREATE INDEX idx_agent_attempt_reconcile_due
    ON agent_action_attempts(next_reconcile_at, id)
    WHERE status IN ('accepted', 'unknown');
CREATE INDEX idx_agent_attempt_provider_ref
    ON agent_action_attempts(provider, provider_task_ref)
    WHERE provider_task_ref IS NOT NULL;
CREATE INDEX idx_agent_attempt_callback_correlation
    ON agent_action_attempts(callback_correlation)
    WHERE callback_correlation IS NOT NULL;

CREATE FUNCTION _agent_runtime_226_append_action_event(
    p_action_id UUID, p_event_type TEXT, p_payload JSONB
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_actions%ROWTYPE;
BEGIN
    SELECT * INTO a FROM agent_actions WHERE id=p_action_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_ACTION_EVENT_BINDING_INVALID'; END IF;
    PERFORM append_agent_runtime_event(
        a.session_id, p_event_type, a.run_id, a.model_step_id, a.id,
        'executor', current_user, COALESCE(p_payload,'{}'), ARRAY['web_runtime','wecom']
    );
END; $$;

CREATE FUNCTION record_agent_action_provider_submission(
    p_attempt_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_provider TEXT, p_provider_task_ref TEXT, p_status_locator TEXT,
    p_callback_correlation TEXT, p_provider_idempotency_key TEXT,
    p_provider_request_hash TEXT, p_next_reconcile_at TIMESTAMPTZ,
    p_external_receipt JSONB DEFAULT '{}'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_action_attempts%ROWTYPE; r JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF a.execution_token IS DISTINCT FROM p_execution_token
       OR a.request_hash IS DISTINCT FROM p_request_hash
       OR a.status NOT IN ('dispatching','accepted','unknown') THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM agent_action_dispatch_intents intent
          JOIN agent_policy_receipts receipt ON receipt.id = intent.policy_receipt_id
         WHERE intent.attempt_id = a.id
           AND intent.action_id = a.action_id
           AND intent.execution_token = p_execution_token
           AND intent.request_hash = p_request_hash
           AND receipt.action_id = a.action_id
           AND receipt.expires_at > clock_timestamp()
    ) THEN
        RETURN jsonb_build_object('outcome','dispatch_contract_missing');
    END IF;
    IF NULLIF(btrim(p_provider),'') IS NULL OR NULLIF(btrim(p_provider_task_ref),'') IS NULL
       OR p_provider_request_hash IS DISTINCT FROM p_request_hash THEN
        RAISE EXCEPTION 'AGENT_PROVIDER_RECEIPT_INVALID' USING ERRCODE='22023';
    END IF;
    UPDATE agent_action_attempts SET status='accepted', dispatch_phase='accepted',
        provider=btrim(p_provider), provider_task_ref=btrim(p_provider_task_ref),
        provider_status_locator=NULLIF(btrim(p_status_locator),''),
        callback_correlation=NULLIF(btrim(p_callback_correlation),''),
        provider_idempotency_key=btrim(p_provider_idempotency_key),
        provider_request_hash=p_provider_request_hash,
        next_reconcile_at=p_next_reconcile_at, last_provider_status='accepted',
        external_receipt=p_external_receipt, accepted_at=clock_timestamp(),
        state_version=state_version+1, updated_at=clock_timestamp()
      WHERE id=p_attempt_id RETURNING to_jsonb(agent_action_attempts.*) INTO r;
    UPDATE agent_actions SET status='accepted', accepted_at=clock_timestamp(),
        state_version=state_version+1, updated_at=clock_timestamp()
      WHERE id=a.action_id AND status='running';
    PERFORM _agent_runtime_226_append_action_event(a.action_id,'action.provider.accepted',jsonb_build_object('provider',p_provider,'provider_task_ref',p_provider_task_ref));
    RETURN jsonb_build_object('outcome','accepted','attempt',r);
END; $$;

CREATE FUNCTION record_agent_action_unknown(
    p_attempt_id UUID, p_execution_token UUID, p_request_hash TEXT,
    p_ambiguity_evidence JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF a.execution_token IS DISTINCT FROM p_execution_token OR a.request_hash IS DISTINCT FROM p_request_hash
       OR jsonb_typeof(p_ambiguity_evidence) IS DISTINCT FROM 'object'
       OR p_ambiguity_evidence='{}'::JSONB THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents intent WHERE intent.attempt_id=a.id AND intent.action_id=a.action_id AND intent.execution_token=p_execution_token AND intent.request_hash=p_request_hash) THEN
        RETURN jsonb_build_object('outcome','dispatch_contract_missing');
    END IF;
    UPDATE agent_action_attempts SET status='unknown', ambiguity_evidence=p_ambiguity_evidence,
        last_provider_status='unknown', next_reconcile_at=clock_timestamp(),
        state_version=state_version+1, updated_at=clock_timestamp() WHERE id=p_attempt_id;
    UPDATE agent_actions SET status='unknown', state_version=state_version+1,
        updated_at=clock_timestamp() WHERE id=a.action_id AND status IN ('running','accepted');
    PERFORM _agent_runtime_226_append_action_event(a.action_id,'action.provider.unknown',p_ambiguity_evidence);
    RETURN jsonb_build_object('outcome','unknown','attempt_id',p_attempt_id);
END; $$;

CREATE FUNCTION resolve_agent_action_provider_reconciliation(
    p_attempt_id UUID, p_reconciliation_token UUID, p_request_hash TEXT,
    p_resolution TEXT, p_result JSONB DEFAULT NULL,
    p_ambiguity_evidence JSONB DEFAULT '{}'
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_action_attempts%ROWTYPE; target TEXT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_resolution NOT IN ('completed','failed','cancelled','unknown') THEN RAISE EXCEPTION 'AGENT_RECONCILIATION_INVALID'; END IF;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND OR a.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR a.request_hash IS DISTINCT FROM p_request_hash OR a.status NOT IN ('accepted','unknown')
       OR (p_resolution='unknown' AND p_ambiguity_evidence='{}'::JSONB) THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
    target:=p_resolution;
    UPDATE agent_action_attempts SET status=target, ended_at=CASE WHEN target IN ('completed','failed','cancelled') THEN clock_timestamp() ELSE NULL END,
      ambiguity_evidence=CASE WHEN target='unknown' THEN p_ambiguity_evidence ELSE ambiguity_evidence END,
      reconciliation_token=NULL, reconciliation_lease_expires_at=NULL, state_version=state_version+1, updated_at=clock_timestamp()
      WHERE id=p_attempt_id;
    UPDATE agent_actions SET status=target, completed_at=CASE WHEN target IN ('completed','failed','cancelled') THEN clock_timestamp() ELSE NULL END,
      state_version=state_version+1, updated_at=clock_timestamp() WHERE id=a.action_id;
    PERFORM _agent_runtime_226_append_action_event(a.action_id,'action.provider.reconciled',jsonb_build_object('resolution',target));
    RETURN jsonb_build_object('outcome',target,'attempt_id',p_attempt_id);
END; $$;

REVOKE ALL ON FUNCTION record_agent_action_provider_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB),
 record_agent_action_unknown(UUID,UUID,TEXT,JSONB), claim_agent_action_reconciliation(UUID,BIGINT,TEXT,INTEGER),
 renew_agent_action_reconciliation(UUID,UUID,BIGINT,INTEGER),
 resolve_agent_action_provider_reconciliation(UUID,UUID,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION _agent_runtime_226_append_action_event(UUID,TEXT,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_agent_action_provider_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB),
 record_agent_action_unknown(UUID,UUID,TEXT,JSONB), claim_agent_action_reconciliation(UUID,BIGINT,TEXT,INTEGER),
 renew_agent_action_reconciliation(UUID,UUID,BIGINT,INTEGER),
 resolve_agent_action_provider_reconciliation(UUID,UUID,TEXT,TEXT,JSONB,JSONB)
 TO everydayai_agent_runtime_worker;

RESET ROLE;
