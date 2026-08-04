-- 227.11: Runtime-owned recovery snapshot and audited recovery intents.
-- This lane never mutates domain facts. Domain owners consume the intent through
-- their existing fenced RPCs.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_recovery_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE CHECK (length(btrim(idempotency_key)) BETWEEN 1 AND 300),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    recovery_domain TEXT NOT NULL CHECK (recovery_domain IN (
        'artifact','workspace','scheduler','child_run','sandbox'
    )),
    target_id TEXT NOT NULL CHECK (length(btrim(target_id)) BETWEEN 1 AND 300),
    operation TEXT NOT NULL CHECK (operation IN (
        'readback','reconcile','cleanup','recover','cancel'
    )),
    expected_state_version BIGINT NOT NULL CHECK (expected_state_version >= 0),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN (
        'requested','claimed','completed','rejected'
    )),
    outcome_code TEXT,
    claimed_by TEXT,
    claim_expires_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_agent_runtime_recovery_intents_queue
    ON agent_runtime_recovery_intents(status, claim_expires_at, created_at)
    WHERE status IN ('requested','claimed');
CREATE INDEX idx_agent_runtime_recovery_intents_scope
    ON agent_runtime_recovery_intents(org_id, recovery_domain, target_id, created_at);

CREATE TABLE agent_runtime_recovery_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES agent_runtime_recovery_intents(request_id)
        ON DELETE RESTRICT,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    recovery_domain TEXT NOT NULL,
    target_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    expected_state_version BIGINT NOT NULL,
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    outcome_code TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (request_id, outcome_code)
);

CREATE FUNCTION _agent_runtime_recovery_intent_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.org_id IS DISTINCT FROM OLD.org_id
       OR NEW.recovery_domain IS DISTINCT FROM OLD.recovery_domain
       OR NEW.target_id IS DISTINCT FROM OLD.target_id
       OR NEW.operation IS DISTINCT FROM OLD.operation
       OR NEW.expected_state_version IS DISTINCT FROM OLD.expected_state_version
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'RUNTIME_RECOVERY_INTENT_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER agent_runtime_recovery_intent_immutable
BEFORE UPDATE ON agent_runtime_recovery_intents
FOR EACH ROW EXECUTE FUNCTION _agent_runtime_recovery_intent_immutable();

CREATE FUNCTION _agent_runtime_recovery_audit_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION 'RUNTIME_RECOVERY_AUDIT_IMMUTABLE' USING ERRCODE='55000';
END;
$$;
CREATE TRIGGER agent_runtime_recovery_audit_immutable
BEFORE UPDATE OR DELETE ON agent_runtime_recovery_audit
FOR EACH ROW EXECUTE FUNCTION _agent_runtime_recovery_audit_immutable();

ALTER TABLE agent_runtime_recovery_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_recovery_intents FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_recovery_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_recovery_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_recovery_intents_owner_all
    ON agent_runtime_recovery_intents FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_recovery_audit_owner_all
    ON agent_runtime_recovery_audit FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON TABLE agent_runtime_recovery_intents, agent_runtime_recovery_audit
    FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
         everydayai_sync, everydayai, everydayai_agent_runtime_worker,
         everydayai_projection_worker, everydayai_authorization_worker,
         everydayai_sandbox_worker, everydayai_runtime_admin;

CREATE FUNCTION _agent_runtime_recovery_snapshot_row(
    p_domain TEXT, p_target_id TEXT, p_org_id UUID, p_state TEXT,
    p_state_version BIGINT, p_run_id UUID, p_action_id UUID, p_attempt_id UUID,
    p_created_at TIMESTAMPTZ, p_updated_at TIMESTAMPTZ,
    p_lease_expires_at TIMESTAMPTZ, p_failure_code TEXT,
    p_owner TEXT, p_reconcile_only BOOLEAN, p_can_cleanup BOOLEAN,
    p_can_cancel BOOLEAN, p_can_recover BOOLEAN, p_fence JSONB
) RETURNS JSONB LANGUAGE SQL STABLE
SET search_path = pg_catalog, public AS $$
    SELECT jsonb_build_object(
        'recovery_domain', p_domain, 'target_id', p_target_id, 'tenant_id', p_org_id,
        'run_id', p_run_id, 'action_id', p_action_id, 'attempt_id', p_attempt_id,
        'state', p_state, 'state_version', p_state_version,
        'created_at', p_created_at, 'updated_at', p_updated_at,
        'age_seconds', EXTRACT(EPOCH FROM (clock_timestamp()-p_created_at)),
        'lease_expires_at', p_lease_expires_at,
        'failure_reason_code', NULLIF(p_failure_code,''), 'owner', p_owner,
        'reconcile_only', p_reconcile_only, 'can_cleanup', p_can_cleanup,
        'can_cancel', p_can_cancel, 'can_recover', p_can_recover,
        'fence', COALESCE(p_fence, '{}'::JSONB)
    );
