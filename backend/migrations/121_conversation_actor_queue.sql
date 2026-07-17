-- 121: Conversation Actor 持久队列与执行权基础
-- 依赖 120_turn_revision_foundation.sql。
-- 本迁移只增加兼容字段和 enqueue/claim/renew RPC，不切换现有业务链路。

CREATE SEQUENCE IF NOT EXISTS task_queue_sequence_seq AS BIGINT;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS queue_sequence BIGINT,
    ADD COLUMN IF NOT EXISTS execution_token UUID,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS execution_attempt INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS delivery_context JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS terminal_reason TEXT;

ALTER TABLE tasks
    ALTER COLUMN queue_sequence SET DEFAULT nextval('task_queue_sequence_seq');

UPDATE tasks
   SET queue_sequence = nextval('task_queue_sequence_seq')
 WHERE queue_sequence IS NULL;

SELECT setval(
    'task_queue_sequence_seq',
    GREATEST((SELECT COALESCE(MAX(queue_sequence), 1) FROM tasks), 1),
    TRUE
);

ALTER TABLE tasks
    ALTER COLUMN queue_sequence SET NOT NULL,
    DROP CONSTRAINT IF EXISTS tasks_execution_attempt_check,
    ADD CONSTRAINT tasks_execution_attempt_check CHECK (execution_attempt >= 0),
    DROP CONSTRAINT IF EXISTS tasks_delivery_context_object_check,
    ADD CONSTRAINT tasks_delivery_context_object_check
        CHECK (jsonb_typeof(delivery_context) = 'object');

