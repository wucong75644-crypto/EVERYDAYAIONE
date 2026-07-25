-- 179: 定时任务 run fencing token、租约与原子结果消息能力。

SET LOCAL ROLE everydayai_owner;

ALTER TABLE public.scheduled_task_runs
    ADD COLUMN execution_token UUID,
    ADD COLUMN lease_expires_at TIMESTAMPTZ,
    ADD COLUMN result_message_id UUID;

CREATE FUNCTION clear_scheduled_run_fence_on_terminal()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_transaction RECORD;
    v_refund JSONB;
BEGIN
    IF OLD.status = 'running' AND NEW.status <> 'running' THEN
        FOR v_transaction IN
            SELECT id
              FROM public.credit_transactions
             WHERE task_id = NEW.id
               AND status = 'pending'
             FOR UPDATE
        LOOP
            SELECT public.atomic_refund_credits(v_transaction.id)
              INTO v_refund;
        END LOOP;
        NEW.execution_token := NULL;
        NEW.lease_expires_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_clear_scheduled_run_fence_on_terminal
BEFORE UPDATE OF status ON public.scheduled_task_runs
FOR EACH ROW
EXECUTE FUNCTION clear_scheduled_run_fence_on_terminal();

CREATE FUNCTION _assert_scheduled_run_scope(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_task_id IS NULL OR p_run_id IS NULL OR p_execution_token IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_RUN_SCOPE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM 1
      FROM public.scheduled_task_runs run
      JOIN public.scheduled_tasks task ON task.id = run.task_id
     WHERE task.id = p_task_id
       AND run.id = p_run_id
       AND run.org_id = task.org_id
       AND task.status = 'running'
       AND run.status = 'running'
       AND run.execution_token IS NOT DISTINCT FROM p_execution_token
       AND run.lease_expires_at > clock_timestamp()
     FOR UPDATE OF run, task;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SCHEDULED_RUN_OWNERSHIP_LOST'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE FUNCTION worker_create_scheduled_run(
    p_task_id UUID,
    p_lease_seconds INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
    v_run public.scheduled_task_runs%ROWTYPE;
    v_token UUID := gen_random_uuid();
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_task_id IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'SCHEDULED_RUN_CREATE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task
      FROM public.scheduled_tasks
     WHERE id = p_task_id AND status = 'running'
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_running');
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.scheduled_task_runs
         WHERE task_id = p_task_id AND status = 'running'
    ) THEN
        RETURN jsonb_build_object('outcome', 'already_running');
    END IF;
    INSERT INTO public.scheduled_task_runs(
        task_id, org_id, status, execution_token, lease_expires_at
    ) VALUES (
        v_task.id, v_task.org_id, 'running', v_token,
        clock_timestamp() + make_interval(secs => p_lease_seconds)
    )
    RETURNING * INTO v_run;
    RETURN jsonb_build_object(
        'outcome', 'created',
        'run', to_jsonb(v_run) - 'execution_token',
        'execution_token', v_token
    );
END;
$$;

CREATE FUNCTION worker_renew_scheduled_run(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID,
    p_lease_seconds INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'SCHEDULED_RUN_RENEW_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM public._assert_scheduled_run_scope(
        p_task_id, p_run_id, p_execution_token
    );
    UPDATE public.scheduled_task_runs
       SET lease_expires_at =
           clock_timestamp() + make_interval(secs => p_lease_seconds)
     WHERE id = p_run_id;
    RETURN jsonb_build_object('outcome', 'renewed');
END;
$$;