$$;

CREATE FUNCTION list_agent_runtime_recovery_snapshot(
    p_org_id UUID, p_domain TEXT DEFAULT NULL, p_state TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_limit INTEGER := LEAST(GREATEST(COALESCE(p_limit,100),1),200);
    v_rows JSONB := '[]'::JSONB;
    v_part JSONB;
BEGIN
    PERFORM _agent_runtime_admin_org_check(p_org_id);
    IF p_domain IS NOT NULL AND p_domain NOT IN
       ('artifact','workspace','scheduler','child_run','sandbox') THEN
        RAISE EXCEPTION 'RUNTIME_RECOVERY_DOMAIN_INVALID' USING ERRCODE='22023';
    END IF;
    IF p_state IS NOT NULL AND length(btrim(p_state)) = 0 THEN
        RAISE EXCEPTION 'RUNTIME_RECOVERY_STATE_INVALID' USING ERRCODE='22023';
    END IF;

    IF p_domain IS NULL OR p_domain='artifact' THEN
        SELECT COALESCE(jsonb_agg(x ORDER BY x->>'created_at' DESC),'[]'::JSONB)
          INTO v_part
          FROM (
            SELECT _agent_runtime_recovery_snapshot_row(
                'artifact', l.artifact_id::TEXT, p_org_id, l.materialize_status,
                aat.state_version, aat.run_id, l.action_id, l.attempt_id,
                l.created_at, l.created_at, NULL,
                CASE WHEN l.materialize_status='materialize_failed' THEN 'ARTIFACT_READBACK_FAILED' END,
                'artifact_owner', l.materialize_status IN ('pending','partial','materialize_failed'),
                FALSE, FALSE, l.materialize_status IN ('pending','partial','materialize_failed'),
                COALESCE(fence.fence, '{}'::JSONB)) AS x
            FROM agent_action_artifact_links l
            JOIN agent_action_attempts aat ON aat.id=l.attempt_id
            JOIN agent_actions aa ON aa.id=l.action_id AND aa.org_id=p_org_id
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'tenant_kill_epoch', ofn.tenant_kill_epoch,
                    'provider_kill_epoch', ofn.provider_kill_epoch,
                    'capability_kill_epoch', ofn.capability_kill_epoch,
                    'provider_revision', ofn.provider_revision,
                    'capability_revision', ofn.capability_revision,
                    'status', ofn.status, 'lease_expires_at', ofn.lease_expires_at
                ) AS fence
                FROM agent_runtime_owner_fences ofn
                WHERE ofn.owner_kind='attempt' AND ofn.owner_id=aat.id
                ORDER BY ofn.updated_at DESC LIMIT 1
            ) fence ON TRUE
            WHERE aa.org_id=p_org_id
              AND (p_state IS NULL OR l.materialize_status=p_state)
            ORDER BY l.created_at DESC LIMIT v_limit
          ) q;
        v_rows := v_rows || v_part;
    END IF;

    IF p_domain IS NULL OR p_domain='workspace' THEN
        SELECT COALESCE(jsonb_agg(x ORDER BY x->>'created_at' DESC),'[]'::JSONB)
          INTO v_part
          FROM (
            SELECT _agent_runtime_recovery_snapshot_row(
                'workspace', d.id::TEXT, p_org_id,
                CASE WHEN d.purged THEN 'purged' ELSE 'cleanup_pending' END,
                d.runtime_state_version, d.runtime_action_id, d.runtime_action_id,
                d.runtime_attempt_id, da.created_at, da.updated_at, NULL,
                CASE WHEN NOT d.purged THEN 'WORKSPACE_CLEANUP_PENDING' END,
                'workspace_owner', FALSE, NOT d.purged, FALSE, NOT d.purged,
                COALESCE(fence.fence, '{}'::JSONB)) AS x
            FROM deleted_files d
            JOIN agent_actions da ON da.id=d.runtime_action_id AND da.org_id=p_org_id
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'tenant_kill_epoch', ofn.tenant_kill_epoch,
                    'provider_kill_epoch', ofn.provider_kill_epoch,
                    'capability_kill_epoch', ofn.capability_kill_epoch,
                    'provider_revision', ofn.provider_revision,
                    'capability_revision', ofn.capability_revision,
                    'status', ofn.status, 'lease_expires_at', ofn.lease_expires_at
                ) AS fence
                FROM agent_runtime_owner_fences ofn
                WHERE ofn.owner_kind='attempt' AND ofn.owner_id=d.runtime_attempt_id
                ORDER BY ofn.updated_at DESC LIMIT 1
            ) fence ON TRUE
            WHERE da.org_id=p_org_id
              AND (p_state IS NULL OR (CASE WHEN d.purged THEN 'purged' ELSE 'cleanup_pending' END)=p_state)
            ORDER BY da.created_at DESC LIMIT v_limit
          ) q;
        v_rows := v_rows || v_part;
    END IF;

    IF p_domain IS NULL OR p_domain='scheduler' THEN
        SELECT COALESCE(jsonb_agg(x ORDER BY x->>'created_at' DESC),'[]'::JSONB)
          INTO v_part
          FROM (
            SELECT _agent_runtime_recovery_snapshot_row(
                'scheduler', s.task_id, p_org_id, s.state, s.state_version,
                s.run_id, s.action_id, s.attempt_id, s.created_at, s.updated_at,
                s.lease_expires_at,
                CASE WHEN s.lease_expires_at<=clock_timestamp() THEN 'SCHEDULER_STALE_LEASE'
                     WHEN s.state='cancel_requested' THEN 'SCHEDULER_CANCEL_PENDING' END,
                'scheduler_owner', FALSE, FALSE, s.state='cancel_requested',
                s.lease_expires_at<=clock_timestamp(),
                COALESCE(fence.fence, '{}'::JSONB)) AS x
            FROM agent_runtime_scheduler_cas_facts s
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'tenant_kill_epoch', ofn.tenant_kill_epoch,
                    'provider_kill_epoch', ofn.provider_kill_epoch,
                    'capability_kill_epoch', ofn.capability_kill_epoch,
                    'provider_revision', ofn.provider_revision,
                    'capability_revision', ofn.capability_revision,
                    'status', ofn.status, 'lease_expires_at', ofn.lease_expires_at
                ) AS fence
                FROM agent_runtime_owner_fences ofn
                WHERE ofn.owner_kind='attempt' AND ofn.owner_id=s.attempt_id
                ORDER BY ofn.updated_at DESC LIMIT 1
            ) fence ON TRUE
            WHERE s.org_id=p_org_id
              AND (p_state IS NULL OR s.state=p_state)
            ORDER BY s.created_at DESC LIMIT v_limit
          ) q;
        v_rows := v_rows || v_part;
    END IF;

    IF p_domain IS NULL OR p_domain='child_run' THEN
        SELECT COALESCE(jsonb_agg(x ORDER BY x->>'created_at' DESC),'[]'::JSONB)
          INTO v_part
          FROM (
            SELECT _agent_runtime_recovery_snapshot_row(
                'child_run', c.id::TEXT, p_org_id, c.status, c.aggregation_revision,
                c.id, c.parent_action_id, NULL, c.created_at, c.updated_at, NULL,
                CASE WHEN c.status IN ('queued','running') THEN 'CHILD_RUN_RECOVERY_PENDING' END,
                'child_run_owner', c.status NOT IN ('completed','failed','cancelled'), FALSE,
                c.status NOT IN ('completed','failed','cancelled'), c.status IN ('queued','running'),
                '{}'::JSONB) AS x
            FROM agent_runs c
            WHERE c.org_id=p_org_id AND c.parent_run_id IS NOT NULL
              AND (p_state IS NULL OR c.status=p_state)
            ORDER BY c.created_at DESC LIMIT v_limit
          ) q;
        v_rows := v_rows || v_part;
    END IF;

    IF p_domain IS NULL OR p_domain='sandbox' THEN
        SELECT COALESCE(jsonb_agg(x ORDER BY x->>'created_at' DESC),'[]'::JSONB)
          INTO v_part
          FROM (
            SELECT _agent_runtime_recovery_snapshot_row(
                'sandbox', j.id::TEXT, p_org_id, j.cleanup_status, j.state_version,
                j.run_id, j.action_id, j.attempt_id, j.created_at, j.updated_at,
                j.lease_expires_at,
                CASE WHEN j.cleanup_status IN ('failed','unknown') THEN 'SANDBOX_CLEANUP_UNCERTAIN'
                     WHEN j.cleanup_deadline_at<=clock_timestamp() THEN 'SANDBOX_CLEANUP_DEADLINE' END,
                'sandbox_owner', j.status='unknown', j.cleanup_status IN ('pending','failed','unknown'),
                j.status='cancel_requested', j.cleanup_status IN ('pending','failed','unknown'),
                COALESCE(fence.fence, '{}'::JSONB)) AS x
            FROM agent_sandbox_jobs j
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object(
                    'tenant_kill_epoch', ofn.tenant_kill_epoch,
                    'provider_kill_epoch', ofn.provider_kill_epoch,
                    'capability_kill_epoch', ofn.capability_kill_epoch,
                    'provider_revision', ofn.provider_revision,
                    'capability_revision', ofn.capability_revision,
                    'status', ofn.status, 'lease_expires_at', ofn.lease_expires_at
                ) AS fence
                FROM agent_runtime_owner_fences ofn
                WHERE ofn.owner_kind='attempt' AND ofn.owner_id=j.attempt_id
                ORDER BY ofn.updated_at DESC LIMIT 1
            ) fence ON TRUE
            WHERE j.org_id=p_org_id
              AND (p_state IS NULL OR j.cleanup_status=p_state OR j.status=p_state)
            ORDER BY j.created_at DESC LIMIT v_limit
          ) q;
        v_rows := v_rows || v_part;
    END IF;
    RETURN jsonb_build_object('outcome','readback','org_id',p_org_id,
        'items',v_rows,'count',jsonb_array_length(v_rows));
