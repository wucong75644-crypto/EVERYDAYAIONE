-- 216: Minimal scoped read capabilities for Runtime repositories/projections.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _assert_agent_runtime_session_read(p_session_id UUID)
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_session agent_runtime_sessions%ROWTYPE;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM _assert_agent_runtime_actor(TRUE);
    ELSE
        PERFORM _assert_agent_runtime_actor(FALSE);
    END IF;
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF session_user <> 'everydayai_worker' AND (
        tenant_org_id() IS DISTINCT FROM v_session.org_id
        OR (
            v_session.scope_kind = 'user'
            AND tenant_actor_user_id() IS DISTINCT FROM v_session.user_id
        )
        OR (
            v_session.scope_kind = 'channel'
            AND NOT EXISTS (
                SELECT 1 FROM org_members member
                 WHERE member.org_id = v_session.org_id
                   AND member.user_id = tenant_actor_user_id()
                   AND member.status = 'active'
            )
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_READ_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE FUNCTION get_agent_runtime_session(p_session_id UUID)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_session agent_runtime_sessions%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_session_read(p_session_id);
    SELECT * INTO v_session FROM agent_runtime_sessions
     WHERE id = p_session_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'session', to_jsonb(v_session)
    );
END;
$$;

CREATE FUNCTION replay_agent_runtime_events(
    p_session_id UUID, p_after_sequence BIGINT DEFAULT 0,
    p_limit INTEGER DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_exists BOOLEAN;
    v_events JSONB;
    v_count BIGINT;
    v_min BIGINT;
    v_max BIGINT;
BEGIN
    IF p_after_sequence < 0 OR p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_REPLAY_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM _assert_agent_runtime_session_read(p_session_id);
    SELECT EXISTS(
        SELECT 1 FROM agent_runtime_sessions WHERE id = p_session_id
    ) INTO v_exists;
    IF NOT v_exists THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    WITH page AS (
        SELECT event.*
          FROM agent_runtime_events event
         WHERE event.session_id = p_session_id
           AND event.sequence > p_after_sequence
         ORDER BY event.sequence
         LIMIT p_limit
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(page) ORDER BY page.sequence), '[]'),
           count(*), min(sequence), max(sequence)
      INTO v_events, v_count, v_min, v_max
      FROM page;
    IF v_count > 0 AND (
        v_min <> p_after_sequence + 1
        OR v_max <> p_after_sequence + v_count
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_EVENT_SEQUENCE_GAP'
            USING ERRCODE = '55000';
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'events', v_events
    );
END;
$$;

CREATE FUNCTION get_agent_runtime_run_claim(
    p_run_id UUID, p_worker_id TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_attempt JSONB; v_state_version BIGINT; v_event_sequence BIGINT;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF NULLIF(BTRIM(p_worker_id), '') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CLAIM_READ_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT to_jsonb(attempt) || jsonb_build_object(
               'scope_kind', session.scope_kind,
               'scope_id', session.scope_id
           ), run.state_version, event.sequence
      INTO v_attempt, v_state_version, v_event_sequence
      FROM agent_run_attempts attempt
      JOIN agent_runs run ON run.id = attempt.run_id
      JOIN agent_runtime_sessions session ON session.id = run.session_id
      JOIN agent_runtime_events event
        ON event.session_id = run.session_id
       AND event.run_id = run.id
       AND event.event_type = 'run.claimed'
       AND event.correlation_id = attempt.execution_token
     WHERE attempt.run_id = p_run_id
       AND attempt.worker_id = BTRIM(p_worker_id)
       AND attempt.ended_at IS NULL
       AND run.status = 'running'
       AND run.execution_token = attempt.execution_token;
    IF v_attempt IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'attempt', v_attempt,
        'state_version', v_state_version,
        'event_sequence', v_event_sequence
    );
END;
$$;

CREATE FUNCTION get_claimed_agent_projection_event(
    p_outbox_id UUID, p_lease_token UUID
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_outbox agent_projection_outbox%ROWTYPE;
    v_event agent_runtime_events%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO v_outbox FROM agent_projection_outbox
     WHERE id = p_outbox_id;
    IF NOT FOUND OR v_outbox.status <> 'processing'
       OR v_outbox.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_outbox.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    SELECT * INTO v_event FROM agent_runtime_events
     WHERE id = v_outbox.event_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_EVENT_MISSING'
            USING ERRCODE = '55000';
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found',
        'outbox', to_jsonb(v_outbox),
        'event', to_jsonb(v_event)
    );
END;
$$;

REVOKE ALL ON FUNCTION
    _assert_agent_runtime_session_read(UUID),
    get_agent_runtime_session(UUID),
    replay_agent_runtime_events(UUID, BIGINT, INTEGER),
    get_agent_runtime_run_claim(UUID, TEXT),
    get_claimed_agent_projection_event(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

GRANT EXECUTE ON FUNCTION
    get_agent_runtime_session(UUID),
    replay_agent_runtime_events(UUID, BIGINT, INTEGER)
TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION
    get_agent_runtime_run_claim(UUID, TEXT),
    get_claimed_agent_projection_event(UUID, UUID)
TO everydayai_worker;

RESET ROLE;
