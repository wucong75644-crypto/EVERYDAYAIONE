-- 120: Turn / context revision 数据库基础
-- 仅增加兼容字段和事务 RPC；业务链路在后续版本接入。

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS turn_id UUID,
    ADD COLUMN IF NOT EXISTS reply_to_message_id UUID,
    ADD COLUMN IF NOT EXISTS context_revision BIGINT,
    ADD COLUMN IF NOT EXISTS message_kind TEXT NOT NULL DEFAULT 'conversation';

ALTER TABLE messages
    DROP CONSTRAINT IF EXISTS messages_reply_to_message_id_fkey,
    ADD CONSTRAINT messages_reply_to_message_id_fkey
        FOREIGN KEY (reply_to_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    DROP CONSTRAINT IF EXISTS messages_context_revision_check,
    ADD CONSTRAINT messages_context_revision_check
        CHECK (context_revision IS NULL OR context_revision >= 0),
    DROP CONSTRAINT IF EXISTS messages_message_kind_check,
    ADD CONSTRAINT messages_message_kind_check
        CHECK (message_kind IN ('conversation', 'synthetic', 'tool_internal'));

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS input_message_id UUID,
    ADD COLUMN IF NOT EXISTS turn_id UUID,
    ADD COLUMN IF NOT EXISTS base_context_revision BIGINT,
    ADD COLUMN IF NOT EXISTS context_through_message_id UUID,
    ADD COLUMN IF NOT EXISTS execution_mode TEXT NOT NULL DEFAULT 'serial';

ALTER TABLE tasks
    DROP CONSTRAINT IF EXISTS tasks_input_message_id_fkey,
    ADD CONSTRAINT tasks_input_message_id_fkey
        FOREIGN KEY (input_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    DROP CONSTRAINT IF EXISTS tasks_context_through_message_id_fkey,
    ADD CONSTRAINT tasks_context_through_message_id_fkey
        FOREIGN KEY (context_through_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    DROP CONSTRAINT IF EXISTS tasks_base_context_revision_check,
    ADD CONSTRAINT tasks_base_context_revision_check
        CHECK (base_context_revision IS NULL OR base_context_revision >= 0),
    DROP CONSTRAINT IF EXISTS tasks_execution_mode_check,
    ADD CONSTRAINT tasks_execution_mode_check
        CHECK (execution_mode IN ('serial', 'branch'));

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS context_revision BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_closed_message_id UUID,
    ADD COLUMN IF NOT EXISTS summary_revision BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS summary_through_message_id UUID;

ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_last_closed_message_id_fkey,
    ADD CONSTRAINT conversations_last_closed_message_id_fkey
        FOREIGN KEY (last_closed_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    DROP CONSTRAINT IF EXISTS conversations_summary_through_message_id_fkey,
    ADD CONSTRAINT conversations_summary_through_message_id_fkey
        FOREIGN KEY (summary_through_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    DROP CONSTRAINT IF EXISTS conversations_context_revision_check,
    ADD CONSTRAINT conversations_context_revision_check CHECK (context_revision >= 0),
    DROP CONSTRAINT IF EXISTS conversations_summary_revision_check,
    ADD CONSTRAINT conversations_summary_revision_check CHECK (summary_revision >= 0);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_revision_created
    ON messages(conversation_id, context_revision, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_turn
    ON messages(conversation_id, turn_id);
CREATE INDEX IF NOT EXISTS idx_messages_reply_to
    ON messages(reply_to_message_id) WHERE reply_to_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_conversation_turn
    ON tasks(conversation_id, turn_id);
CREATE INDEX IF NOT EXISTS idx_tasks_input_message
    ON tasks(input_message_id) WHERE input_message_id IS NOT NULL;

CREATE OR REPLACE FUNCTION bind_generation_turn(
    p_conversation_id UUID,
    p_task_id UUID,
    p_input_message_id UUID,
    p_turn_id UUID,
    p_execution_mode TEXT DEFAULT 'serial'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_input messages%ROWTYPE;
    v_output messages%ROWTYPE;
BEGIN
    IF p_turn_id IS NULL
       OR p_execution_mode IS NULL
       OR p_execution_mode NOT IN ('serial', 'branch') THEN
        RAISE EXCEPTION 'TURN_BIND_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TURN_CONVERSATION_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.org_id IS DISTINCT FROM v_conversation.org_id THEN
        RAISE EXCEPTION 'TURN_TASK_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_input FROM messages WHERE id = p_input_message_id;
    IF NOT FOUND
       OR v_input.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_input.org_id IS DISTINCT FROM v_conversation.org_id
       OR v_input.role::TEXT <> 'user' THEN
        RAISE EXCEPTION 'TURN_INPUT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_output FROM messages WHERE id = v_task.assistant_message_id;
    IF NOT FOUND
       OR v_output.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_output.org_id IS DISTINCT FROM v_conversation.org_id
       OR v_output.role::TEXT <> 'assistant'
       OR (v_output.turn_id IS NOT NULL AND v_output.turn_id IS DISTINCT FROM p_turn_id)
       OR (v_output.reply_to_message_id IS NOT NULL
           AND v_output.reply_to_message_id IS DISTINCT FROM p_input_message_id)
       OR (v_input.turn_id IS NOT NULL AND v_input.turn_id IS DISTINCT FROM p_turn_id) THEN
        RAISE EXCEPTION 'TURN_MESSAGE_RELATION_MISMATCH' USING ERRCODE = '23505';
    END IF;

    IF v_task.input_message_id IS NOT NULL
       OR v_task.turn_id IS NOT NULL
       OR v_task.base_context_revision IS NOT NULL THEN
        IF v_task.input_message_id IS DISTINCT FROM p_input_message_id
           OR v_task.turn_id IS DISTINCT FROM p_turn_id
           OR v_task.execution_mode IS DISTINCT FROM p_execution_mode THEN
            RAISE EXCEPTION 'TURN_ALREADY_BOUND' USING ERRCODE = '23505';
        END IF;
    ELSE
        UPDATE messages SET turn_id = p_turn_id WHERE id = p_input_message_id;
        UPDATE messages
           SET turn_id = p_turn_id,
               reply_to_message_id = p_input_message_id
         WHERE id = v_task.assistant_message_id;
        UPDATE tasks
           SET input_message_id = p_input_message_id,
               turn_id = p_turn_id,
               base_context_revision = v_conversation.context_revision,
               context_through_message_id = v_conversation.last_closed_message_id,
               execution_mode = p_execution_mode
         WHERE id = p_task_id
         RETURNING * INTO v_task;
    END IF;

    RETURN jsonb_build_object(
        'turn_id', p_turn_id,
        'input_message_id', p_input_message_id,
        'base_context_revision', v_task.base_context_revision,
        'context_through_message_id', v_task.context_through_message_id,
        'execution_mode', p_execution_mode
    );
END;
$$;

CREATE OR REPLACE FUNCTION close_generation_turn(
    p_conversation_id UUID,
    p_task_id UUID,
    p_output_message_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_output messages%ROWTYPE;
    v_closed_revision BIGINT;
BEGIN
    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TURN_CONVERSATION_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.org_id IS DISTINCT FROM v_conversation.org_id
       OR v_task.input_message_id IS NULL
       OR v_task.turn_id IS NULL THEN
        RAISE EXCEPTION 'TURN_TASK_NOT_BOUND' USING ERRCODE = '55000';
    END IF;

    SELECT * INTO v_output FROM messages WHERE id = p_output_message_id FOR UPDATE;
    IF NOT FOUND
       OR v_output.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_output.org_id IS DISTINCT FROM v_conversation.org_id
       OR v_output.role::TEXT <> 'assistant'
       OR (v_task.assistant_message_id IS NOT NULL
           AND v_task.assistant_message_id IS DISTINCT FROM p_output_message_id)
       OR v_output.reply_to_message_id IS DISTINCT FROM v_task.input_message_id
       OR v_output.turn_id IS DISTINCT FROM v_task.turn_id THEN
        RAISE EXCEPTION 'TURN_OUTPUT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;

    IF v_output.context_revision IS NOT NULL THEN
        UPDATE tasks
           SET status = 'completed',
               completed_at = COALESCE(completed_at, NOW())
         WHERE id = p_task_id;
        RETURN jsonb_build_object(
            'turn_id', v_task.turn_id,
            'closed_revision', v_output.context_revision,
            'output_message_id', p_output_message_id,
            'already_closed', TRUE
        );
    END IF;

    v_closed_revision := v_conversation.context_revision + 1;
    UPDATE messages
       SET turn_id = v_task.turn_id,
           context_revision = v_closed_revision,
           message_kind = 'conversation'
     WHERE id = v_task.input_message_id
       AND conversation_id = p_conversation_id
       AND context_revision IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TURN_INPUT_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    UPDATE messages
       SET context_revision = v_closed_revision,
           message_kind = 'conversation'
     WHERE id = p_output_message_id;

    UPDATE tasks
       SET assistant_message_id = p_output_message_id,
           status = 'completed',
           completed_at = COALESCE(completed_at, NOW())
     WHERE id = p_task_id
       AND (assistant_message_id IS NULL OR assistant_message_id = p_output_message_id);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TURN_OUTPUT_ALREADY_BOUND' USING ERRCODE = '23505';
    END IF;

    UPDATE conversations
       SET context_revision = v_closed_revision,
           last_closed_message_id = p_output_message_id
     WHERE id = p_conversation_id;

    RETURN jsonb_build_object(
        'turn_id', v_task.turn_id,
        'closed_revision', v_closed_revision,
        'output_message_id', p_output_message_id,
        'already_closed', FALSE
    );
END;
$$;

REVOKE ALL ON FUNCTION bind_generation_turn(UUID, UUID, UUID, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION close_generation_turn(UUID, UUID, UUID) FROM PUBLIC;

COMMENT ON FUNCTION bind_generation_turn(UUID, UUID, UUID, UUID, TEXT)
    IS '原子绑定生成任务的输入、Turn 和上下文基线；完全相同的重复调用幂等';
COMMENT ON FUNCTION close_generation_turn(UUID, UUID, UUID)
    IS '原子关闭 Turn 并推进会话 revision；重复关闭返回首次 revision';
