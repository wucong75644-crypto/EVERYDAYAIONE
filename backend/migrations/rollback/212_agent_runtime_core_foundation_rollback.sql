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

DROP FUNCTION IF EXISTS
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
