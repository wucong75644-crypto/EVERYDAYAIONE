-- Rollback 186: 恢复迁移 171 的媒体批次终态实现。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION worker_settle_media_batch_item(
    p_external_task_id TEXT,
    p_expected_version INTEGER,
    p_status TEXT,
    p_result_data JSONB,
    p_error_message TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_batch_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(BTRIM(p_external_task_id), '') IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 0
       OR p_status NOT IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'MEDIA_WORKER_SETTLE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type = 'image'
       AND batch_id IS NOT NULL
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_task.status IN ('completed', 'failed', 'cancelled') THEN
        RETURN jsonb_build_object('outcome', 'already_terminal');
    END IF;
    IF v_task.version <> p_expected_version THEN
        RETURN jsonb_build_object('outcome', 'version_conflict');
    END IF;

    IF p_status = 'completed' AND v_task.credit_transaction_id IS NOT NULL THEN
        UPDATE public.credit_transactions
           SET status = 'confirmed',
               confirmed_at = NOW()
         WHERE id = v_task.credit_transaction_id
           AND status = 'pending';
    END IF;

    UPDATE public.tasks
       SET status = p_status,
           result_data = p_result_data,
           error_message = CASE
               WHEN p_status = 'failed' THEN p_error_message
               ELSE NULL
           END,
           completed_at = NOW()
     WHERE id = v_task.id;

    SELECT COALESCE(
               jsonb_agg(to_jsonb(batch_task) ORDER BY batch_task.image_index),
               '[]'::JSONB
           )
      INTO v_batch_tasks
      FROM public.tasks batch_task
     WHERE batch_task.batch_id = v_task.batch_id;

    RETURN jsonb_build_object(
        'outcome', 'settled',
        'batch_tasks', v_batch_tasks
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_settle_media_batch_item(
    TEXT, INTEGER, TEXT, JSONB, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_settle_media_batch_item(
    TEXT, INTEGER, TEXT, JSONB, TEXT
) TO everydayai_worker;
