-- 175: Worker 按媒体任务范围写入结构化知识指标。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_record_media_metric(
    p_task_id UUID,
    p_status TEXT,
    p_error_code TEXT,
    p_cost_time_ms INTEGER,
    p_prompt_tokens INTEGER,
    p_completion_tokens INTEGER,
    p_prompt_category TEXT,
    p_params JSONB,
    p_retried BOOLEAN,
    p_retry_from_model TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_metric_id UUID;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_METRIC_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_task_id IS NULL
       OR p_status NOT IN ('success', 'failed')
       OR p_cost_time_ms < 0
       OR p_prompt_tokens < 0
       OR p_completion_tokens < 0
       OR p_params IS NULL OR jsonb_typeof(p_params) <> 'object' THEN
        RAISE EXCEPTION 'MEDIA_METRIC_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id
       AND type IN ('image', 'video');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'MEDIA_METRIC_TASK_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;
    IF COALESCE((v_task.delivery_context ->> 'actor')::BOOLEAN, FALSE) THEN
        RAISE EXCEPTION 'MEDIA_METRIC_ACTOR_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO public.knowledge_metrics (
        task_type, model_id, status, error_code, cost_time_ms,
        prompt_tokens, completion_tokens, prompt_category, params,
        retried, retry_from_model, user_id, org_id
    ) VALUES (
        v_task.type, COALESCE(v_task.model_id, 'unknown'), p_status,
        p_error_code, p_cost_time_ms, p_prompt_tokens, p_completion_tokens,
        p_prompt_category, p_params, COALESCE(p_retried, FALSE),
        p_retry_from_model, v_task.user_id, v_task.org_id
    )
    RETURNING id INTO v_metric_id;
    RETURN jsonb_build_object('outcome', 'recorded', 'id', v_metric_id);
END;
$$;

REVOKE ALL ON FUNCTION worker_record_media_metric(
    UUID, TEXT, TEXT, INTEGER, INTEGER, INTEGER, TEXT, JSONB, BOOLEAN, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_record_media_metric(
    UUID, TEXT, TEXT, INTEGER, INTEGER, INTEGER, TEXT, JSONB, BOOLEAN, TEXT
) TO everydayai_worker;

RESET ROLE;
