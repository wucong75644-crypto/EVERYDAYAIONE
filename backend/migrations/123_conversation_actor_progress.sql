-- 123: Conversation Actor fencing 进度协议
-- 依赖 121_conversation_actor_queue.sql。
-- 仅当前 running task 的有效 execution_token 可以写临时进度。

CREATE OR REPLACE FUNCTION update_generation_progress(
    p_task_id UUID,
    p_execution_token UUID,
    p_accumulated_content TEXT,
    p_accumulated_blocks JSONB DEFAULT '[]'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
BEGIN
    IF p_task_id IS NULL
       OR p_execution_token IS NULL
       OR p_accumulated_content IS NULL
       OR p_accumulated_blocks IS NULL
       OR jsonb_typeof(p_accumulated_blocks) <> 'array' THEN
        RAISE EXCEPTION 'ACTOR_PROGRESS_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL OR v_task.type <> 'chat' THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object(
            'outcome', 'terminal', 'status', v_task.status
        );
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_task.lease_expires_at IS NULL OR v_task.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;

    UPDATE tasks
       SET accumulated_content = p_accumulated_content,
           accumulated_blocks = p_accumulated_blocks
     WHERE id = p_task_id;

    RETURN jsonb_build_object('outcome', 'updated', 'task_id', p_task_id);
END;
$$;

REVOKE ALL ON FUNCTION update_generation_progress(UUID, UUID, TEXT, JSONB)
FROM PUBLIC;

COMMENT ON FUNCTION update_generation_progress(UUID, UUID, TEXT, JSONB)
    IS '仅当前 fencing token 在有效租约内写 Chat 临时进度，不改变消息或任务终态';
