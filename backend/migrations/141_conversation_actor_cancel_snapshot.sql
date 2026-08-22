-- 141: Conversation Actor 取消时原子保存部分生成快照
-- 依赖 120_turn_revision_foundation.sql、121_conversation_actor_queue.sql、
-- 108_tasks_accumulated_blocks.sql 与 122_conversation_actor_terminal.sql。
--
-- 本迁移只升级 cancel_generation_turn：
-- 1. 在同一事务内先锁定 conversation/task，再锁定助手消息；
-- 2. 将 fencing 进度合并为可回放的 content，并追加 interrupt_marker；
-- 3. 再把 task 置为 cancelled、清理执行权。
--
-- 这不是 token 级原地续流，也不是 pause/resume 状态；它保证取消后已有进度
-- 不会只停留在 tasks 临时字段里，后续“继续”可以从中断历史重新开启新 turn。

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
    v_output messages%ROWTYPE;
    v_conversation_id UUID;
    v_snapshot JSONB := '[]'::JSONB;
    v_blocks_text TEXT := '';
    v_remaining_text TEXT := '';
    v_now_iso TEXT := to_char(clock_timestamp() AT TIME ZONE 'UTC',
                              'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
    v_message_updated INTEGER := 0;
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    -- 与 122 保持一致：conversation -> task，避免和其他 Actor RPC 形成反向锁。
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
        RETURN jsonb_build_object(
            'outcome', 'already_cancelled',
            'task_id', p_task_id,
            'snapshot_saved', FALSE
        );
    END IF;
    IF v_task.status NOT IN ('pending', 'running') THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;

    -- 消息锁位于 task 锁之后。缺少消息时仍允许取消，但会明确返回
    -- snapshot_saved=false，避免取消入口被一个已损坏的占位消息阻塞。
    IF v_task.assistant_message_id IS NOT NULL THEN
        SELECT * INTO v_output FROM messages
         WHERE id = v_task.assistant_message_id FOR UPDATE;
    END IF;

    -- 优先使用 fencing 进度；没有进度时保留已有助手消息内容，避免把非空内容
    -- 覆盖成空数组。兼容旧任务只有 accumulated_content 的情况。
    IF jsonb_typeof(v_task.accumulated_blocks) = 'array'
       AND jsonb_array_length(v_task.accumulated_blocks) > 0 THEN
        v_snapshot := v_task.accumulated_blocks;
    ELSIF v_task.accumulated_content IS NOT NULL
          AND BTRIM(v_task.accumulated_content) <> '' THEN
        v_snapshot := jsonb_build_array(
            jsonb_build_object('type', 'text', 'text', v_task.accumulated_content)
        );
    ELSIF v_output.id IS NOT NULL
          AND jsonb_typeof(v_output.content) = 'array'
          AND jsonb_array_length(v_output.content) > 0 THEN
        v_snapshot := v_output.content;
    END IF;

    -- 与 services.task_utils.merge_blocks_with_text 保持同一规则：
    -- blocks 中已提交的 text 是前缀，task 累积全文中的剩余部分追加为新 text block。
    IF jsonb_typeof(v_task.accumulated_blocks) = 'array'
       AND jsonb_array_length(v_task.accumulated_blocks) > 0
       AND v_task.accumulated_content IS NOT NULL THEN
        SELECT COALESCE(string_agg(item.value->>'text', '' ORDER BY item.ordinality), '')
          INTO v_blocks_text
          FROM jsonb_array_elements(v_snapshot) WITH ORDINALITY AS item(value, ordinality)
         WHERE item.value->>'type' = 'text';
        IF LEFT(v_task.accumulated_content, LENGTH(v_blocks_text)) = v_blocks_text
           AND LENGTH(v_task.accumulated_content) > LENGTH(v_blocks_text) THEN
            v_remaining_text := SUBSTRING(v_task.accumulated_content
                                          FROM LENGTH(v_blocks_text) + 1);
            IF BTRIM(v_remaining_text) <> '' THEN
                v_snapshot := v_snapshot || jsonb_build_array(
                    jsonb_build_object('type', 'text', 'text', v_remaining_text)
                );
            END IF;
        END IF;
    END IF;

    -- 取消时将仍处于 running 的工具步骤显式标记为 cancelled，避免恢复历史
    -- 把一个已经被用户终止的工具误判为仍在执行。
    SELECT COALESCE(jsonb_agg(
        CASE
            WHEN item.value->>'type' = 'tool_step'
             AND item.value->>'status' = 'running'
            THEN item.value || jsonb_build_object(
                'status', 'cancelled', 'cancelled_at', v_now_iso
            )
            ELSE item.value
        END
        ORDER BY item.ordinality
    ), '[]'::JSONB)
      INTO v_snapshot
      FROM jsonb_array_elements(v_snapshot) WITH ORDINALITY AS item(value, ordinality);

    IF NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_snapshot) AS item(value)
         WHERE item.value->>'type' = 'interrupt_marker'
    ) THEN
        v_snapshot := v_snapshot || jsonb_build_array(
            jsonb_build_object(
                'type', 'interrupt_marker',
                'interrupted_at', v_now_iso,
                'reason', 'user_cancel'
            )
        );
    END IF;

    -- 主事实（messages）与任务终态在同一 RPC 事务中更新。
    IF v_output.id IS NOT NULL THEN
        UPDATE messages
           SET content = v_snapshot,
               status = 'interrupted'
         WHERE id = v_output.id
           AND status IS DISTINCT FROM 'completed';
        GET DIAGNOSTICS v_message_updated = ROW_COUNT;
    END IF;

    UPDATE tasks
       SET status = 'cancelled',
           error_message = '用户取消了任务',
           completed_at = NOW(),
           execution_token = NULL,
           lease_expires_at = NULL,
           terminal_reason = 'user_cancelled'
     WHERE id = p_task_id;
    UPDATE conversations
       SET active_serial_task_id = NULL,
           actor_updated_at = NOW()
     WHERE id = v_conversation.id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object(
        'outcome', 'cancelled',
        'task_id', p_task_id,
        'snapshot_saved', v_message_updated = 1,
        'snapshot_blocks', jsonb_array_length(v_snapshot)
    );
END;
$$;

REVOKE ALL ON FUNCTION cancel_generation_turn(UUID, UUID, UUID) FROM PUBLIC;

COMMENT ON FUNCTION cancel_generation_turn(UUID, UUID, UUID)
    IS '用户范围校验后，在同一事务中保存 Actor 部分生成快照、追加中断标记并取消 Chat task';
