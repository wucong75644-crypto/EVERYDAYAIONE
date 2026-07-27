SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    prepare_model_attempt(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, TEXT, JSONB, INTEGER, INTEGER),
    start_model_attempt_dispatch(UUID, UUID, BIGINT, TEXT),
    mark_model_attempt_response_started(UUID, UUID, BIGINT, TEXT, TEXT),
    record_model_attempt_unknown(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, JSONB),
    complete_model_attempt_without_actions(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, JSONB, INTEGER
    ),
    fail_model_attempt_and_step(UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS
    fail_model_attempt_and_step(UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, TEXT),
    complete_model_attempt_without_actions(
        UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, JSONB, INTEGER
    ),
    record_model_attempt_unknown(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, JSONB),
    mark_model_attempt_response_started(UUID, UUID, BIGINT, TEXT, TEXT),
    start_model_attempt_dispatch(UUID, UUID, BIGINT, TEXT),
    prepare_model_attempt(UUID, UUID, BIGINT, TEXT, TEXT, TEXT, TEXT, JSONB, INTEGER, INTEGER);

CREATE OR REPLACE FUNCTION cancel_agent_run(
    p_run_id UUID, p_expected_state_version BIGINT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_event JSONB; v_session_id UUID;
BEGIN
    IF session_user = 'everydayai_worker' THEN
        PERFORM _assert_agent_runtime_actor(TRUE);
    ELSE
        PERFORM _assert_agent_runtime_actor(FALSE);
    END IF;
    SELECT session_id INTO v_session_id FROM agent_runs WHERE id = p_run_id;
    PERFORM 1 FROM agent_runtime_sessions
     WHERE id = v_session_id FOR UPDATE;
    SELECT * INTO v_run FROM agent_runs WHERE id = p_run_id FOR UPDATE;
    IF v_run.status = 'cancelled' THEN
        IF v_run.terminal_reason IS DISTINCT FROM p_reason THEN
            RETURN jsonb_build_object('outcome', 'terminal_conflict');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'already_cancelled', 'entity_id', v_run.id,
            'state_version', v_run.state_version
        );
    END IF;
    IF v_run.status IN ('completed', 'failed') THEN
        RETURN jsonb_build_object('outcome', 'terminal_conflict');
    END IF;
    IF v_run.state_version <> p_expected_state_version THEN
        RETURN jsonb_build_object('outcome', 'stale_version');
    END IF;
    IF session_user <> 'everydayai_worker' AND (
        tenant_org_id() IS DISTINCT FROM v_run.org_id
        OR NOT EXISTS (
            SELECT 1 FROM agent_runtime_sessions session
             WHERE session.id = v_run.session_id
               AND (
                   (session.scope_kind = 'user'
                    AND session.user_id = tenant_actor_user_id())
                   OR (
                       session.scope_kind = 'channel'
                       AND EXISTS (
                           SELECT 1 FROM org_members member
                            WHERE member.org_id = session.org_id
                              AND member.user_id = tenant_actor_user_id()
                              AND member.status = 'active'
                       )
                   )
               )
        )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_CANCEL_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    UPDATE agent_runs SET status = 'cancelled', execution_token = NULL,
           lease_expires_at = NULL, completed_at = clock_timestamp(),
           terminal_reason = p_reason, state_version = state_version + 1,
           updated_at = clock_timestamp()
     WHERE id = p_run_id RETURNING * INTO v_run;
    UPDATE agent_run_attempts SET ended_at = clock_timestamp(),
           outcome = 'cancelled'
     WHERE run_id = p_run_id AND ended_at IS NULL;
    v_event := append_agent_runtime_event(
        v_run.session_id, 'run.cancelled', v_run.id, NULL,
        gen_random_uuid(), CASE WHEN session_user = 'everydayai_worker'
            THEN 'system' ELSE 'user' END, session_user,
        jsonb_build_object('reason', p_reason),
        ARRAY['web_runtime', 'audit']::TEXT[]
    );
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'entity_id', v_run.id,
        'state_version', v_run.state_version,
        'event_sequence', v_event->'event_sequence'
    );
END;
$$;

REVOKE ALL ON FUNCTION cancel_agent_run(UUID, BIGINT, TEXT)
FROM PUBLIC, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION cancel_agent_run(UUID, BIGINT, TEXT)
TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

RESET ROLE;
