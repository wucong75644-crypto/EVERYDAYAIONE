-- 177: Worker 定时任务执行记录与终态能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_create_scheduled_run(p_task_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
    v_run public.scheduled_task_runs%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task
      FROM public.scheduled_tasks
     WHERE id = p_task_id AND status = 'running'
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_running');
    END IF;
    INSERT INTO public.scheduled_task_runs(task_id, org_id, status)
    VALUES (v_task.id, v_task.org_id, 'running')
    RETURNING * INTO v_run;
    RETURN jsonb_build_object('outcome', 'created', 'run', to_jsonb(v_run));
END;
$$;

CREATE FUNCTION worker_get_scheduled_task(p_task_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task FROM public.scheduled_tasks WHERE id = p_task_id;
    RETURN CASE WHEN FOUND THEN to_jsonb(v_task) ELSE NULL END;
END;
$$;

CREATE FUNCTION worker_complete_scheduled_run(
    p_task_id UUID,
    p_run_id UUID,
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
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_next_status NOT IN ('active', 'paused')
       OR p_result IS NULL OR jsonb_typeof(p_result) <> 'object'
       OR p_files IS NULL OR jsonb_typeof(p_files) <> 'array'
       OR p_credits_used < 0 OR p_tokens_used < 0 OR p_duration_ms < 0
       OR p_now IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_COMPLETE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM 1
      FROM public.scheduled_task_runs run
      JOIN public.scheduled_tasks task ON task.id = run.task_id
     WHERE run.id = p_run_id
       AND task.id = p_task_id
       AND run.org_id = task.org_id
       AND run.status = 'running'
       AND task.status = 'running'
     FOR UPDATE OF run, task;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    UPDATE public.scheduled_tasks
       SET status = p_next_status,
           next_run_at = p_next_run_at,
           last_run_at = p_now,
           last_summary = p_summary,
           last_result = p_result,
           run_count = run_count + 1,
           consecutive_failures = 0,
           updated_at = p_now
     WHERE id = p_task_id;
    UPDATE public.scheduled_task_runs
       SET status = 'success',
           result_summary = p_summary,
           result_files = p_files,
           push_status = p_push_status,
           credits_used = p_credits_used,
           tokens_used = p_tokens_used,
           duration_ms = p_duration_ms,
           finished_at = p_now
     WHERE id = p_run_id;
    RETURN jsonb_build_object('outcome', 'completed');
END;
$$;

CREATE FUNCTION worker_fail_scheduled_run(
    p_task_id UUID,
    p_run_id UUID,
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
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_next_status NOT IN ('active', 'paused', 'error')
       OR p_consecutive_failures < 1 OR p_tokens_used < 0
       OR p_duration_ms < 0 OR p_now IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_FAIL_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM 1
      FROM public.scheduled_task_runs run
      JOIN public.scheduled_tasks task ON task.id = run.task_id
     WHERE run.id = p_run_id
       AND task.id = p_task_id
       AND run.org_id = task.org_id
       AND run.status = 'running'
       AND task.status = 'running'
     FOR UPDATE OF run, task;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    UPDATE public.scheduled_tasks
       SET status = p_next_status,
           next_run_at = p_next_run_at,
           consecutive_failures = p_consecutive_failures,
           updated_at = p_now
     WHERE id = p_task_id;
    UPDATE public.scheduled_task_runs
       SET status = 'failed',
           error_message = LEFT(p_error_message, 500),
           tokens_used = p_tokens_used,
           duration_ms = p_duration_ms,
           finished_at = p_now
     WHERE id = p_run_id;
    RETURN jsonb_build_object('outcome', 'failed');
END;
$$;

REVOKE ALL ON FUNCTION worker_create_scheduled_run(UUID),
    worker_get_scheduled_task(UUID),
    worker_complete_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB, TEXT,
        INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_fail_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT, INTEGER,
        INTEGER, TIMESTAMPTZ
    )
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_create_scheduled_run(UUID),
    worker_get_scheduled_task(UUID),
    worker_complete_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB, TEXT,
        INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_fail_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT, INTEGER,
        INTEGER, TIMESTAMPTZ
    )
TO everydayai_worker;

RESET ROLE;