CREATE FUNCTION worker_get_scheduled_task(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
BEGIN
    PERFORM public._assert_scheduled_run_scope(
        p_task_id, p_run_id, p_execution_token
    );
    SELECT run.result_message_id INTO v_message_id
      FROM public.scheduled_task_runs run
     WHERE run.id = p_run_id;
    IF v_message_id IS NOT NULL THEN
        SELECT message.conversation_id INTO v_conversation_id
          FROM public.messages message
         WHERE message.id = v_message_id;
        RETURN jsonb_build_object(
            'outcome', 'already_stored',
            'conversation_id', v_conversation_id,
            'message_id', v_message_id
        );
    END IF;
    SELECT * INTO v_task FROM public.scheduled_tasks WHERE id = p_task_id;
    RETURN to_jsonb(v_task);
END;
$$;

CREATE FUNCTION worker_append_scheduled_result_message(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID,
    p_text TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
    v_conversation_id UUID;
    v_message_id UUID;
BEGIN
    IF NULLIF(BTRIM(p_text), '') IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_RESULT_MESSAGE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM public._assert_scheduled_run_scope(
        p_task_id, p_run_id, p_execution_token
    );
    SELECT * INTO v_task FROM public.scheduled_tasks WHERE id = p_task_id;
    SELECT id INTO v_conversation_id
      FROM public.conversations
     WHERE user_id = v_task.user_id
       AND org_id = v_task.org_id
       AND source = 'wecom'
     ORDER BY updated_at DESC
     LIMIT 1
     FOR UPDATE;
    IF v_conversation_id IS NULL THEN
        INSERT INTO public.conversations(
            user_id, title, message_count, credits_consumed,
            org_id, source, model_id
        ) VALUES (
            v_task.user_id, '企微对话', 0, 0,
            v_task.org_id, 'wecom', 'auto'
        )
        RETURNING id INTO v_conversation_id;
    END IF;
    INSERT INTO public.messages(
        conversation_id, role, content, status, org_id
    ) VALUES (
        v_conversation_id, 'assistant',
        jsonb_build_array(
            jsonb_build_object('type', 'text', 'text', p_text)
        )::TEXT,
        'completed', v_task.org_id
    )
    RETURNING id INTO v_message_id;
    UPDATE public.scheduled_task_runs
       SET result_message_id = v_message_id
     WHERE id = p_run_id;
    UPDATE public.conversations
       SET last_message_preview = LEFT(p_text, 50),
           message_count = COALESCE(message_count, 0) + 1,
           updated_at = NOW()
     WHERE id = v_conversation_id;
    RETURN jsonb_build_object(
        'outcome', 'stored',
        'conversation_id', v_conversation_id,
        'message_id', v_message_id
    );
END;
$$;

CREATE FUNCTION worker_complete_scheduled_run(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID,
    p_next_status TEXT,
    p_next_run_at TIMESTAMPTZ,
    p_summary TEXT,
    p_result JSONB,
    p_files JSONB,
    p_push_status TEXT,
    p_credits_used INTEGER,
    p_tokens_used INTEGER,
    p_duration_ms INTEGER,
    p_now TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_scheduled_run_scope(
        p_task_id, p_run_id, p_execution_token
    );
    SELECT public.worker_complete_scheduled_run(
        p_task_id, p_run_id, p_next_status, p_next_run_at, p_summary,
        p_result, p_files, p_push_status, p_credits_used, p_tokens_used,
        p_duration_ms, p_now
    ) INTO v_result;
    IF v_result->>'outcome' = 'completed' THEN
        UPDATE public.scheduled_task_runs
           SET execution_token = NULL, lease_expires_at = NULL
         WHERE id = p_run_id;
    END IF;
    RETURN v_result;
END;
$$;

CREATE FUNCTION worker_fail_scheduled_run(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID,
    p_next_status TEXT,
    p_next_run_at TIMESTAMPTZ,
    p_consecutive_failures INTEGER,
    p_error_message TEXT,
    p_tokens_used INTEGER,
    p_duration_ms INTEGER,
    p_now TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_scheduled_run_scope(
        p_task_id, p_run_id, p_execution_token
    );
    SELECT public.worker_fail_scheduled_run(
        p_task_id, p_run_id, p_next_status, p_next_run_at,
        p_consecutive_failures, p_error_message, p_tokens_used,
        p_duration_ms, p_now
    ) INTO v_result;
    IF v_result->>'outcome' = 'failed' THEN
        UPDATE public.scheduled_task_runs
           SET execution_token = NULL, lease_expires_at = NULL
         WHERE id = p_run_id;
    END IF;
    RETURN v_result;
END;
$$;

CREATE FUNCTION worker_lock_scheduled_credits(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_scheduled_run_scope(
        p_task_id, p_run_id, p_execution_token
    );
    SELECT public.worker_lock_scheduled_credits(
        p_task_id, p_run_id
    ) INTO v_result;
    RETURN v_result;
END;
$$;

CREATE FUNCTION worker_settle_scheduled_credits(
    p_task_id UUID,
    p_run_id UUID,
    p_execution_token UUID,
    p_transaction_id UUID,
    p_success BOOLEAN,
    p_actual_amount INTEGER DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_scheduled_run_scope(
        p_task_id, p_run_id, p_execution_token
    );
    SELECT public.worker_settle_scheduled_credits(
        p_task_id, p_run_id, p_transaction_id, p_success, p_actual_amount
    ) INTO v_result;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION _assert_scheduled_run_scope(UUID, UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION clear_scheduled_run_fence_on_terminal()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION worker_create_scheduled_run(UUID),
    worker_get_scheduled_task(UUID),
    worker_complete_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB, TEXT,
        INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_fail_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT, INTEGER,
        INTEGER, TIMESTAMPTZ
    ),
    worker_lock_scheduled_credits(UUID, UUID),
    worker_settle_scheduled_credits(UUID, UUID, UUID, BOOLEAN, INTEGER)
FROM everydayai_worker;

REVOKE ALL ON FUNCTION worker_create_scheduled_run(UUID, INTEGER),
    worker_renew_scheduled_run(UUID, UUID, UUID, INTEGER),
    worker_get_scheduled_task(UUID, UUID, UUID),
    worker_append_scheduled_result_message(UUID, UUID, UUID, TEXT),
    worker_complete_scheduled_run(
        UUID, UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB,
        TEXT, INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_fail_scheduled_run(
        UUID, UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT,
        INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_lock_scheduled_credits(UUID, UUID, UUID),
    worker_settle_scheduled_credits(
        UUID, UUID, UUID, UUID, BOOLEAN, INTEGER
    )
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_create_scheduled_run(UUID, INTEGER),
    worker_renew_scheduled_run(UUID, UUID, UUID, INTEGER),
    worker_get_scheduled_task(UUID, UUID, UUID),
    worker_append_scheduled_result_message(UUID, UUID, UUID, TEXT),
    worker_complete_scheduled_run(
        UUID, UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB,
        TEXT, INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_fail_scheduled_run(
        UUID, UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT,
        INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_lock_scheduled_credits(UUID, UUID, UUID),
    worker_settle_scheduled_credits(
        UUID, UUID, UUID, UUID, BOOLEAN, INTEGER
    )
TO everydayai_worker;

RESET ROLE;
