-- 178: Worker 定时任务执行的任务范围积分能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_lock_scheduled_credits(
    p_task_id UUID,
    p_run_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
    v_balance INTEGER;
    v_transaction_id UUID := gen_random_uuid();
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_CREDIT_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT task.* INTO v_task
      FROM public.scheduled_tasks task
      JOIN public.scheduled_task_runs run ON run.task_id = task.id
     WHERE task.id = p_task_id
       AND run.id = p_run_id
       AND task.status = 'running'
       AND run.status = 'running'
       AND run.org_id = task.org_id
     FOR UPDATE OF task;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    SELECT credits INTO v_balance
      FROM public.users
     WHERE id = v_task.user_id
     FOR UPDATE;
    IF v_balance IS NULL OR v_balance < v_task.max_credits THEN
        RETURN jsonb_build_object(
            'outcome', 'insufficient_credits',
            'required', v_task.max_credits,
            'current', COALESCE(v_balance, 0)
        );
    END IF;
    UPDATE public.users
       SET credits = credits - v_task.max_credits,
           updated_at = NOW()
     WHERE id = v_task.user_id;
    INSERT INTO public.credit_transactions (
        id, task_id, user_id, amount, type, status, reason, org_id
    ) VALUES (
        v_transaction_id, p_run_id, v_task.user_id, v_task.max_credits,
        'lock', 'pending', '定时任务: ' || v_task.name, v_task.org_id
    );
    RETURN jsonb_build_object(
        'outcome', 'locked',
        'transaction_id', v_transaction_id,
        'locked_amount', v_task.max_credits
    );
END;
$$;

CREATE FUNCTION worker_settle_scheduled_credits(
    p_task_id UUID,
    p_run_id UUID,
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
    v_task public.scheduled_tasks%ROWTYPE;
    v_transaction public.credit_transactions%ROWTYPE;
    v_actual INTEGER;
    v_refund_amount INTEGER;
    v_refund JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_CREDIT_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT task.* INTO v_task
      FROM public.scheduled_tasks task
      JOIN public.scheduled_task_runs run ON run.task_id = task.id
     WHERE task.id = p_task_id
       AND run.id = p_run_id
       AND run.org_id = task.org_id;
    SELECT * INTO v_transaction
      FROM public.credit_transactions
     WHERE id = p_transaction_id
     FOR UPDATE;
    IF v_task.id IS NULL OR v_transaction.id IS NULL
       OR v_transaction.task_id IS DISTINCT FROM p_run_id
       OR v_transaction.user_id IS DISTINCT FROM v_task.user_id
       OR v_transaction.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'SCHEDULED_CREDIT_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF v_transaction.status <> 'pending' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_settled',
            'final_credits_used', v_transaction.amount
        );
    END IF;
    IF NOT p_success THEN
        SELECT public.atomic_refund_credits(p_transaction_id) INTO v_refund;
        RETURN jsonb_build_object(
            'outcome', 'refunded',
            'final_credits_used', 0,
            'refund', v_refund
        );
    END IF;

    v_actual := GREATEST(
        1, LEAST(COALESCE(p_actual_amount, v_transaction.amount),
                 v_transaction.amount)
    );
    v_refund_amount := v_transaction.amount - v_actual;
    UPDATE public.credit_transactions
       SET status = 'confirmed', confirmed_at = NOW()
     WHERE id = p_transaction_id;
    IF v_refund_amount > 0 THEN
        SELECT public.partial_refund_credits(
            v_task.user_id, v_refund_amount,
            '按量计费差额退回 (tx=' || p_transaction_id || ')',
            v_task.org_id
        ) INTO v_refund;
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'confirmed',
        'final_credits_used', CASE
            WHEN v_refund_amount = 0
              OR COALESCE((v_refund ->> 'refunded')::BOOLEAN, FALSE)
            THEN v_actual
            ELSE v_transaction.amount
        END,
        'refund', v_refund
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_lock_scheduled_credits(UUID, UUID),
    worker_settle_scheduled_credits(UUID, UUID, UUID, BOOLEAN, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_lock_scheduled_credits(UUID, UUID),
    worker_settle_scheduled_credits(UUID, UUID, UUID, BOOLEAN, INTEGER)
TO everydayai_worker;

RESET ROLE;
