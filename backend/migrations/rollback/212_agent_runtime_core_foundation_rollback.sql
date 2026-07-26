-- Roll back migration 212 only when no Agent Runtime business fact exists.

SET LOCAL ROLE everydayai_owner;

DO $$
DECLARE
    v_table TEXT;
    v_count BIGINT;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'agent_runtime_sessions',
        'agent_session_commands',
        'agent_runs',
        'agent_run_attempts',
        'agent_model_steps',
        'agent_runtime_events',
        'agent_projection_outbox'
    ]
    LOOP
        IF to_regclass('public.' || v_table) IS NOT NULL THEN
            EXECUTE format('SELECT count(*) FROM public.%I', v_table)
               INTO v_count;
            IF v_count <> 0 THEN
                RAISE EXCEPTION 'AGENT_RUNTIME_ROLLBACK_FACTS_PRESENT:%',
                    v_table USING ERRCODE = '55000';
            END IF;
        END IF;
    END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    ),
    submit_session_command(UUID, TEXT, TEXT, JSONB),
    cancel_agent_run(UUID, BIGINT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    renew_agent_run(UUID, UUID, INTEGER),
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT),
    wake_agent_run(UUID, BIGINT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT),
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    fail_agent_projection_outbox(UUID, UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS
    fail_agent_projection_outbox(UUID, UUID, TEXT),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB),
    cancel_agent_run(UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    _finish_agent_run(UUID, UUID, BIGINT, TEXT, TEXT, TEXT),
    wake_agent_run(UUID, BIGINT),
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT),
    renew_agent_run(UUID, UUID, INTEGER),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    submit_session_command(UUID, TEXT, TEXT, JSONB),
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    ),
    append_agent_runtime_event(
        UUID, TEXT, UUID, UUID, UUID, TEXT, TEXT, JSONB, TEXT[]
    ),
    _assert_agent_runtime_actor(BOOLEAN);

DROP TABLE IF EXISTS agent_projection_outbox;
DROP TABLE IF EXISTS agent_runtime_events;
DROP TABLE IF EXISTS agent_model_steps;
DROP TABLE IF EXISTS agent_run_attempts;
DROP TABLE IF EXISTS agent_runs;
DROP TABLE IF EXISTS agent_session_commands;
DROP TABLE IF EXISTS agent_runtime_sessions;

RESET ROLE;
