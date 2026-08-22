-- 回滚 141：恢复 122 版 cancel_generation_turn。
-- 该回滚只撤销“取消时保存快照”的函数升级，不删除任务或消息数据。

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

REVOKE ALL ON FUNCTION cancel_generation_turn(UUID, UUID, UUID) FROM PUBLIC;

COMMENT ON FUNCTION cancel_generation_turn(UUID, UUID, UUID)
    IS '用户范围校验后立即取消 pending/running Chat task，并使旧 execution token 失效';