END;
$$;

CREATE FUNCTION request_agent_runtime_recovery(
    p_request_id UUID, p_org_id UUID, p_recovery_domain TEXT, p_target_id TEXT,
    p_operation TEXT, p_expected_state_version BIGINT, p_reason TEXT,
    p_idempotency_key TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_intent agent_runtime_recovery_intents%ROWTYPE;
    v_org UUID;
    v_version BIGINT;
BEGIN
    PERFORM _agent_runtime_admin_org_check(p_org_id);
    IF p_recovery_domain NOT IN ('artifact','workspace','scheduler','child_run','sandbox')
       OR p_operation NOT IN ('readback','reconcile','cleanup','recover','cancel')
       OR NULLIF(btrim(p_target_id),'') IS NULL OR p_expected_state_version IS NULL
       OR p_expected_state_version < 0 OR NULLIF(btrim(p_reason),'') IS NULL
       OR NULLIF(btrim(p_idempotency_key),'') IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_RECOVERY_REQUEST_INVALID' USING ERRCODE='22023';
    END IF;
    IF p_operation='readback' AND p_recovery_domain NOT IN ('artifact','workspace') THEN
        RAISE EXCEPTION 'RUNTIME_RECOVERY_OPERATION_INVALID' USING ERRCODE='22023';
    END IF;
    IF p_operation='cleanup' AND p_recovery_domain NOT IN ('artifact','workspace','sandbox') THEN
        RAISE EXCEPTION 'RUNTIME_RECOVERY_OPERATION_INVALID' USING ERRCODE='22023';
    END IF;

    IF p_recovery_domain='artifact' THEN
        SELECT aa.org_id, aat.state_version INTO v_org, v_version
        FROM agent_action_artifact_links l
        JOIN agent_actions aa ON aa.id=l.action_id
        JOIN agent_action_attempts aat ON aat.id=l.attempt_id
        WHERE l.artifact_id::TEXT=btrim(p_target_id) FOR SHARE;
    ELSIF p_recovery_domain='workspace' THEN
        SELECT da.org_id, d.runtime_state_version INTO v_org, v_version
        FROM deleted_files d
        JOIN agent_actions da ON da.id=d.runtime_action_id
        WHERE d.id::TEXT=btrim(p_target_id) FOR SHARE;
    ELSIF p_recovery_domain='scheduler' THEN
        SELECT s.org_id, s.state_version INTO v_org, v_version
        FROM agent_runtime_scheduler_cas_facts s WHERE s.task_id=btrim(p_target_id) FOR SHARE;
    ELSIF p_recovery_domain='child_run' THEN
        SELECT c.org_id, c.aggregation_revision INTO v_org, v_version
        FROM agent_runs c WHERE c.id::TEXT=btrim(p_target_id) AND c.parent_run_id IS NOT NULL FOR SHARE;
    ELSE
        SELECT j.org_id, j.state_version INTO v_org, v_version
        FROM agent_sandbox_jobs j WHERE j.id::TEXT=btrim(p_target_id) FOR SHARE;
    END IF;
    IF v_org IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_org IS DISTINCT FROM p_org_id THEN RETURN jsonb_build_object('outcome','tenant_mismatch'); END IF;
    IF v_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','stale_version','state_version',v_version);
    END IF;

    SELECT * INTO v_intent FROM agent_runtime_recovery_intents
     WHERE request_id=p_request_id OR idempotency_key=btrim(p_idempotency_key)
     FOR UPDATE;
    IF FOUND THEN
        IF v_intent.request_id=p_request_id AND v_intent.org_id=p_org_id
           AND v_intent.recovery_domain=p_recovery_domain
           AND v_intent.target_id=btrim(p_target_id)
           AND v_intent.operation=p_operation
           AND v_intent.expected_state_version=p_expected_state_version THEN
            RETURN jsonb_build_object('outcome','already_applied','intent_id',v_intent.id,
                'status',v_intent.status);
        END IF;
        RETURN jsonb_build_object('outcome','idempotency_conflict');
    END IF;
    INSERT INTO agent_runtime_recovery_intents(
        request_id,idempotency_key,org_id,recovery_domain,target_id,operation,
        expected_state_version,reason,actor_user_id
    ) VALUES (
        p_request_id,btrim(p_idempotency_key),p_org_id,p_recovery_domain,
        btrim(p_target_id),p_operation,p_expected_state_version,left(btrim(p_reason),500),
        tenant_actor_user_id()) RETURNING * INTO v_intent;
    INSERT INTO agent_runtime_recovery_audit(
        request_id,org_id,recovery_domain,target_id,operation,expected_state_version,
        actor_user_id,outcome_code,reason_code
    ) VALUES (v_intent.request_id,v_intent.org_id,v_intent.recovery_domain,v_intent.target_id,
        v_intent.operation,v_intent.expected_state_version,v_intent.actor_user_id,
        'applied','recovery_intent_requested');
    RETURN jsonb_build_object('outcome','applied','intent_id',v_intent.id,
        'status',v_intent.status,'operation',v_intent.operation);
END;
$$;

CREATE FUNCTION claim_agent_runtime_recovery(
    p_intent_id UUID, p_worker_id TEXT, p_lease_seconds INTEGER DEFAULT 120
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_intent agent_runtime_recovery_intents%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NULLIF(btrim(p_worker_id),'') IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 900 THEN
        RAISE EXCEPTION 'RUNTIME_RECOVERY_CLAIM_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_intent FROM agent_runtime_recovery_intents
    WHERE id=p_intent_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_intent.status='claimed' AND v_intent.claim_expires_at>clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','already_applied','intent_id',v_intent.id,'status',v_intent.status);
    END IF;
    IF v_intent.status NOT IN ('requested','claimed') THEN
        RETURN jsonb_build_object('outcome','stale_version','status',v_intent.status);
    END IF;
    UPDATE agent_runtime_recovery_intents
    SET status='claimed', claimed_by=btrim(p_worker_id),
        claim_expires_at=clock_timestamp()+(p_lease_seconds||' seconds')::INTERVAL,
        updated_at=clock_timestamp()
    WHERE id=p_intent_id RETURNING * INTO v_intent;
    RETURN jsonb_build_object('outcome','applied','intent_id',v_intent.id,
        'status',v_intent.status,'recovery_domain',v_intent.recovery_domain,
        'target_id',v_intent.target_id,'operation',v_intent.operation,
        'expected_state_version',v_intent.expected_state_version);
END;
$$;

REVOKE ALL ON FUNCTION _agent_runtime_recovery_snapshot_row(TEXT,TEXT,UUID,TEXT,BIGINT,UUID,UUID,UUID,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,TEXT,TEXT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN,JSONB),
    list_agent_runtime_recovery_snapshot(UUID,TEXT,TEXT,INTEGER),
    request_agent_runtime_recovery(UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT),
    claim_agent_runtime_recovery(UUID,TEXT,INTEGER)
    FROM PUBLIC, everydayai_runtime, everydayai_worker, everydayai_wecom_runtime,
         everydayai_projection_worker, everydayai_authorization_worker,
         everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION list_agent_runtime_recovery_snapshot(UUID,TEXT,TEXT,INTEGER),
    request_agent_runtime_recovery(UUID,UUID,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT)
    TO everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_recovery(UUID,TEXT,INTEGER)
    TO everydayai_agent_runtime_worker;

RESET ROLE;
