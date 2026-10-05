-- Stop new accepts and drain new-source jobs before this rollback.
-- Private task catalogs remain intact. Do not delete active evidence.
RESET ROLE;
DO $owner$ DECLARE v_owner TEXT; BEGIN
    SELECT pg_get_userbyid(proowner) INTO v_owner FROM pg_proc
        WHERE oid='public.claim_next_serial_generation_turn(uuid,integer,integer)'::regprocedure;
    IF v_owner NOT IN ('everydayai','everydayai_owner') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_CLAIM_OWNER_UNSUPPORTED: %',v_owner;
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',v_owner);
END $owner$;
CREATE OR REPLACE FUNCTION claim_next_serial_generation_turn(
    p_conversation_id UUID,
    p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_owner tasks%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_token UUID;
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300 OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'ACTOR_CLAIM_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_CONVERSATION_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    IF v_conversation.active_serial_task_id IS NOT NULL THEN
        SELECT * INTO v_owner
          FROM tasks
         WHERE id = v_conversation.active_serial_task_id
         FOR UPDATE;
        IF v_owner.id IS NOT NULL
           AND v_owner.status = 'running'
           AND v_owner.lease_expires_at > NOW() THEN
            RETURN jsonb_build_object('outcome', 'busy');
        END IF;
        IF v_owner.id IS NOT NULL AND v_owner.status = 'running' THEN
            IF v_owner.execution_attempt >= p_max_attempts THEN
                UPDATE tasks
                   SET status = 'failed',
                       terminal_reason = 'lease_attempts_exhausted',
                       completed_at = NOW(),
                       execution_token = NULL,
                       lease_expires_at = NULL
                 WHERE id = v_owner.id;
            ELSE
                UPDATE tasks
                   SET status = 'pending',
                       terminal_reason = 'lease_expired',
                       execution_token = NULL,
                       lease_expires_at = NULL
                 WHERE id = v_owner.id;
            END IF;
        END IF;
        UPDATE conversations
           SET active_serial_task_id = NULL,
               actor_updated_at = NOW()
         WHERE id = p_conversation_id;
    END IF;

    SELECT * INTO v_task
      FROM tasks
     WHERE conversation_id = p_conversation_id
       AND type = 'chat'
       AND delivery_context @> '{"actor": true}'::JSONB
       AND execution_mode = 'serial'
       AND status = 'pending'
       AND input_message_id IS NOT NULL
       AND turn_id IS NOT NULL
     ORDER BY queue_sequence, id
     FOR UPDATE SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'empty');
    END IF;

    v_token := uuid_generate_v4();
    UPDATE tasks
       SET status = 'running',
           execution_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           execution_attempt = execution_attempt + 1,
           started_at = COALESCE(started_at, NOW()),
           base_context_revision = v_conversation.context_revision,
           context_through_message_id = v_conversation.last_closed_message_id,
           terminal_reason = NULL
     WHERE id = v_task.id
     RETURNING * INTO v_task;

    UPDATE conversations
       SET active_serial_task_id = v_task.id,
           actor_updated_at = NOW()
     WHERE id = p_conversation_id;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'task_id', v_task.id,
        'execution_token', v_token,
        'turn_id', v_task.turn_id,
        'input_message_id', v_task.input_message_id,
        'base_context_revision', v_task.base_context_revision,
        'context_through_message_id', v_task.context_through_message_id,
        'execution_attempt', v_task.execution_attempt
    );
END;
$$;

RESET ROLE;
DO $owner$ DECLARE v_owner TEXT; BEGIN
    SELECT pg_get_userbyid(proowner) INTO v_owner FROM pg_proc
        WHERE oid='public.claim_branch_generation_turn(uuid,integer,integer)'::regprocedure;
    IF v_owner NOT IN ('everydayai','everydayai_owner') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_CLAIM_OWNER_UNSUPPORTED: %',v_owner;
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',v_owner);
END $owner$;
CREATE OR REPLACE FUNCTION claim_branch_generation_turn(
    p_task_id UUID,
    p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_token UUID;
    v_conversation_id UUID;
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300 OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'ACTOR_CLAIM_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT conversation_id INTO v_conversation_id FROM tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = v_conversation_id
     FOR UPDATE;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL
       OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.execution_mode <> 'branch'
       OR v_task.input_message_id IS NULL
       OR v_task.turn_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_BRANCH_NOT_CLAIMABLE' USING ERRCODE = '55000';
    END IF;
    IF v_task.status = 'running' AND v_task.lease_expires_at > NOW() THEN
        RETURN jsonb_build_object('outcome', 'busy');
    END IF;
    IF v_task.status = 'running' AND v_task.execution_attempt >= p_max_attempts THEN
        UPDATE tasks
           SET status = 'failed',
               terminal_reason = 'lease_attempts_exhausted',
               completed_at = NOW(),
               execution_token = NULL,
               lease_expires_at = NULL
         WHERE id = p_task_id;
        RETURN jsonb_build_object('outcome', 'attempts_exhausted');
    END IF;
    IF v_task.status NOT IN ('pending', 'running') THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;

    v_token := uuid_generate_v4();
    UPDATE tasks
       SET status = 'running',
           execution_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           execution_attempt = execution_attempt + 1,
           started_at = COALESCE(started_at, NOW()),
           base_context_revision = v_conversation.context_revision,
           context_through_message_id = v_conversation.last_closed_message_id,
           terminal_reason = NULL
     WHERE id = p_task_id
     RETURNING * INTO v_task;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'task_id', v_task.id,
        'execution_token', v_token,
        'turn_id', v_task.turn_id,
        'input_message_id', v_task.input_message_id,
        'base_context_revision', v_task.base_context_revision,
        'context_through_message_id', v_task.context_through_message_id,
        'execution_attempt', v_task.execution_attempt
    );
END;
$$;

RESET ROLE;
SET LOCAL ROLE everydayai;
DROP TRIGGER IF EXISTS guard_chat_image_sources ON public.tasks;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;
DROP FUNCTION public.guard_chat_image_sources();
DROP FUNCTION public.freeze_chat_image_sources(public.tasks,public.conversations);
RESET ROLE;
