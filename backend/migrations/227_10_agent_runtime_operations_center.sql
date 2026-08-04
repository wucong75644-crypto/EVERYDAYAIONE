-- 227.10: Accepted/Unknown operations-center readback and intent contracts.
-- Provider facts remain Runtime-owned; admin writes only audited operation intents.
SET LOCAL ROLE everydayai_owner;

ALTER TABLE agent_runtime_provider_submission_facts
    ADD COLUMN last_readback_at TIMESTAMPTZ,
    ADD COLUMN last_reconcile_at TIMESTAMPTZ;

CREATE FUNCTION _agent_runtime_provider_fact_observation_stamp()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.readback_hash IS DISTINCT FROM OLD.readback_hash
       OR NEW.state IN ('readback_confirmed','cancelled','failed')
          AND OLD.state IS DISTINCT FROM NEW.state THEN
        NEW.last_readback_at := clock_timestamp();
    END IF;
    IF NEW.state IN ('accepted','unknown','reconcile_required','cancel_requested')
       AND OLD.state IS DISTINCT FROM NEW.state THEN
        NEW.last_reconcile_at := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER agent_runtime_provider_fact_observation_stamp
BEFORE UPDATE ON agent_runtime_provider_submission_facts
FOR EACH ROW EXECUTE FUNCTION _agent_runtime_provider_fact_observation_stamp();

