-- 220_26: Audited, tenant-scoped Projection dead-stream recovery.
SET LOCAL ROLE everydayai_owner;

ALTER TABLE agent_projection_outbox
    ADD COLUMN recovery_version BIGINT NOT NULL DEFAULT 0
        CHECK (recovery_version >= 0),
    ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0
        CHECK (recovery_count >= 0);

CREATE TABLE agent_projection_dead_recoveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recovery_request_id UUID NOT NULL UNIQUE,
    outbox_id UUID NOT NULL
        REFERENCES agent_projection_outbox(id) ON DELETE RESTRICT,
    event_id UUID NOT NULL REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    projection_kind TEXT NOT NULL
        CHECK (projection_kind IN ('web_runtime', 'wecom')),
    event_sequence BIGINT NOT NULL CHECK (event_sequence > 0),
    recovery_sequence INTEGER NOT NULL CHECK (recovery_sequence > 0),
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL CHECK (
        reason = btrim(reason) AND length(reason) BETWEEN 1 AND 500
    ),
    expected_status TEXT NOT NULL CHECK (expected_status = 'dead'),
    expected_recovery_version BIGINT NOT NULL
        CHECK (expected_recovery_version >= 0),
    expected_attempt_count INTEGER NOT NULL CHECK (expected_attempt_count >= 8),
    previous_status TEXT NOT NULL CHECK (previous_status = 'dead'),
    previous_attempt_count INTEGER NOT NULL CHECK (previous_attempt_count >= 8),
    previous_last_error_code TEXT,
    previous_next_attempt_at TIMESTAMPTZ NOT NULL,
    not_before TIMESTAMPTZ NOT NULL,
    database_request_id TEXT NOT NULL CHECK (
        database_request_id = btrim(database_request_id)
        AND length(database_request_id) BETWEEN 1 AND 128
    ),
    requeued_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (outbox_id, recovery_sequence)
);

ALTER TABLE agent_projection_dead_recoveries ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_projection_dead_recoveries_owner_all
    ON agent_projection_dead_recoveries
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_projection_dead_recoveries FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE agent_projection_dead_recoveries
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

ALTER FUNCTION claim_agent_projection_outbox(INTEGER, INTEGER)
    RENAME TO _claim_agent_projection_outbox_215;
ALTER FUNCTION apply_agent_compat_projection(UUID, UUID, TEXT)
    RENAME TO _apply_agent_compat_projection_220_12;
