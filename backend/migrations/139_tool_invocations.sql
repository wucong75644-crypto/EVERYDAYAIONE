-- Conversation Actor 副作用工具幂等记录。
-- 只允许当前 fencing owner 登记/完成；不依赖 Redis 作为事实来源。

CREATE TABLE IF NOT EXISTS tool_invocations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id UUID NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    result JSONB,
    error_message TEXT NOT NULL DEFAULT '',
    execution_token UUID,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tool_invocations_status_check
        CHECK (status IN ('running', 'succeeded', 'uncertain')),
    CONSTRAINT tool_invocations_tool_call_check
        CHECK (length(BTRIM(tool_call_id)) BETWEEN 1 AND 200),
    CONSTRAINT tool_invocations_tool_name_check
        CHECK (length(BTRIM(tool_name)) BETWEEN 1 AND 200),
    CONSTRAINT tool_invocations_args_hash_check
        CHECK (args_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT tool_invocations_result_check
        CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
    CONSTRAINT tool_invocations_task_turn_call_unique
        UNIQUE (task_id, turn_id, tool_call_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_invocations_task_status
    ON tool_invocations(task_id, status, updated_at);

CREATE OR REPLACE FUNCTION begin_tool_invocation(
    p_task_id UUID,
    p_conversation_id UUID,
    p_turn_id UUID,
    p_execution_token UUID,
    p_tool_call_id TEXT,
    p_tool_name TEXT,
    p_args_hash TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_invocation tool_invocations%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR p_conversation_id IS NULL OR p_turn_id IS NULL
       OR p_execution_token IS NULL
       OR NULLIF(BTRIM(p_tool_call_id), '') IS NULL
       OR NULLIF(BTRIM(p_tool_name), '') IS NULL
       OR p_args_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_invocation
      FROM tool_invocations
     WHERE task_id = p_task_id
       AND turn_id = p_turn_id
       AND tool_call_id = BTRIM(p_tool_call_id)
     FOR UPDATE;

    IF FOUND THEN
        IF v_invocation.tool_name IS DISTINCT FROM BTRIM(p_tool_name)
           OR v_invocation.args_hash IS DISTINCT FROM p_args_hash
           OR v_invocation.conversation_id IS DISTINCT FROM p_conversation_id THEN
            RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_REUSE_MISMATCH'
                USING ERRCODE = '42501';
        END IF;
        IF v_invocation.status = 'succeeded' THEN
            RETURN jsonb_build_object(
                'outcome', 'replay', 'result', v_invocation.result
            );
        END IF;
        IF v_invocation.status = 'uncertain' THEN
            RETURN jsonb_build_object(
                'outcome', 'uncertain', 'error_message', v_invocation.error_message
            );
        END IF;
        RETURN jsonb_build_object('outcome', 'in_progress');
    END IF;

    INSERT INTO tool_invocations(
        task_id, conversation_id, turn_id, tool_call_id,
        tool_name, args_hash, status, execution_token
    ) VALUES (
        p_task_id, p_conversation_id, p_turn_id, BTRIM(p_tool_call_id),
        BTRIM(p_tool_name), p_args_hash, 'running', p_execution_token
    );
    RETURN jsonb_build_object('outcome', 'execute');
END;
$$;

CREATE OR REPLACE FUNCTION complete_tool_invocation(
    p_task_id UUID,
    p_turn_id UUID,
    p_tool_call_id TEXT,
    p_execution_token UUID,
    p_status TEXT,
    p_result JSONB,
    p_error_message TEXT DEFAULT ''
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_updated_count BIGINT;
BEGIN
    IF p_task_id IS NULL OR p_turn_id IS NULL OR p_execution_token IS NULL
       OR NULLIF(BTRIM(p_tool_call_id), '') IS NULL
       OR p_status NOT IN ('succeeded', 'uncertain')
       OR p_result IS NULL OR jsonb_typeof(p_result) <> 'object' THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_COMPLETE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    UPDATE tool_invocations
       SET status = p_status,
           result = p_result,
           error_message = LEFT(COALESCE(p_error_message, ''), 2000),
           execution_token = p_execution_token,
           completed_at = NOW(),
           updated_at = NOW()
     WHERE task_id = p_task_id
       AND turn_id = p_turn_id
       AND tool_call_id = BTRIM(p_tool_call_id)
       AND status = 'running';
    GET DIAGNOSTICS v_updated_count = ROW_COUNT;

    IF v_updated_count = 0 THEN
        RETURN jsonb_build_object('outcome', 'already_completed');
    END IF;
    RETURN jsonb_build_object('outcome', p_status);
END;
$$;

REVOKE ALL ON FUNCTION begin_tool_invocation(UUID, UUID, UUID, UUID, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION complete_tool_invocation(UUID, UUID, TEXT, UUID, TEXT, JSONB, TEXT) FROM PUBLIC;

COMMENT ON TABLE tool_invocations IS
    'Conversation Actor 工具调用幂等事实；uncertain 表示外部副作用结果未知，禁止盲目重试';
