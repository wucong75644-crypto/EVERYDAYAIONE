-- 243: 修复生产 Conversation Actor 契约遗漏。
--
-- 239 的部分兼容 RPC 已存在于生产的早期迁移中，但以下两个新增函数没有
-- 随首次 Actor 发布落库。运行时代码已经依赖它们：
-- - mark_stale_tool_invocation_uncertain：执行副作用工具前的保守恢复闸门；
-- - cancel_paused_generation_turn：暂停任务的最终取消入口。
--
-- 仅 CREATE OR REPLACE 函数，不触碰现有业务数据、任务、消息或 invocation
-- 记录。事务保证两个入口作为一个契约一起生效。

BEGIN;

CREATE OR REPLACE FUNCTION public.cancel_paused_generation_turn(
    p_task_id UUID, p_user_id UUID, p_org_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_snapshot JSONB;
BEGIN
    IF p_task_id IS NULL OR p_user_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_PAUSED_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    SELECT * INTO v_conversation FROM conversations
     WHERE id = v_task.conversation_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.user_id IS DISTINCT FROM p_user_id
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_conversation.user_id IS DISTINCT FROM p_user_id
       OR v_conversation.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_PAUSED_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'already_cancelled', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'paused' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;

    v_snapshot := public.materialize_actor_cancel_snapshot(p_task_id);
    UPDATE tasks SET status = 'cancelled', error_message = '用户取消了任务',
        completed_at = NOW(), execution_token = NULL, lease_expires_at = NULL,
        terminal_reason = 'user_cancelled'
     WHERE id = p_task_id;
    UPDATE conversations SET active_serial_task_id = NULL, actor_updated_at = NOW()
     WHERE id = v_conversation.id AND active_serial_task_id = p_task_id;
    RETURN jsonb_build_object(
        'outcome', 'cancelled', 'task_id', p_task_id,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.mark_stale_tool_invocation_uncertain(
    p_task_id UUID,
    p_turn_id UUID,
    p_tool_call_id TEXT,
    p_execution_token UUID,
    p_stale_after_seconds INTEGER DEFAULT 900
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_invocation tool_invocations%ROWTYPE;
    v_updated_count BIGINT;
    v_threshold INTEGER := GREATEST(COALESCE(p_stale_after_seconds, 900), 1);
BEGIN
    IF p_task_id IS NULL OR p_turn_id IS NULL OR p_execution_token IS NULL
       OR NULLIF(BTRIM(p_tool_call_id), '') IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_STALE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_TOOL_INVOCATION_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    SELECT * INTO v_invocation FROM tool_invocations
     WHERE task_id = p_task_id AND turn_id = p_turn_id
       AND tool_call_id = BTRIM(p_tool_call_id) FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;
    IF v_invocation.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', v_invocation.status);
    END IF;
    IF v_invocation.updated_at >= NOW() - make_interval(secs => v_threshold) THEN
        RETURN jsonb_build_object('outcome', 'fresh');
    END IF;
    UPDATE tool_invocations SET status = 'uncertain',
        error_message = LEFT('外部工具调用超过恢复阈值，结果未知；禁止自动重试。', 2000),
        completed_at = NOW(), updated_at = NOW()
     WHERE id = v_invocation.id AND status = 'running';
    GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    IF v_updated_count = 0 THEN
        RETURN jsonb_build_object('outcome', 'already_completed');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'uncertain',
        'error_message', '外部工具调用超过恢复阈值，结果未知；禁止自动重试。'
    );
END;
$$;

COMMIT;