REVOKE ALL ON FUNCTION
    _claim_agent_projection_outbox_215(INTEGER, INTEGER),
    _apply_agent_compat_projection_220_12(UUID, UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

CREATE FUNCTION claim_agent_projection_outbox(
    p_batch_size INTEGER DEFAULT 50, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_rows JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_CLAIM_INVALID'
            USING ERRCODE = '22023';
    END IF;
    WITH candidates AS (
        SELECT id FROM agent_projection_outbox
         WHERE projection_kind = 'audit'
           AND next_attempt_at <= clock_timestamp()
           AND (
               status = 'pending'
               OR (status = 'processing'
                   AND lease_expires_at <= clock_timestamp())
           )
         ORDER BY next_attempt_at, created_at, id
         FOR UPDATE SKIP LOCKED LIMIT p_batch_size
    ), claimed AS (
        UPDATE agent_projection_outbox outbox SET status = 'processing',
               attempt_count = attempt_count + 1,
               lease_token = gen_random_uuid(),
               lease_expires_at = clock_timestamp()
                   + make_interval(secs => p_lease_seconds),
               updated_at = clock_timestamp()
          FROM candidates WHERE outbox.id = candidates.id
        RETURNING outbox.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_rows FROM claimed;
    RETURN v_rows;
END;
$$;

CREATE FUNCTION apply_agent_compat_projection(
    p_outbox_id UUID, p_lease_token UUID, p_action TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_session_id UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT session_id INTO v_session_id
      FROM agent_projection_outbox WHERE id = p_outbox_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    IF NOT EXISTS (
        SELECT 1 FROM agent_projection_outbox
         WHERE id = p_outbox_id AND session_id = v_session_id
    ) THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_ASSOCIATION_INVALID'
            USING ERRCODE = '55000';
    END IF;
    RETURN _apply_agent_compat_projection_220_12(
        p_outbox_id, p_lease_token, p_action
    );
END;
$$;

CREATE FUNCTION list_agent_projection_dead_items(p_limit INTEGER DEFAULT 50)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_items JSONB; v_actor UUID := tenant_actor_user_id();
BEGIN
    IF NOT tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    IF p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_DEAD_INSPECT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(item) ORDER BY item.created_at), '[]')
      INTO v_items
      FROM (
        SELECT outbox.id AS outbox_id, outbox.event_id, outbox.session_id,
               outbox.projection_kind, event.sequence AS event_sequence,
               event.event_type, outbox.status, outbox.attempt_count,
               outbox.next_attempt_at, outbox.lease_expires_at,
               outbox.last_error_code, outbox.recovery_version,
               outbox.recovery_count, outbox.created_at,
               checkpoint.through_sequence,
               checkpoint.state_version AS checkpoint_state_version
          FROM agent_projection_outbox outbox
          JOIN agent_runtime_events event ON event.id = outbox.event_id
          LEFT JOIN agent_compat_projection_checkpoints checkpoint
            ON checkpoint.session_id = outbox.session_id
           AND checkpoint.projection_kind = outbox.projection_kind
         WHERE outbox.status = 'dead'
           AND outbox.projection_kind IN ('web_runtime', 'wecom')
           AND (
               (outbox.org_id IS NOT NULL
                AND outbox.org_id = tenant_org_id())
               OR (outbox.org_id IS NULL AND outbox.user_id = v_actor)
           )
         ORDER BY outbox.created_at, outbox.id LIMIT p_limit
      ) item;
    RETURN jsonb_build_object('outcome', 'found', 'items', v_items);
END;
$$;

CREATE FUNCTION get_agent_projection_dead_item(p_outbox_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_item JSONB; v_actor UUID := tenant_actor_user_id();
BEGIN
    IF NOT tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    SELECT jsonb_build_object(
               'outbox_id', outbox.id, 'event_id', outbox.event_id,
               'session_id', outbox.session_id,
               'projection_kind', outbox.projection_kind,
               'event_sequence', event.sequence,
               'event_type', event.event_type, 'status', outbox.status,
               'attempt_count', outbox.attempt_count,
               'next_attempt_at', outbox.next_attempt_at,
               'lease_expires_at', outbox.lease_expires_at,
               'last_error_code', outbox.last_error_code,
               'recovery_version', outbox.recovery_version,
               'recovery_count', outbox.recovery_count,
               'through_sequence', checkpoint.through_sequence,
               'checkpoint_state_version', checkpoint.state_version)
      INTO v_item
      FROM agent_projection_outbox outbox
      JOIN agent_runtime_events event ON event.id = outbox.event_id
      LEFT JOIN agent_compat_projection_checkpoints checkpoint
        ON checkpoint.session_id = outbox.session_id
       AND checkpoint.projection_kind = outbox.projection_kind
     WHERE outbox.id = p_outbox_id AND outbox.status = 'dead'
       AND outbox.projection_kind IN ('web_runtime', 'wecom')
       AND (
           (outbox.org_id IS NOT NULL AND outbox.org_id = tenant_org_id())
           OR (outbox.org_id IS NULL AND outbox.user_id = v_actor)
       );
    IF v_item IS NULL THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    RETURN jsonb_build_object('outcome', 'found', 'item', v_item);
END;
$$;

CREATE FUNCTION requeue_agent_projection_dead(
    p_outbox_id UUID, p_expected_status TEXT,
    p_expected_recovery_version BIGINT, p_expected_attempt_count INTEGER,
    p_recovery_request_id UUID, p_reason TEXT,
    p_not_before TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_actor UUID := tenant_actor_user_id();
    v_request_id TEXT := current_setting('app.request_id', TRUE);
    v_session_id UUID; v_outbox agent_projection_outbox%ROWTYPE;
    v_session agent_runtime_sessions%ROWTYPE;
    v_event agent_runtime_events%ROWTYPE;
    v_checkpoint agent_compat_projection_checkpoints%ROWTYPE;
    v_audit agent_projection_dead_recoveries%ROWTYPE;
BEGIN
    IF NOT tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED' USING ERRCODE = '42501';
    END IF;
    IF p_expected_status IS DISTINCT FROM 'dead'
       OR p_expected_recovery_version < 0
       OR p_expected_attempt_count < 8
       OR p_recovery_request_id IS NULL
       OR p_reason IS NULL OR p_reason <> btrim(p_reason)
       OR length(p_reason) NOT BETWEEN 1 AND 500
       OR NULLIF(btrim(v_request_id), '') IS NULL
       OR p_not_before IS NULL THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_DEAD_REQUEUE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'agent-projection-recovery:' || p_recovery_request_id::TEXT, 0
    ));
    SELECT * INTO v_audit FROM agent_projection_dead_recoveries
     WHERE recovery_request_id = p_recovery_request_id;
    IF FOUND THEN
        IF NOT (
               (v_audit.org_id IS NOT NULL
                AND v_audit.org_id = tenant_org_id())
               OR (v_audit.org_id IS NULL AND v_audit.user_id = v_actor)
           ) THEN
            RAISE EXCEPTION 'AGENT_PROJECTION_DEAD_SCOPE_MISMATCH'
                USING ERRCODE = '42501';
        END IF;
        IF v_audit.outbox_id IS DISTINCT FROM p_outbox_id
           OR v_audit.actor_user_id IS DISTINCT FROM v_actor
           OR v_audit.reason IS DISTINCT FROM p_reason
           OR v_audit.not_before IS DISTINCT FROM p_not_before
           OR v_audit.expected_status IS DISTINCT FROM p_expected_status
           OR v_audit.expected_recovery_version
              IS DISTINCT FROM p_expected_recovery_version
           OR v_audit.expected_attempt_count
              IS DISTINCT FROM p_expected_attempt_count THEN
            RETURN jsonb_build_object('outcome', 'recovery_request_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_requeued', 'outbox_id', v_audit.outbox_id,
            'audit_id', v_audit.id,
            'recovery_version', v_audit.expected_recovery_version + 1,
            'recovery_count', v_audit.recovery_sequence,
            'attempt_count', v_audit.previous_attempt_count,
            'next_attempt_at', v_audit.not_before);
    END IF;
    IF p_not_before < clock_timestamp() - interval '1 second'
       OR p_not_before > clock_timestamp() + interval '24 hours' THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_DEAD_REQUEUE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT session_id INTO v_session_id FROM agent_projection_outbox
     WHERE id = p_outbox_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_outbox FROM agent_projection_outbox
     WHERE id = p_outbox_id FOR UPDATE;
    IF v_outbox.session_id IS DISTINCT FROM v_session_id THEN
        RETURN jsonb_build_object('outcome', 'wrong_stream');
    END IF;
    IF NOT (
        (v_outbox.org_id IS NOT NULL AND v_outbox.org_id = tenant_org_id())
        OR (v_outbox.org_id IS NULL AND v_outbox.user_id = v_actor)
    ) THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_DEAD_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_outbox.status IS DISTINCT FROM 'dead' THEN
        RETURN jsonb_build_object('outcome', 'not_dead');
    END IF;
    IF v_outbox.recovery_version <> p_expected_recovery_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF v_outbox.attempt_count <> p_expected_attempt_count THEN
        RETURN jsonb_build_object('outcome', 'attempt_count_conflict');
    END IF;
    IF v_outbox.projection_kind NOT IN ('web_runtime', 'wecom') THEN
        RETURN jsonb_build_object('outcome', 'wrong_stream');
    END IF;
    SELECT * INTO v_event FROM agent_runtime_events
     WHERE id = v_outbox.event_id FOR SHARE;
    SELECT * INTO v_checkpoint FROM agent_compat_projection_checkpoints
     WHERE session_id = v_outbox.session_id
       AND projection_kind = v_outbox.projection_kind FOR UPDATE;
    IF v_session.id IS NULL
       OR v_session.org_id IS DISTINCT FROM v_outbox.org_id
       OR v_session.user_id IS DISTINCT FROM v_outbox.user_id
       OR v_event.id IS NULL
       OR v_event.session_id <> v_outbox.session_id
       OR v_event.org_id IS DISTINCT FROM v_outbox.org_id
       OR v_event.user_id IS DISTINCT FROM v_outbox.user_id
       OR v_checkpoint.session_id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'wrong_stream');
    END IF;
    IF v_checkpoint.through_sequence >= v_event.sequence THEN
        RETURN jsonb_build_object('outcome', 'checkpoint_already_advanced');
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_compat_projection_results
         WHERE outbox_id = v_outbox.id
            OR (session_id = v_outbox.session_id
                AND projection_kind = v_outbox.projection_kind
                AND event_sequence = v_event.sequence)
    ) THEN
        RETURN jsonb_build_object('outcome', 'projection_result_conflict');
    END IF;
    INSERT INTO agent_projection_dead_recoveries(
        recovery_request_id, outbox_id, event_id, session_id, org_id, user_id,
        projection_kind, event_sequence, recovery_sequence, actor_user_id,
        reason, expected_status, expected_recovery_version,
        expected_attempt_count, previous_status, previous_attempt_count,
        previous_last_error_code, previous_next_attempt_at, not_before,
        database_request_id
    ) VALUES (
        p_recovery_request_id, v_outbox.id, v_event.id, v_outbox.session_id,
        v_outbox.org_id, v_outbox.user_id, v_outbox.projection_kind,
        v_event.sequence, v_outbox.recovery_count + 1, v_actor, p_reason,
        p_expected_status, p_expected_recovery_version,
        p_expected_attempt_count, v_outbox.status, v_outbox.attempt_count,
        v_outbox.last_error_code, v_outbox.next_attempt_at, p_not_before,
        btrim(v_request_id)
    ) RETURNING * INTO v_audit;
    UPDATE agent_projection_outbox SET status = 'pending',
           lease_token = NULL, lease_expires_at = NULL,
           next_attempt_at = p_not_before,
           recovery_version = recovery_version + 1,
           recovery_count = recovery_count + 1,
           updated_at = clock_timestamp()
     WHERE id = v_outbox.id RETURNING * INTO v_outbox;
    RETURN jsonb_build_object(
        'outcome', 'requeued', 'outbox_id', v_outbox.id,
        'audit_id', v_audit.id,
        'recovery_version', v_outbox.recovery_version,
        'recovery_count', v_outbox.recovery_count,
        'attempt_count', v_outbox.attempt_count,
        'next_attempt_at', v_outbox.next_attempt_at);
END;
$$;

REVOKE ALL ON FUNCTION
    claim_agent_projection_outbox(INTEGER, INTEGER),
    apply_agent_compat_projection(UUID, UUID, TEXT),
    list_agent_projection_dead_items(INTEGER),
    get_agent_projection_dead_item(UUID),
    requeue_agent_projection_dead(
        UUID, TEXT, BIGINT, INTEGER, UUID, TEXT, TIMESTAMPTZ)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    claim_agent_projection_outbox(INTEGER, INTEGER),
    apply_agent_compat_projection(UUID, UUID, TEXT)
TO everydayai_worker;
GRANT EXECUTE ON FUNCTION
    list_agent_projection_dead_items(INTEGER),
    get_agent_projection_dead_item(UUID),
    requeue_agent_projection_dead(
        UUID, TEXT, BIGINT, INTEGER, UUID, TEXT, TIMESTAMPTZ)
TO everydayai_runtime;

RESET ROLE;
