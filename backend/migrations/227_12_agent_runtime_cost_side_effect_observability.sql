-- 227.12: Read-only cost and external side-effect ledger observability.
-- Existing Runtime facts remain the only correctness source; this migration
-- creates no second ledger and exposes no mutation path.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_agent_runtime_cost_side_effect_snapshot(
    p_org_id UUID, p_provider TEXT DEFAULT NULL, p_domain TEXT DEFAULT NULL,
    p_state TEXT DEFAULT NULL, p_limit INTEGER DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_limit INTEGER := LEAST(GREATEST(COALESCE(p_limit,100),1),200);
    v_cost JSONB;
    v_effects JSONB;
    v_anomalies JSONB;
BEGIN
    PERFORM _agent_runtime_admin_org_check(p_org_id);
    IF p_domain IS NOT NULL AND p_domain NOT IN
       ('ERP','Media','Artifact','Workspace','Scheduler','Sandbox','Provider') THEN
        RAISE EXCEPTION 'RUNTIME_LEDGER_DOMAIN_INVALID' USING ERRCODE='22023';
    END IF;
    IF p_state IS NOT NULL AND p_state NOT IN (
       'RESERVED','SETTLEMENT_PENDING','SETTLED','RELEASED','REFUND_PENDING',
       'REFUNDED','MISMATCH','UNKNOWN','FAILED_CLOSED') THEN
        RAISE EXCEPTION 'RUNTIME_LEDGER_STATE_INVALID' USING ERRCODE='22023';
    END IF;

    WITH grouped AS (
        SELECT c.action_id, c.attempt_id, c.run_id, c.org_id,
               SUM(c.reserved_amount)::BIGINT AS reserved_amount,
               SUM(c.actual_amount) FILTER (WHERE c.kind='settle')::BIGINT AS settled_amount,
               SUM(c.actual_amount) FILTER (WHERE c.kind='release')::BIGINT AS released_amount,
               SUM(c.actual_amount) FILTER (WHERE c.kind='refund')::BIGINT AS refunded_amount,
               MAX(c.created_at) AS updated_at,
               MAX(c.provider_receipt_hash) FILTER (WHERE c.kind='settle') AS receipt_hash,
               COUNT(*) FILTER (WHERE c.kind='reserve') AS reserve_count,
               COUNT(*) FILTER (WHERE c.kind='settle') AS settle_count,
               COUNT(*) FILTER (WHERE c.kind='release') AS release_count,
               COUNT(*) FILTER (WHERE c.kind='refund') AS refund_count
        FROM agent_action_cost_settlements c
        WHERE c.org_id=p_org_id
        GROUP BY c.action_id,c.attempt_id,c.run_id,c.org_id
    ), rows AS (
        SELECT jsonb_build_object(
            'org_id', g.org_id, 'run_id', g.run_id, 'action_id', g.action_id,
            'attempt_id', g.attempt_id, 'currency', 'credits_minor',
            'reserved_amount', COALESCE(g.reserved_amount,0),
            'settled_amount', COALESCE(g.settled_amount,0),
            'released_amount', COALESCE(g.released_amount,0),
            'refunded_amount', COALESCE(g.refunded_amount,0),
            'expected_amount', COALESCE(g.reserved_amount,0),
            'mismatch_amount', GREATEST(
                COALESCE(g.settled_amount,0)+COALESCE(g.refunded_amount,0)
                -COALESCE(g.reserved_amount,0), 0),
            'cost_state', CASE
                WHEN COALESCE(g.settled_amount,0)+COALESCE(g.refunded_amount,0)
                     > COALESCE(g.reserved_amount,0) THEN 'MISMATCH'
                WHEN g.refund_count > 0 THEN 'REFUNDED'
                WHEN g.release_count > 0 THEN 'RELEASED'
                WHEN g.settle_count > 0 THEN 'SETTLED'
                WHEN g.reserve_count > 0 THEN 'SETTLEMENT_PENDING'
                ELSE 'FAILED_CLOSED' END,
            'state_version', 0, 'request_hash', a.request_hash,
            'provider_revision', f.provider_revision,
            'provider_receipt_hash', COALESCE(g.receipt_hash,f.provider_receipt_hash),
            'created_at', a.created_at, 'updated_at', g.updated_at,
            'settlement_age_seconds', EXTRACT(EPOCH FROM (clock_timestamp()-g.updated_at)),
            'provider_state', f.state,
            'reconcile_only', f.state IN ('accepted','unknown','reconcile_required','cancel_requested'),
            'side_effect_correlation_id', f.id
        ) AS item
        FROM grouped g
        JOIN agent_actions a ON a.id=g.action_id
        LEFT JOIN agent_runtime_provider_submission_facts f ON f.attempt_id=g.attempt_id
        WHERE (p_provider IS NULL OR f.provider=p_provider)
          AND (p_state IS NULL OR (CASE
                WHEN COALESCE(g.settled_amount,0)+COALESCE(g.refunded_amount,0)
                     > COALESCE(g.reserved_amount,0) THEN 'MISMATCH'
                WHEN g.refund_count > 0 THEN 'REFUNDED'
                WHEN g.release_count > 0 THEN 'RELEASED'
                WHEN g.settle_count > 0 THEN 'SETTLED'
                WHEN g.reserve_count > 0 THEN 'SETTLEMENT_PENDING'
                ELSE 'FAILED_CLOSED' END)=p_state)
        ORDER BY g.updated_at DESC LIMIT v_limit
    )
    SELECT COALESCE(jsonb_agg(item),'[]'::JSONB) INTO v_cost FROM rows;

    SELECT COALESCE(jsonb_agg(item ORDER BY created_at DESC),'[]'::JSONB)
      INTO v_effects
      FROM (
        SELECT jsonb_build_object(
            'side_effect_id', f.id, 'org_id', f.org_id, 'run_id', f.run_id,
            'action_id', f.action_id, 'attempt_id', f.attempt_id,
            'domain', CASE
                WHEN lower(a.tool_name) LIKE '%erp%' THEN 'ERP'
                WHEN lower(a.tool_name) LIKE '%media%' THEN 'Media'
                WHEN lower(a.tool_name) LIKE '%artifact%' THEN 'Artifact'
                WHEN lower(a.tool_name) LIKE '%workspace%' THEN 'Workspace'
                WHEN lower(a.tool_name) LIKE '%schedul%' THEN 'Scheduler'
                WHEN lower(a.tool_name) LIKE '%code_execute%' THEN 'Sandbox'
                ELSE 'Provider' END,
            'provider', f.provider, 'operation', a.tool_name,
            'external_reference', f.provider_task_ref,
            'idempotency_key_hash', encode(digest(f.external_idempotency_key,'sha256'),'hex'),
            'request_hash', f.request_hash, 'provider_receipt_hash', f.provider_receipt_hash,
            'status', CASE WHEN f.state='submission_pending' THEN 'submitted'
                WHEN f.state IN ('accepted','unknown','reconcile_required','cancel_requested')
                    THEN f.state ELSE f.state END,
            'first_seen_at', f.created_at, 'last_readback_at', f.last_readback_at,
            'reconcile_count', rec.reconcile_count,
            'duplicate_attempt_count', GREATEST(dup.attempt_count-1,0),
            'kill_epoch', COALESCE(ofn.tenant_kill_epoch,0),
            'provider_revision', f.provider_revision,
            'cost_linked', EXISTS (
                SELECT 1 FROM agent_action_cost_settlements c
                WHERE c.action_id=f.action_id AND c.attempt_id=f.attempt_id),
            'reconcile_only', f.state IN ('accepted','unknown','reconcile_required','cancel_requested'),
            'reason_code', NULLIF(f.ambiguity_evidence->>'error_code','')
        ) AS item, f.created_at
        FROM agent_runtime_provider_submission_facts f
        JOIN agent_actions a ON a.id=f.action_id AND a.org_id=p_org_id
        LEFT JOIN LATERAL (
            SELECT tenant_kill_epoch FROM agent_runtime_owner_fences
            WHERE owner_kind='attempt' AND owner_id=f.attempt_id
            ORDER BY updated_at DESC LIMIT 1
        ) ofn ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*)::INTEGER AS reconcile_count
            FROM agent_runtime_events e
            WHERE e.action_id=f.action_id AND e.event_type='action.provider.reconciled'
        ) rec ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*)::INTEGER AS attempt_count
            FROM agent_runtime_provider_submission_facts d
            WHERE d.org_id=f.org_id AND d.provider=f.provider
              AND d.external_idempotency_key=f.external_idempotency_key
        ) dup ON TRUE
        WHERE f.org_id=p_org_id
          AND (p_provider IS NULL OR f.provider=p_provider)
          AND (p_domain IS NULL OR p_domain=CASE
                WHEN lower(a.tool_name) LIKE '%erp%' THEN 'ERP'
                WHEN lower(a.tool_name) LIKE '%media%' THEN 'Media'
                WHEN lower(a.tool_name) LIKE '%artifact%' THEN 'Artifact'
                WHEN lower(a.tool_name) LIKE '%workspace%' THEN 'Workspace'
                WHEN lower(a.tool_name) LIKE '%schedul%' THEN 'Scheduler'
                WHEN lower(a.tool_name) LIKE '%code_execute%' THEN 'Sandbox'
                ELSE 'Provider' END)
        ORDER BY f.created_at DESC LIMIT v_limit
      ) rows;

    SELECT jsonb_build_object(
        'cost_mismatch_count', COUNT(*) FILTER (WHERE
            COALESCE(c.settled_amount,0)+COALESCE(c.refunded_amount,0)
            > COALESCE(c.reserved_amount,0)),
        'settlement_pending_count', COUNT(*) FILTER (WHERE c.reserve_count>0 AND c.settle_count=0),
        'terminal_without_cost_count', (
            SELECT COUNT(*) FROM agent_actions ta
            WHERE ta.org_id=p_org_id AND ta.status IN ('completed','failed','cancelled')
              AND NOT EXISTS (SELECT 1 FROM agent_action_cost_settlements tc
                              WHERE tc.action_id=ta.id)
        ),
        'provider_without_readback_count', (
            SELECT COUNT(*) FROM agent_runtime_provider_submission_facts pf
            WHERE pf.org_id=p_org_id
              AND pf.state IN ('accepted','unknown','reconcile_required','cancel_requested')
              AND pf.last_readback_at IS NULL
        ),
        'refund_overflow_count', COUNT(*) FILTER (WHERE
            COALESCE(c.refunded_amount,0)>COALESCE(c.settled_amount,0)),
        'reconcile_only_count', (
            SELECT COUNT(*) FROM agent_runtime_provider_submission_facts rf
            WHERE rf.org_id=p_org_id
              AND rf.state IN ('accepted','unknown','reconcile_required','cancel_requested')
        )
    ) INTO v_anomalies FROM (
        SELECT g.reserved_amount, g.settled_amount, g.refunded_amount,
               g.reserve_count, g.settle_count
        FROM agent_action_cost_settlements c
        JOIN LATERAL (
            SELECT SUM(reserved_amount)::BIGINT reserved_amount,
                   SUM(actual_amount) FILTER (WHERE kind='settle')::BIGINT settled_amount,
                   SUM(actual_amount) FILTER (WHERE kind='refund')::BIGINT refunded_amount,
                   COUNT(*) FILTER (WHERE kind='reserve') reserve_count,
                   COUNT(*) FILTER (WHERE kind='settle') settle_count
            FROM agent_action_cost_settlements x
            WHERE x.action_id=c.action_id AND x.attempt_id=c.attempt_id
        ) g ON TRUE
        WHERE c.org_id=p_org_id
        GROUP BY g.reserved_amount,g.settled_amount,g.refunded_amount,g.reserve_count,g.settle_count
    ) c;

    RETURN jsonb_build_object(
        'outcome','readback','org_id',p_org_id,'currency_contract','credits_minor_integer',
        'cost_ledger',v_cost,'side_effect_ledger',v_effects,'anomalies',v_anomalies,
        'production_ready',FALSE,'production_enabled',FALSE
    );
END;
$$;

REVOKE ALL ON FUNCTION get_agent_runtime_cost_side_effect_snapshot(UUID,TEXT,TEXT,TEXT,INTEGER)
    FROM PUBLIC, everydayai_runtime, everydayai_worker, everydayai_wecom_runtime,
         everydayai_agent_runtime_worker, everydayai_projection_worker,
         everydayai_authorization_worker, everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION get_agent_runtime_cost_side_effect_snapshot(UUID,TEXT,TEXT,TEXT,INTEGER)
    TO everydayai_runtime_admin;
RESET ROLE;