ALTER SEQUENCE task_queue_sequence_seq OWNED BY tasks.queue_sequence;

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS active_serial_task_id UUID,
    ADD COLUMN IF NOT EXISTS actor_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_active_serial_task_id_fkey,
    ADD CONSTRAINT conversations_active_serial_task_id_fkey
        FOREIGN KEY (active_serial_task_id) REFERENCES tasks(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_actor_queue
    ON tasks(conversation_id, execution_mode, status, queue_sequence)
    WHERE type = 'chat';
CREATE INDEX IF NOT EXISTS idx_tasks_actor_expired_lease
    ON tasks(lease_expires_at)
    WHERE type = 'chat' AND status = 'running';
CREATE INDEX IF NOT EXISTS idx_tasks_actor_active
    ON tasks(conversation_id, status)
    WHERE type = 'chat' AND status IN ('pending', 'running');

CREATE OR REPLACE FUNCTION enqueue_generation_turn(
    p_task_data JSONB,
    p_input_message_id UUID,
    p_turn_id UUID,
    p_execution_mode TEXT DEFAULT 'serial',
    p_delivery_context JSONB DEFAULT '{}'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_input messages%ROWTYPE;
    v_output messages%ROWTYPE;
    v_task_id UUID;
    v_output_id UUID;
    v_user_id UUID;
    v_org_id UUID;
    v_conversation_id UUID;
    v_inserted_count BIGINT;
BEGIN
    IF p_task_data IS NULL
       OR jsonb_typeof(p_task_data) <> 'object'
       OR p_input_message_id IS NULL
       OR p_turn_id IS NULL
       OR p_execution_mode NOT IN ('serial', 'branch')
       OR p_delivery_context IS NULL
       OR jsonb_typeof(p_delivery_context) <> 'object' THEN
        RAISE EXCEPTION 'ACTOR_ENQUEUE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    BEGIN
        v_task_id := (p_task_data->>'id')::UUID;
        v_output_id := (p_task_data->>'assistant_message_id')::UUID;
        v_user_id := (p_task_data->>'user_id')::UUID;
        v_org_id := NULLIF(p_task_data->>'org_id', '')::UUID;
        v_conversation_id := (p_task_data->>'conversation_id')::UUID;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'ACTOR_ENQUEUE_ID_INVALID' USING ERRCODE = '22023';
    END;
    IF v_task_id IS NULL
       OR v_output_id IS NULL
       OR v_user_id IS NULL
       OR v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_ENQUEUE_ID_MISSING' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    IF NOT FOUND
       OR v_conversation.user_id IS DISTINCT FROM v_user_id
       OR v_conversation.org_id IS DISTINCT FROM v_org_id THEN
        RAISE EXCEPTION 'ACTOR_CONVERSATION_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_input FROM messages WHERE id = p_input_message_id FOR UPDATE;
    SELECT * INTO v_output FROM messages WHERE id = v_output_id FOR UPDATE;
    IF v_input.id IS NULL
       OR v_output.id IS NULL
       OR v_input.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_output.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_input.org_id IS DISTINCT FROM v_org_id
       OR v_output.org_id IS DISTINCT FROM v_org_id
       OR v_input.role::TEXT <> 'user'
       OR v_output.role::TEXT <> 'assistant'
       OR (v_input.turn_id IS NOT NULL AND v_input.turn_id IS DISTINCT FROM p_turn_id)
       OR (v_output.turn_id IS NOT NULL AND v_output.turn_id IS DISTINCT FROM p_turn_id)
       OR (v_output.reply_to_message_id IS NOT NULL
           AND v_output.reply_to_message_id IS DISTINCT FROM p_input_message_id) THEN
        RAISE EXCEPTION 'ACTOR_MESSAGE_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;

    INSERT INTO tasks(
        id, external_task_id, client_task_id, user_id, org_id, conversation_id,
        type, status, model_id, placeholder_message_id, assistant_message_id,
        request_params, placeholder_created_at, input_message_id, turn_id,
        execution_mode, delivery_context
    ) VALUES (
        v_task_id, p_task_data->>'external_task_id',
        p_task_data->>'client_task_id', v_user_id, v_org_id, v_conversation.id,
        'chat', 'pending', p_task_data->>'model_id',
        NULLIF(p_task_data->>'placeholder_message_id', '')::UUID,
        v_output_id, COALESCE(p_task_data->'request_params', '{}'::JSONB),
        NULLIF(p_task_data->>'placeholder_created_at', '')::TIMESTAMPTZ,
        p_input_message_id, p_turn_id, p_execution_mode, p_delivery_context
    )
    ON CONFLICT (id) DO NOTHING;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;

    SELECT * INTO v_task FROM tasks WHERE id = v_task_id FOR UPDATE;
    IF v_task.id IS NULL
       OR v_task.type <> 'chat'
       OR v_task.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_task.user_id IS DISTINCT FROM v_user_id
       OR v_task.org_id IS DISTINCT FROM v_org_id
       OR v_task.input_message_id IS DISTINCT FROM p_input_message_id
       OR v_task.turn_id IS DISTINCT FROM p_turn_id
       OR v_task.assistant_message_id IS DISTINCT FROM v_output_id
       OR v_task.execution_mode IS DISTINCT FROM p_execution_mode THEN
        RAISE EXCEPTION 'ACTOR_ENQUEUE_CONFLICT' USING ERRCODE = '23505';
    END IF;

    UPDATE messages SET turn_id = p_turn_id WHERE id = p_input_message_id;
    UPDATE messages
       SET turn_id = p_turn_id,
           reply_to_message_id = p_input_message_id
     WHERE id = v_output_id;

    RETURN jsonb_build_object(
        'task_id', v_task.id,
        'status', v_task.status,
        'queue_sequence', v_task.queue_sequence,
        'execution_mode', v_task.execution_mode,
        'already_enqueued', v_inserted_count = 0
    );
END;
$$;

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

CREATE OR REPLACE FUNCTION renew_generation_lease(
    p_task_id UUID,
    p_execution_token UUID,
    p_lease_seconds INTEGER DEFAULT 90
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
BEGIN
    IF p_execution_token IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'ACTOR_RENEW_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    UPDATE tasks
       SET lease_expires_at = NOW() + make_interval(secs => p_lease_seconds)
     WHERE id = p_task_id
     RETURNING * INTO v_task;
    RETURN jsonb_build_object(
        'outcome', 'renewed',
        'lease_expires_at', v_task.lease_expires_at
    );
END;
$$;

REVOKE ALL ON FUNCTION enqueue_generation_turn(JSONB, UUID, UUID, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_next_serial_generation_turn(UUID, INTEGER, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_branch_generation_turn(UUID, INTEGER, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION renew_generation_lease(UUID, UUID, INTEGER) FROM PUBLIC;

COMMENT ON FUNCTION enqueue_generation_turn(JSONB, UUID, UUID, TEXT, JSONB)
    IS '原子创建 pending Chat Turn；不在 enqueue 阶段绑定 context revision';
COMMENT ON FUNCTION claim_next_serial_generation_turn(UUID, INTEGER, INTEGER)
    IS '按 queue_sequence 认领 conversation 最早 serial Chat Turn，并绑定最新 context revision';
COMMENT ON FUNCTION claim_branch_generation_turn(UUID, INTEGER, INTEGER)
    IS '精确认领内部 branch Chat Turn，不占用 conversation serial owner';
COMMENT ON FUNCTION renew_generation_lease(UUID, UUID, INTEGER)
    IS '仅当前 fencing token 可续约 running Chat task';
