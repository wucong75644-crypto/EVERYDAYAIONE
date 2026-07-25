-- 172: 非 Actor 视频 Worker 的原子终态能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_commit_video_terminal(
    p_external_task_id TEXT,
    p_expected_version INTEGER,
    p_status TEXT,
    p_content JSONB,
    p_error_code TEXT DEFAULT NULL,
    p_error_message TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_message public.messages%ROWTYPE;
    v_refund JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'VIDEO_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(BTRIM(p_external_task_id), '') IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 0
       OR p_status NOT IN ('completed', 'failed')
       OR p_content IS NULL OR jsonb_typeof(p_content) <> 'array'
       OR (p_status = 'failed'
           AND NULLIF(BTRIM(p_error_message), '') IS NULL) THEN
        RAISE EXCEPTION 'VIDEO_WORKER_TERMINAL_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type = 'video'
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF COALESCE((v_task.delivery_context ->> 'actor')::BOOLEAN, FALSE) THEN
        RAISE EXCEPTION 'VIDEO_WORKER_ACTOR_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status IN ('completed', 'failed', 'cancelled') THEN
        SELECT * INTO v_message
          FROM public.messages
         WHERE id = v_task.placeholder_message_id;
        RETURN jsonb_build_object(
            'outcome', 'already_terminal',
            'task', to_jsonb(v_task),
            'message', CASE WHEN FOUND THEN to_jsonb(v_message) ELSE NULL END
        );
    END IF;
    IF v_task.version <> p_expected_version THEN
        RETURN jsonb_build_object('outcome', 'version_conflict');
    END IF;

    SELECT * INTO v_message
      FROM public.messages
     WHERE id = v_task.placeholder_message_id
       AND conversation_id = v_task.conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'VIDEO_WORKER_MESSAGE_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;

    IF p_status = 'completed' THEN
        IF v_task.credit_transaction_id IS NOT NULL THEN
            UPDATE public.credit_transactions
               SET status = 'confirmed',
                   confirmed_at = NOW()
             WHERE id = v_task.credit_transaction_id
               AND status = 'pending';
        END IF;
        UPDATE public.messages
           SET content = p_content,
               status = 'completed',
               credits_cost = COALESCE(v_task.credits_locked, 0),
               is_error = FALSE,
               generation_params = COALESCE(generation_params, '{}'::JSONB)
                   || jsonb_build_object(
                       'type', 'video',
                       'model', COALESCE(v_task.model_id, 'unknown')
                   )
         WHERE id = v_message.id
         RETURNING * INTO v_message;

        IF v_task.turn_id IS NOT NULL AND v_task.input_message_id IS NOT NULL THEN
            PERFORM public.close_generation_turn(
                v_task.conversation_id, v_task.id, v_message.id
            );
        ELSE
            UPDATE public.tasks
               SET status = 'completed',
                   completed_at = NOW()
             WHERE id = v_task.id;
        END IF;
    ELSE
        IF v_task.credit_transaction_id IS NOT NULL THEN
            SELECT public.atomic_refund_credits(v_task.credit_transaction_id)
              INTO v_refund;
        END IF;
        UPDATE public.messages
           SET content = p_content,
               status = 'failed',
               credits_cost = 0,
               is_error = TRUE,
               generation_params = COALESCE(generation_params, '{}'::JSONB)
                   || jsonb_build_object(
                       'type', 'video',
                       'model', COALESCE(v_task.model_id, 'unknown')
                   )
         WHERE id = v_message.id
         RETURNING * INTO v_message;
        UPDATE public.tasks
           SET status = 'failed',
               fail_code = LEFT(COALESCE(p_error_code, 'UNKNOWN'), 50),
               error_message = p_error_message,
               completed_at = NOW()
         WHERE id = v_task.id;
    END IF;

    SELECT * INTO v_task FROM public.tasks WHERE id = v_task.id;
    RETURN jsonb_build_object(
        'outcome', CASE
            WHEN p_status = 'completed' THEN 'committed'
            ELSE 'failed'
        END,
        'task', to_jsonb(v_task),
        'message', to_jsonb(v_message),
        'refund', v_refund
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_commit_video_terminal(
    TEXT, INTEGER, TEXT, JSONB, TEXT, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_commit_video_terminal(
    TEXT, INTEGER, TEXT, JSONB, TEXT, TEXT
) TO everydayai_worker;

RESET ROLE;
