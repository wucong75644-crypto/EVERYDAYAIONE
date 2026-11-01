-- Exact reviewed production control variants. Test fixture only; never deployed as a migration.
CREATE OR REPLACE FUNCTION public.cancel_generation_turn_owned(p_task_id uuid, p_execution_token uuid, p_reason text DEFAULT 'user_cancelled'::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_snapshot JSONB;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL
       OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'cancelled' THEN
        RETURN jsonb_build_object('outcome', 'already_cancelled', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = v_task.conversation_id
     FOR UPDATE;
    IF v_conversation.id IS NULL
       OR v_conversation.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ACTOR_CANCEL_OWNER_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    v_snapshot := materialize_actor_cancel_snapshot(p_task_id);

    UPDATE tasks
       SET status = 'cancelled',
           error_message = COALESCE(NULLIF(BTRIM(p_reason), ''), '用户取消了任务'),
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
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$function$
;
ALTER FUNCTION public.cancel_generation_turn_owned(uuid,uuid,text) OWNER TO everydayai;
REVOKE ALL ON FUNCTION public.cancel_generation_turn_owned(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.cancel_generation_turn_owned(uuid,uuid,text) TO everydayai;
CREATE OR REPLACE FUNCTION public.pause_generation_turn_owned(p_task_id uuid, p_execution_token uuid, p_reason text DEFAULT 'user_paused'::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
DECLARE
    v_task tasks%ROWTYPE;
    v_conversation conversations%ROWTYPE;
    v_snapshot JSONB;
    v_checkpoint conversation_turn_checkpoints%ROWTYPE;
    v_output_context_revision BIGINT;
    v_closed_revision BIGINT;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = v_task.conversation_id
     FOR UPDATE;
    IF NOT FOUND OR v_conversation.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_OWNER_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'paused' THEN
        RETURN jsonb_build_object('outcome', 'already_paused', 'task_id', p_task_id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT * INTO v_checkpoint
      FROM conversation_turn_checkpoints
     WHERE task_id = p_task_id AND status = 'ready'
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_PAUSE_CHECKPOINT_MISSING' USING ERRCODE = '55000';
    END IF;

    v_snapshot := materialize_actor_cancel_snapshot(p_task_id);

    -- 中断不是已完成回答，但它必须成为后续新 turn 可读取的历史。
    -- 同一 task 恢复时仍使用 checkpoint 中的固定消息快照；再次暂停时
    -- 发现已有 revision 后保持幂等，不重复推进会话 revision。
    SELECT context_revision INTO v_output_context_revision
      FROM messages
     WHERE id = v_task.assistant_message_id
     FOR UPDATE;
    IF v_output_context_revision IS NULL THEN
        v_closed_revision := v_conversation.context_revision + 1;
        UPDATE messages
           SET context_revision = v_closed_revision,
               message_kind = 'conversation'
         WHERE id = v_task.input_message_id
           AND conversation_id = v_task.conversation_id
           AND context_revision IS NULL;
        UPDATE messages
           SET context_revision = v_closed_revision,
               message_kind = 'conversation'
         WHERE id = v_task.assistant_message_id
           AND conversation_id = v_task.conversation_id
           AND context_revision IS NULL;
        UPDATE conversations
           SET context_revision = v_closed_revision,
               last_closed_message_id = v_task.assistant_message_id,
               actor_updated_at = NOW()
         WHERE id = v_conversation.id;
    ELSE
        v_closed_revision := v_output_context_revision;
    END IF;

    UPDATE conversation_turn_checkpoints
       SET status = 'paused', updated_at = NOW()
     WHERE task_id = p_task_id AND version = v_checkpoint.version;
    UPDATE conversation_control_events
       SET status = 'applied', applied_execution_token = p_execution_token,
           applied_at = NOW()
     WHERE task_id = p_task_id
       AND dedupe_key = 'pause:' || p_task_id::TEXT
       AND status = 'pending';

    UPDATE tasks
       SET status = 'paused',
           error_message = COALESCE(NULLIF(BTRIM(p_reason), ''), '用户暂停了任务'),
           execution_token = NULL,
           lease_expires_at = NULL,
           terminal_reason = 'user_paused'
     WHERE id = p_task_id;
    UPDATE conversations
       SET active_serial_task_id = NULL, actor_updated_at = NOW()
     WHERE id = v_task.conversation_id
       AND active_serial_task_id = p_task_id;

    RETURN jsonb_build_object(
        'outcome', 'paused', 'task_id', p_task_id,
        'checkpoint_version', v_checkpoint.version,
        'closed_revision', v_closed_revision,
        'snapshot_saved', COALESCE((v_snapshot->>'saved')::BOOLEAN, FALSE)
    );
END;
$function$
;
ALTER FUNCTION public.pause_generation_turn_owned(uuid,uuid,text) OWNER TO everydayai;
REVOKE ALL ON FUNCTION public.pause_generation_turn_owned(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pause_generation_turn_owned(uuid,uuid,text) TO everydayai;
