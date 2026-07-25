-- 164: Actor Worker task-scoped execution capabilities.
-- Ordinary conversation reads remain protected by FORCE RLS. Task mutation and
-- terminal reads stay behind fencing-token SECURITY DEFINER facades.

SET LOCAL ROLE everydayai_owner;

GRANT SELECT ON TABLE conversations, messages TO everydayai_worker;

CREATE OR REPLACE FUNCTION worker_update_generation_progress(
    p_task_id UUID,
    p_execution_token UUID,
    p_accumulated_content TEXT,
    p_accumulated_blocks JSONB DEFAULT '[]'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_actor_worker_task_scope(p_task_id);
    SELECT public.update_generation_progress(
        p_task_id, p_execution_token, p_accumulated_content,
        p_accumulated_blocks
    ) INTO v_result;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION worker_update_generation_model(
    p_task_id UUID,
    p_execution_token UUID,
    p_model_id TEXT,
    p_request_params JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
BEGIN
    IF p_execution_token IS NULL
       OR NULLIF(BTRIM(p_model_id), '') IS NULL
       OR LENGTH(p_model_id) > 200
       OR p_request_params IS NULL
       OR jsonb_typeof(p_request_params) <> 'object' THEN
        RAISE EXCEPTION 'ACTOR_MODEL_UPDATE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM public._assert_actor_worker_task_scope(p_task_id);
    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF v_task.status <> 'running'
       OR v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_task.lease_expires_at IS NULL
       OR v_task.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    UPDATE public.tasks
       SET model_id = BTRIM(p_model_id),
           request_params = p_request_params
     WHERE id = p_task_id;
    RETURN jsonb_build_object('outcome', 'updated');
END;
$$;

CREATE OR REPLACE FUNCTION worker_get_generation_terminal_snapshot(
    p_task_id UUID,
    p_execution_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
BEGIN
    IF p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TERMINAL_READ_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM public._assert_actor_worker_task_scope(p_task_id);
    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id;
    IF v_task.status <> 'cancelled'
       AND v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RAISE EXCEPTION 'ACTOR_TERMINAL_READ_OWNERSHIP_LOST'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status NOT IN ('completed', 'failed', 'cancelled') THEN
        RETURN jsonb_build_object(
            'outcome', 'non_terminal', 'status', v_task.status
        );
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'terminal',
        'task', to_jsonb(v_task)
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_update_generation_progress(
    UUID, UUID, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_update_generation_model(
    UUID, UUID, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_get_generation_terminal_snapshot(
    UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;

GRANT EXECUTE ON FUNCTION worker_update_generation_progress(
    UUID, UUID, TEXT, JSONB
) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_update_generation_model(
    UUID, UUID, TEXT, JSONB
) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_get_generation_terminal_snapshot(
    UUID, UUID
) TO everydayai_worker;

COMMENT ON FUNCTION worker_update_generation_progress(
    UUID, UUID, TEXT, JSONB
) IS 'Actor Worker 在当前 task scope 与 fencing token 下写临时进度';
COMMENT ON FUNCTION worker_update_generation_model(
    UUID, UUID, TEXT, JSONB
) IS 'Actor Worker 在当前有效租约内记录智能重试模型';
COMMENT ON FUNCTION worker_get_generation_terminal_snapshot(UUID, UUID)
    IS 'Actor Worker 按 task scope 和 fencing token 读取终态投递快照';

RESET ROLE;