CREATE TABLE agent_runtime_provider_operation_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE CHECK (length(btrim(idempotency_key)) BETWEEN 1 AND 300),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    submission_id UUID NOT NULL REFERENCES agent_runtime_provider_submission_facts(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL CHECK (operation IN ('readback','reconcile','cancel')),
    expected_state_version BIGINT NOT NULL CHECK (expected_state_version >= 0),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','claimed','completed','rejected')),
    outcome_code TEXT,
    claimed_by TEXT,
    claim_expires_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_agent_runtime_provider_operation_intents_queue
    ON agent_runtime_provider_operation_intents(status, claim_expires_at, created_at)
    WHERE status IN ('requested','claimed');
CREATE INDEX idx_agent_runtime_provider_operation_intents_tenant
    ON agent_runtime_provider_operation_intents(org_id, submission_id, created_at);

CREATE FUNCTION _agent_runtime_provider_operation_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.org_id IS DISTINCT FROM OLD.org_id
       OR NEW.submission_id IS DISTINCT FROM OLD.submission_id
       OR NEW.operation IS DISTINCT FROM OLD.operation
       OR NEW.expected_state_version IS DISTINCT FROM OLD.expected_state_version
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'RUNTIME_PROVIDER_OPERATION_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER agent_runtime_provider_operation_immutable
BEFORE UPDATE ON agent_runtime_provider_operation_intents
FOR EACH ROW EXECUTE FUNCTION _agent_runtime_provider_operation_immutable();

ALTER TABLE agent_runtime_provider_operation_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_provider_operation_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_provider_operation_intents_owner_all
    ON agent_runtime_provider_operation_intents FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON TABLE agent_runtime_provider_operation_intents
    FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
         everydayai_sync, everydayai, everydayai_agent_runtime_worker,
         everydayai_projection_worker, everydayai_authorization_worker,
         everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_admin_org_check(p_org_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF session_user <> 'everydayai_runtime_admin'
       OR current_setting('app.access_kind', true) <> 'runtime_admin'
       OR tenant_actor_user_id() IS NULL
       OR tenant_org_id() IS DISTINCT FROM p_org_id
       OR NOT tenant_platform_admin() THEN
        RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE FUNCTION list_agent_runtime_provider_operations(
    p_org_id UUID, p_provider TEXT DEFAULT NULL, p_capability TEXT DEFAULT NULL,
    p_state TEXT DEFAULT NULL, p_created_after TIMESTAMPTZ DEFAULT NULL,
    p_created_before TIMESTAMPTZ DEFAULT NULL, p_limit INTEGER DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_limit INTEGER := LEAST(GREATEST(COALESCE(p_limit,100),1),200); v_rows JSONB;
BEGIN
    PERFORM _agent_runtime_admin_org_check(p_org_id);
    IF p_state IS NOT NULL AND p_state NOT IN ('accepted','unknown','reconcile_required') THEN
        RAISE EXCEPTION 'RUNTIME_OPERATIONS_STATE_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(row_data ORDER BY row_data->>'created_at' DESC),'[]'::JSONB)
      INTO v_rows
      FROM (
        SELECT jsonb_build_object(
            'submission_id', f.id, 'tenant_id', f.org_id, 'provider', f.provider,
            'capability', NULLIF(btrim(COALESCE(a.policy_snapshot->>'capability',
                a.policy_snapshot->>'capability_name',
                a.policy_snapshot->'capability_requirements'->>0)),''),
            'state', f.state, 'created_at', f.created_at,
            'age_seconds', EXTRACT(EPOCH FROM (clock_timestamp()-f.created_at)),
            'run_id', f.run_id, 'action_id', f.action_id, 'attempt_id', f.attempt_id,
            'provider_reference', f.provider_task_ref,
            'provider_revision', f.provider_revision, 'request_hash', f.request_hash,
            'last_readback_at', f.last_readback_at,
            'last_reconcile_at', f.last_reconcile_at,
            'next_reconcile_at', f.next_reconcile_at, 'state_version', f.state_version,
            'cancel_requested', f.state='cancel_requested' OR f.cancel_requested_at IS NOT NULL,
            'reason_code', NULLIF(f.ambiguity_evidence->>'error_code',''),
            'fence', jsonb_build_object(
                'tenant_kill_epoch', COALESCE(ofn.tenant_kill_epoch,0),
                'provider_kill_epoch', COALESCE(ofn.provider_kill_epoch,0),
                'capability_kill_epoch', COALESCE(ofn.capability_kill_epoch,0),
                'provider_revision', ofn.provider_revision,
                'status', ofn.status, 'lease_expires_at', ofn.lease_expires_at
            )
        ) AS row_data
        FROM agent_runtime_provider_submission_facts AS f
        JOIN agent_actions AS a ON a.id=f.action_id AND a.org_id=p_org_id
        LEFT JOIN agent_runtime_owner_fences AS ofn
          ON ofn.owner_kind='attempt' AND ofn.owner_id=f.attempt_id
         AND ofn.execution_token=f.execution_token
        WHERE f.org_id=p_org_id
          AND (p_provider IS NULL OR f.provider=p_provider)
          AND (p_capability IS NULL OR COALESCE(a.policy_snapshot->>'capability',
              a.policy_snapshot->>'capability_name',
              a.policy_snapshot->'capability_requirements'->>0)=p_capability)
          AND (p_state IS NULL OR f.state=p_state)
          AND (p_created_after IS NULL OR f.created_at>=p_created_after)
          AND (p_created_before IS NULL OR f.created_at<p_created_before)
          AND f.state IN ('accepted','unknown','reconcile_required')
        ORDER BY f.created_at DESC LIMIT v_limit
      ) AS rows;
    RETURN jsonb_build_object('outcome','readback','org_id',p_org_id,'items',v_rows,
        'count',jsonb_array_length(v_rows));
END;
$$;

CREATE FUNCTION request_agent_runtime_provider_operation(
    p_request_id UUID, p_org_id UUID, p_submission_id UUID, p_operation TEXT,
    p_expected_state_version BIGINT, p_reason TEXT, p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fact agent_runtime_provider_submission_facts%ROWTYPE; v_intent agent_runtime_provider_operation_intents%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_admin_org_check(p_org_id);
    IF p_operation NOT IN ('readback','reconcile','cancel') OR NULLIF(btrim(p_reason),'') IS NULL
       OR NULLIF(btrim(p_idempotency_key),'') IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_OPERATION_REQUEST_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fact FROM agent_runtime_provider_submission_facts
     WHERE id=p_submission_id FOR SHARE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_fact.org_id IS DISTINCT FROM p_org_id THEN
        RETURN jsonb_build_object('outcome','tenant_mismatch');
    END IF;
    IF v_fact.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','stale_version','state_version',v_fact.state_version);
    END IF;
    IF (p_operation='readback' AND v_fact.state NOT IN ('accepted','unknown','reconcile_required'))
       OR (p_operation='reconcile' AND v_fact.state NOT IN ('accepted','unknown','reconcile_required','cancel_requested'))
       OR (p_operation='cancel' AND v_fact.state NOT IN ('submitted','accepted','unknown','reconcile_required','cancel_requested')) THEN
        RETURN jsonb_build_object('outcome','reconcile_required','state',v_fact.state);
    END IF;
    SELECT * INTO v_intent FROM agent_runtime_provider_operation_intents
     WHERE request_id=p_request_id OR idempotency_key=btrim(p_idempotency_key)
     FOR UPDATE;
    IF FOUND THEN
        IF v_intent.request_id=p_request_id AND v_intent.org_id=p_org_id
           AND v_intent.submission_id=p_submission_id AND v_intent.operation=p_operation
           AND v_intent.expected_state_version=p_expected_state_version THEN
            RETURN jsonb_build_object('outcome','already_applied','intent_id',v_intent.id,'status',v_intent.status);
        END IF;
        RETURN jsonb_build_object('outcome','idempotency_conflict');
    END IF;
    INSERT INTO agent_runtime_provider_operation_intents(
        request_id,idempotency_key,org_id,submission_id,operation,
        expected_state_version,reason,actor_user_id
    ) VALUES (p_request_id,btrim(p_idempotency_key),p_org_id,p_submission_id,p_operation,
        p_expected_state_version,left(btrim(p_reason),500),tenant_actor_user_id())
    RETURNING * INTO v_intent;
    RETURN jsonb_build_object('outcome','applied','intent_id',v_intent.id,
        'status',v_intent.status,'operation',v_intent.operation);
END;
$$;

CREATE FUNCTION claim_agent_runtime_provider_operation(
    p_intent_id UUID, p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_intent agent_runtime_provider_operation_intents%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NULLIF(btrim(p_worker_id),'') IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 900 THEN
        RAISE EXCEPTION 'RUNTIME_OPERATION_CLAIM_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_intent FROM agent_runtime_provider_operation_intents
     WHERE id=p_intent_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_intent.status='claimed' AND v_intent.claim_expires_at>clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','already_applied','intent_id',v_intent.id,'status',v_intent.status);
    END IF;
    IF v_intent.status NOT IN ('requested','claimed') THEN
        RETURN jsonb_build_object('outcome','stale_version','status',v_intent.status);
    END IF;
    UPDATE agent_runtime_provider_operation_intents SET status='claimed',claimed_by=btrim(p_worker_id),
        claim_expires_at=clock_timestamp()+(p_lease_seconds||' seconds')::INTERVAL,updated_at=clock_timestamp()
     WHERE id=p_intent_id RETURNING * INTO v_intent;
    RETURN jsonb_build_object('outcome','applied','intent_id',v_intent.id,'status',v_intent.status,
        'submission_id',v_intent.submission_id,'operation',v_intent.operation,
        'expected_state_version',v_intent.expected_state_version);
END;
$$;

REVOKE ALL ON FUNCTION list_agent_runtime_provider_operations(UUID,TEXT,TEXT,TEXT,TIMESTAMPTZ,TIMESTAMPTZ,INTEGER),
    request_agent_runtime_provider_operation(UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT),
    claim_agent_runtime_provider_operation(UUID,TEXT,INTEGER) FROM PUBLIC,
    everydayai_runtime,everydayai_worker,everydayai_wecom_runtime,
    everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION list_agent_runtime_provider_operations(UUID,TEXT,TEXT,TEXT,TIMESTAMPTZ,TIMESTAMPTZ,INTEGER),
    request_agent_runtime_provider_operation(UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT)
    TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_provider_operation(UUID,TEXT,INTEGER)
    TO everydayai_agent_runtime_worker;

RESET ROLE;
