-- Disable new attachment commands. Preserve existing events and checkpoint readers.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION append_conversation_steer(
    p_conversation_id UUID,
    p_task_id UUID,
    p_turn_id UUID,
    p_dedupe_key TEXT,
    p_payload JSONB DEFAULT '{}'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_event conversation_control_events%ROWTYPE;
    v_inserted_count BIGINT;
    v_message TEXT;
BEGIN
    v_message := NULLIF(BTRIM(p_payload->>'message'), '');
    IF p_conversation_id IS NULL
       OR p_task_id IS NULL
       OR NULLIF(BTRIM(p_dedupe_key), '') IS NULL
       OR p_payload IS NULL
       OR jsonb_typeof(p_payload) <> 'object'
       OR p_payload ? 'attachments'
       OR v_message IS NULL
       OR length(v_message) > 20000 THEN
        RAISE EXCEPTION 'ACTOR_STEER_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.type <> 'chat'
       OR v_task.status <> 'running'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_STEER_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO conversation_control_events(
        conversation_id, task_id, turn_id, event_type, dedupe_key, payload
    ) VALUES (
        p_conversation_id, p_task_id, p_turn_id, 'steer',
        BTRIM(p_dedupe_key), jsonb_build_object('message', v_message)
    )
    ON CONFLICT (task_id, dedupe_key) DO NOTHING;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;

    SELECT * INTO v_event
      FROM conversation_control_events
     WHERE task_id = p_task_id
       AND dedupe_key = BTRIM(p_dedupe_key);

    RETURN jsonb_build_object(
        'outcome', 'enqueued',
        'event_id', v_event.id,
        'event_sequence', v_event.event_sequence,
        'already_enqueued', v_inserted_count = 0,
        'payload', v_event.payload
    );
END;
$$;

RESET ROLE;
