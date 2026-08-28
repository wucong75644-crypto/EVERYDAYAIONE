-- 173: 媒体 Worker 智能重试的积分准备、提交与中止能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_prepare_media_retry(
    p_external_task_id TEXT,
    p_expected_version INTEGER,
    p_new_model TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_balance INTEGER;
    v_transaction_id UUID := gen_random_uuid();
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_RETRY_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(BTRIM(p_external_task_id), '') IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 0
       OR NULLIF(BTRIM(p_new_model), '') IS NULL THEN
        RAISE EXCEPTION 'MEDIA_RETRY_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type IN ('image', 'video')
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF COALESCE((v_task.delivery_context ->> 'actor')::BOOLEAN, FALSE)
       OR v_task.status NOT IN ('pending', 'running')
       OR v_task.version <> p_expected_version THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    SELECT credits INTO v_balance
      FROM public.users
     WHERE id = v_task.user_id
     FOR UPDATE;
    IF v_balance IS NULL OR v_balance < COALESCE(v_task.credits_locked, 0) THEN
        RETURN jsonb_build_object(
            'outcome', 'insufficient_credits',
            'required', COALESCE(v_task.credits_locked, 0),
            'current', COALESCE(v_balance, 0)
        );
    END IF;

    UPDATE public.users
       SET credits = credits - COALESCE(v_task.credits_locked, 0),
           updated_at = NOW()
     WHERE id = v_task.user_id;
    INSERT INTO public.credit_transactions (
        id, task_id, user_id, amount, type, status, reason, org_id
    ) VALUES (
        v_transaction_id, gen_random_uuid(), v_task.user_id,
        COALESCE(v_task.credits_locked, 0), 'lock', 'pending',
        'Retry[' || v_task.type || ']: ' || BTRIM(p_new_model),
        v_task.org_id
    );
    RETURN jsonb_build_object(
        'outcome', 'prepared',
        'transaction_id', v_transaction_id
    );
END;
$$;

CREATE FUNCTION worker_abort_media_retry(
    p_external_task_id TEXT,
    p_expected_version INTEGER,
    p_transaction_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_transaction public.credit_transactions%ROWTYPE;
    v_refund JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_RETRY_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task
      FROM public.tasks
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type IN ('image', 'video');
    SELECT * INTO v_transaction
      FROM public.credit_transactions
     WHERE id = p_transaction_id;
    IF v_task.id IS NULL OR v_transaction.id IS NULL
       OR v_task.version <> p_expected_version
       OR v_transaction.user_id IS DISTINCT FROM v_task.user_id
       OR v_transaction.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'MEDIA_RETRY_ABORT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT public.atomic_refund_credits(p_transaction_id) INTO v_refund;
    RETURN jsonb_build_object('outcome', 'aborted', 'refund', v_refund);
END;
$$;

CREATE FUNCTION worker_commit_media_retry(
    p_external_task_id TEXT,
    p_expected_version INTEGER,
    p_new_external_task_id TEXT,
    p_new_model TEXT,
    p_request_params JSONB,
    p_transaction_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_transaction public.credit_transactions%ROWTYPE;
    v_refund JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_RETRY_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(BTRIM(p_new_external_task_id), '') IS NULL
       OR NULLIF(BTRIM(p_new_model), '') IS NULL
       OR p_request_params IS NULL
       OR jsonb_typeof(p_request_params) <> 'object' THEN
        RAISE EXCEPTION 'MEDIA_RETRY_COMMIT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type IN ('image', 'video')
     FOR UPDATE;
    SELECT * INTO v_transaction
      FROM public.credit_transactions
     WHERE id = p_transaction_id
     FOR UPDATE;
    IF v_task.id IS NULL OR v_transaction.id IS NULL
       OR v_task.version <> p_expected_version
       OR v_task.status NOT IN ('pending', 'running')
       OR v_transaction.status <> 'pending'
       OR v_transaction.user_id IS DISTINCT FROM v_task.user_id
       OR v_transaction.org_id IS DISTINCT FROM v_task.org_id THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    IF v_task.credit_transaction_id IS NOT NULL THEN
        SELECT public.atomic_refund_credits(v_task.credit_transaction_id)
          INTO v_refund;
    END IF;
    UPDATE public.tasks
       SET external_task_id = BTRIM(p_new_external_task_id),
           model_id = BTRIM(p_new_model),
           status = 'pending',
           request_params = p_request_params,
           credit_transaction_id = p_transaction_id,
           version = version + 1,
           error_message = NULL,
           last_polled_at = NULL
     WHERE id = v_task.id
     RETURNING * INTO v_task;
    RETURN jsonb_build_object(
        'outcome', 'committed',
        'task', to_jsonb(v_task),
        'old_refund', v_refund
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_prepare_media_retry(TEXT, INTEGER, TEXT),
    worker_abort_media_retry(TEXT, INTEGER, UUID),
    worker_commit_media_retry(TEXT, INTEGER, TEXT, TEXT, JSONB, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_prepare_media_retry(TEXT, INTEGER, TEXT),
    worker_abort_media_retry(TEXT, INTEGER, UUID),
    worker_commit_media_retry(TEXT, INTEGER, TEXT, TEXT, JSONB, UUID)
TO everydayai_worker;

RESET ROLE;
