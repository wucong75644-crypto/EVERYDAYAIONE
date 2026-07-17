-- 122: Conversation Actor 原子终态协议
-- 依赖 120_turn_revision_foundation.sql 与 121_conversation_actor_queue.sql。
-- 本迁移只增加 commit/fail/cancel RPC，不切换现有业务链路。

CREATE OR REPLACE FUNCTION commit_generation_turn(
    p_task_id UUID, p_execution_token UUID, p_output_message_id UUID,
    p_result_content JSONB, p_usage JSONB DEFAULT '{}'::JSONB,
    p_credits_cost INTEGER DEFAULT 0, p_tool_digest JSONB DEFAULT NULL
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
    v_credit_result JSONB;
    v_close_result JSONB;
    v_conversation_id UUID;
BEGIN
    IF p_execution_token IS NULL
       OR p_output_message_id IS NULL
       OR p_result_content IS NULL
       OR jsonb_typeof(p_result_content) <> 'array'
       OR p_usage IS NULL
       OR jsonb_typeof(p_usage) <> 'object'
       OR p_credits_cost < 0
       OR (p_tool_digest IS NOT NULL AND jsonb_typeof(p_tool_digest) <> 'object') THEN
        RAISE EXCEPTION 'ACTOR_COMMIT_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT conversation_id INTO v_conversation_id FROM tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;

    IF v_task.id IS NULL OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_task.org_id IS DISTINCT FROM v_conversation.org_id
       OR v_task.assistant_message_id IS DISTINCT FROM p_output_message_id
       OR v_task.input_message_id IS NULL
       OR v_task.turn_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_COMMIT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'completed' THEN
        IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
            RETURN jsonb_build_object('outcome', 'ownership_lost');
        END IF;
        SELECT * INTO v_output FROM messages WHERE id = p_output_message_id;
        RETURN jsonb_build_object(
            'outcome', 'already_committed', 'task_id', v_task.id,
            'closed_revision', v_output.context_revision, 'credits_cost', v_task.credits_used
        );
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_task.lease_expires_at IS NULL OR v_task.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;

    SELECT * INTO v_output FROM messages WHERE id = p_output_message_id FOR UPDATE;
    IF NOT FOUND
       OR v_output.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_output.org_id IS DISTINCT FROM v_task.org_id
       OR v_output.role::TEXT <> 'assistant'
       OR v_output.reply_to_message_id IS DISTINCT FROM v_task.input_message_id
       OR v_output.turn_id IS DISTINCT FROM v_task.turn_id THEN
        RAISE EXCEPTION 'ACTOR_COMMIT_OUTPUT_MISMATCH' USING ERRCODE = '42501';
    END IF;

    IF p_credits_cost > 0 THEN
        SELECT deduct_credits_atomic(
            v_task.user_id, p_credits_cost,
            'Chat: ' || COALESCE(v_task.model_id, 'unknown'), 'conversation_cost', v_task.org_id
        ) INTO v_credit_result;
        IF COALESCE((v_credit_result->>'success')::BOOLEAN, FALSE) IS NOT TRUE THEN
            RAISE EXCEPTION 'ACTOR_COMMIT_INSUFFICIENT_CREDITS' USING ERRCODE = 'P0001';
        END IF;
    END IF;

    UPDATE messages
       SET content = p_result_content,
           status = 'completed',
           credits_cost = p_credits_cost,
           is_error = FALSE,
           generation_params = COALESCE(generation_params, '{}'::JSONB)
               || jsonb_build_object(
                    'type', 'chat', 'model', COALESCE(v_task.model_id, 'unknown'),
                    'usage', p_usage, 'tool_digest', p_tool_digest
               )
     WHERE id = p_output_message_id;

    SELECT close_generation_turn(v_conversation.id, v_task.id, p_output_message_id)
      INTO v_close_result;

    UPDATE tasks
       SET credits_used = p_credits_cost,
           total_credits = p_credits_cost,
           result = jsonb_build_object('usage', p_usage, 'tool_digest', p_tool_digest),
           lease_expires_at = NULL,
           terminal_reason = NULL
     WHERE id = p_task_id;
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object(
        'outcome', 'committed', 'task_id', p_task_id,
        'closed_revision', v_close_result->'closed_revision',
        'credits_cost', p_credits_cost
    );
END;
$$;

CREATE OR REPLACE FUNCTION fail_generation_turn(
    p_task_id UUID,
    p_execution_token UUID,
    p_error_code TEXT,
    p_error_message TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_conversation_id UUID;
BEGIN
    IF p_execution_token IS NULL
       OR NULLIF(BTRIM(p_error_code), '') IS NULL
       OR NULLIF(BTRIM(p_error_message), '') IS NULL THEN
        RAISE EXCEPTION 'ACTOR_FAIL_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT conversation_id INTO v_conversation_id FROM tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;

    IF v_task.id IS NULL OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_task.org_id IS DISTINCT FROM v_conversation.org_id THEN
        RAISE EXCEPTION 'ACTOR_FAIL_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'failed'
       AND v_task.execution_token IS NOT DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'already_failed', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    UPDATE tasks
       SET status = 'failed',
           fail_code = LEFT(p_error_code, 50),
           error_message = p_error_message,
           completed_at = NOW(),
           lease_expires_at = NULL,
           terminal_reason = 'execution_failed'
     WHERE id = p_task_id;
    UPDATE messages
       SET status = 'failed',
           is_error = TRUE
     WHERE id = v_task.assistant_message_id;
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object('outcome', 'failed', 'task_id', p_task_id);
END;
$$;

CREATE OR REPLACE FUNCTION cancel_generation_turn(
    p_task_id UUID,
    p_user_id UUID,
    p_org_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_conversation_id UUID;
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT conversation_id INTO v_conversation_id FROM tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_conversation_id FOR UPDATE;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;

    IF v_task.id IS NULL OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.conversation_id IS DISTINCT FROM v_conversation.id
       OR v_task.user_id IS DISTINCT FROM p_user_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.user_id IS DISTINCT FROM p_user_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'already_cancelled', 'task_id', p_task_id);
    END IF;
    IF v_task.status NOT IN ('pending', 'running') THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;

    UPDATE tasks
       SET status = 'cancelled',
           error_message = '用户取消了任务',
           completed_at = NOW(),
           execution_token = NULL,
           lease_expires_at = NULL,
           terminal_reason = 'user_cancelled'
     WHERE id = p_task_id;
    UPDATE messages
       SET status = 'interrupted'
     WHERE id = v_task.assistant_message_id
       AND status IS DISTINCT FROM 'completed';
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object('outcome', 'cancelled', 'task_id', p_task_id);
END;
$$;

REVOKE ALL ON FUNCTION commit_generation_turn(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB
) FROM PUBLIC;
REVOKE ALL ON FUNCTION fail_generation_turn(UUID, UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION cancel_generation_turn(UUID, UUID, UUID) FROM PUBLIC;

COMMENT ON FUNCTION commit_generation_turn(UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB)
    IS '当前 fencing token 在有效租约内原子提交 Chat 消息、积分、Turn revision 和 task 终态';
COMMENT ON FUNCTION fail_generation_turn(UUID, UUID, TEXT, TEXT)
    IS '仅当前 fencing token 可原子终止 running Chat task 并释放 serial owner';
COMMENT ON FUNCTION cancel_generation_turn(UUID, UUID, UUID)
    IS '用户范围校验后立即取消 pending/running Chat task，并使旧 execution token 失效';
