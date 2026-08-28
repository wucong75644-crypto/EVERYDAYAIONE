-- 176: Worker 定时任务领取与卡死恢复能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_claim_due_scheduled_tasks(
    p_now TIMESTAMPTZ,
    p_limit INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_now IS NULL OR p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    WITH claimed AS (
        UPDATE public.scheduled_tasks
           SET status = 'running',
               next_run_at = NULL,
               updated_at = p_now
         WHERE id IN (
             SELECT id
               FROM public.scheduled_tasks
              WHERE status = 'active'
                AND next_run_at IS NOT NULL
                AND next_run_at <= p_now
              ORDER BY next_run_at
              LIMIT p_limit
              FOR UPDATE SKIP LOCKED
         )
         RETURNING *
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_tasks
      FROM claimed;
    RETURN v_tasks;
END;
$$;

CREATE FUNCTION worker_list_stale_scheduled_tasks(p_cutoff TIMESTAMPTZ)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_cutoff IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_STALE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(task)), '[]'::JSONB)
      INTO v_tasks
      FROM public.scheduled_tasks task
     WHERE task.status = 'running'
       AND task.updated_at < p_cutoff;
    RETURN v_tasks;
END;
$$;

CREATE FUNCTION worker_recover_stale_scheduled_task(
    p_task_id UUID,
    p_cutoff TIMESTAMPTZ,
    p_status TEXT,
    p_next_run_at TIMESTAMPTZ,
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
    IF p_task_id IS NULL OR p_cutoff IS NULL OR p_now IS NULL
       OR p_status NOT IN ('active', 'paused') THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_RECOVER_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE public.scheduled_tasks
       SET status = p_status,
           next_run_at = p_next_run_at,
           updated_at = p_now
     WHERE id = p_task_id
       AND status = 'running'
       AND updated_at < p_cutoff;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_recovered');
    END IF;
    UPDATE public.scheduled_task_runs
       SET status = 'failed',
           error_message = '进程异常退出，任务自动恢复',
           finished_at = p_now
     WHERE task_id = p_task_id
       AND status = 'running';
    RETURN jsonb_build_object('outcome', 'recovered');
END;
$$;

REVOKE ALL ON FUNCTION worker_claim_due_scheduled_tasks(
    TIMESTAMPTZ, INTEGER
), worker_list_stale_scheduled_tasks(TIMESTAMPTZ),
worker_recover_stale_scheduled_task(
    UUID, TIMESTAMPTZ, TEXT, TIMESTAMPTZ, TIMESTAMPTZ
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_claim_due_scheduled_tasks(
    TIMESTAMPTZ, INTEGER
), worker_list_stale_scheduled_tasks(TIMESTAMPTZ),
worker_recover_stale_scheduled_task(
    UUID, TIMESTAMPTZ, TEXT, TIMESTAMPTZ, TIMESTAMPTZ
) TO everydayai_worker;

RESET ROLE;
