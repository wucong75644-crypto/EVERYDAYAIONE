-- 226_08: strict idempotent readback for facts whose legacy RPCs returned bare outcomes.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION record_agent_action_cost_strict(
    p_action_id UUID, p_attempt_id UUID, p_kind TEXT,
    p_reserved_amount BIGINT, p_actual_amount BIGINT, p_currency TEXT,
    p_reason_code TEXT, p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE a agent_actions%ROWTYPE; t agent_action_attempts%ROWTYPE; s agent_action_cost_settlements%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_actions WHERE id=p_action_id;
    SELECT * INTO t FROM agent_action_attempts WHERE id=p_attempt_id;
    IF a.id IS NULL OR t.id IS NULL OR t.action_id IS DISTINCT FROM a.id THEN
        RAISE EXCEPTION 'AGENT_ACTION_COST_BINDING_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO s FROM agent_action_cost_settlements
     WHERE action_id=p_action_id AND attempt_id=p_attempt_id AND kind=p_kind FOR UPDATE;
    IF FOUND THEN
        IF s.reserved_amount IS DISTINCT FROM p_reserved_amount
           OR s.actual_amount IS DISTINCT FROM p_actual_amount
           OR s.currency IS DISTINCT FROM p_currency
           OR s.reason_code IS DISTINCT FROM p_reason_code
           OR s.provider_receipt_hash IS DISTINCT FROM p_provider_receipt_hash THEN
            RAISE EXCEPTION 'AGENT_COST_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
        END IF;
        RETURN jsonb_build_object('outcome','idempotent_readback','settlement_id',s.id,
            'action_id',s.action_id,'attempt_id',s.attempt_id,'kind',s.kind,
            'reserved_amount',s.reserved_amount,'actual_amount',s.actual_amount,
            'currency',s.currency,'reason_code',s.reason_code,
            'provider_receipt_hash',s.provider_receipt_hash);
    END IF;
    INSERT INTO agent_action_cost_settlements(
        action_id,attempt_id,run_id,org_id,user_id,kind,reserved_amount,actual_amount,
        currency,reason_code,provider_receipt_hash,status
    ) VALUES (a.id,t.id,a.run_id,a.org_id,a.user_id,p_kind,p_reserved_amount,
        p_actual_amount,p_currency,p_reason_code,p_provider_receipt_hash,'applied')
    ON CONFLICT (action_id,attempt_id,kind) DO NOTHING
    RETURNING * INTO s;
    IF NOT FOUND THEN
        SELECT * INTO s FROM agent_action_cost_settlements
         WHERE action_id=p_action_id AND attempt_id=p_attempt_id AND kind=p_kind FOR UPDATE;
        IF s.reserved_amount IS DISTINCT FROM p_reserved_amount
           OR s.actual_amount IS DISTINCT FROM p_actual_amount
           OR s.currency IS DISTINCT FROM p_currency
           OR s.reason_code IS DISTINCT FROM p_reason_code
           OR s.provider_receipt_hash IS DISTINCT FROM p_provider_receipt_hash THEN
            RAISE EXCEPTION 'AGENT_COST_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
        END IF;
        RETURN jsonb_build_object('outcome','idempotent_readback','settlement_id',s.id,
            'action_id',s.action_id,'attempt_id',s.attempt_id,'kind',s.kind,
            'reserved_amount',s.reserved_amount,'actual_amount',s.actual_amount,
            'currency',s.currency,'reason_code',s.reason_code,
            'provider_receipt_hash',s.provider_receipt_hash);
    END IF;
    PERFORM _agent_runtime_226_append_action_event(a.id,'action.cost.'||p_kind,
        jsonb_build_object('settlement_id',s.id,'amount',p_actual_amount));
    RETURN jsonb_build_object('outcome','applied','settlement_id',s.id,
        'action_id',s.action_id,'attempt_id',s.attempt_id,'kind',s.kind,
        'reserved_amount',s.reserved_amount,'actual_amount',s.actual_amount,
        'currency',s.currency,'reason_code',s.reason_code,
        'provider_receipt_hash',s.provider_receipt_hash);
END; $$;

CREATE FUNCTION record_agent_action_callback_strict(
    p_provider TEXT, p_provider_event_id TEXT, p_callback_correlation TEXT,
    p_payload_hash TEXT, p_payload_redacted JSONB, p_action_id UUID, p_attempt_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE i agent_action_callback_inbox%ROWTYPE; existing agent_action_callback_inbox%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_action_id IS NULL OR p_attempt_id IS NULL
       OR p_payload_hash !~ '^[0-9a-f]{64}$'
       OR p_payload_redacted ?| ARRAY['token','secret','password','cookie','authorization'] THEN
        RAISE EXCEPTION 'AGENT_CALLBACK_REJECTED' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_action_attempts a WHERE a.id=p_attempt_id
        AND a.action_id=p_action_id AND a.callback_correlation=p_callback_correlation
        AND a.status IN ('dispatching','accepted','unknown')) THEN
        RAISE EXCEPTION 'AGENT_CALLBACK_BINDING_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO existing FROM agent_action_callback_inbox
     WHERE provider=p_provider AND provider_event_id=p_provider_event_id
       AND payload_hash=p_payload_hash FOR UPDATE;
    IF FOUND THEN
        IF existing.callback_correlation IS DISTINCT FROM p_callback_correlation
           OR existing.action_id IS DISTINCT FROM p_action_id
           OR existing.attempt_id IS DISTINCT FROM p_attempt_id
           OR existing.payload_redacted IS DISTINCT FROM p_payload_redacted THEN
            RAISE EXCEPTION 'AGENT_CALLBACK_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
        END IF;
        RETURN jsonb_build_object('outcome','idempotent_readback','inbox_id',existing.id,
            'action_id',existing.action_id,'attempt_id',existing.attempt_id,
            'payload_hash',existing.payload_hash);
    END IF;
    INSERT INTO agent_action_callback_inbox(provider,provider_event_id,callback_correlation,
        payload_hash,payload_redacted,signature_valid,action_id,attempt_id)
    VALUES (btrim(p_provider),btrim(p_provider_event_id),btrim(p_callback_correlation),
        p_payload_hash,p_payload_redacted,TRUE,p_action_id,p_attempt_id)
    ON CONFLICT (provider,provider_event_id,payload_hash) DO NOTHING
    RETURNING * INTO i;
    IF NOT FOUND THEN
        SELECT * INTO existing FROM agent_action_callback_inbox
         WHERE provider=p_provider AND provider_event_id=p_provider_event_id
           AND payload_hash=p_payload_hash FOR UPDATE;
        IF existing.callback_correlation IS DISTINCT FROM p_callback_correlation
           OR existing.action_id IS DISTINCT FROM p_action_id
           OR existing.attempt_id IS DISTINCT FROM p_attempt_id
           OR existing.payload_redacted IS DISTINCT FROM p_payload_redacted THEN
            RAISE EXCEPTION 'AGENT_CALLBACK_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
        END IF;
        RETURN jsonb_build_object('outcome','idempotent_readback','inbox_id',existing.id,
            'action_id',existing.action_id,'attempt_id',existing.attempt_id,
            'payload_hash',existing.payload_hash);
    END IF;
    RETURN jsonb_build_object('outcome','accepted','inbox_id',i.id,
        'action_id',i.action_id,'attempt_id',i.attempt_id,'payload_hash',i.payload_hash);
END; $$;

REVOKE ALL ON FUNCTION record_agent_action_cost_strict(UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT),record_agent_action_callback_strict(TEXT,TEXT,TEXT,TEXT,JSONB,UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_agent_action_cost_strict(UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT),record_agent_action_callback_strict(TEXT,TEXT,TEXT,TEXT,JSONB,UUID,UUID) TO everydayai_agent_runtime_worker;
RESET ROLE;
