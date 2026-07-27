-- 218: Refund rejected Runtime media submissions through a tenant-scoped facade.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION refund_prepared_generation_credits(
    p_task_id UUID,
    p_transaction_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_transaction public.credit_transactions%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS NULL
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id
       OR p_task_id IS NULL
       OR p_transaction_id IS NULL THEN
        RAISE EXCEPTION 'GENERATION_REFUND_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_task.type::TEXT NOT IN ('image', 'video')
       OR v_task.status::TEXT <> 'preparing'
       OR v_task.user_id IS DISTINCT FROM public.tenant_actor_user_id()
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR NOT public.tenant_user_fact_visible(v_task.org_id, v_task.user_id) THEN
        RAISE EXCEPTION 'GENERATION_REFUND_TASK_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_transaction
      FROM public.credit_transactions
     WHERE id = p_transaction_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_transaction.task_id IS DISTINCT FROM v_task.id
       OR v_transaction.user_id IS DISTINCT FROM v_task.user_id
       OR v_transaction.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'GENERATION_REFUND_TRANSACTION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    RETURN public.atomic_refund_credits(p_transaction_id);
END;
$$;

REVOKE ALL ON FUNCTION refund_prepared_generation_credits(
    UUID, UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION refund_prepared_generation_credits(
    UUID, UUID, UUID
) TO everydayai_runtime;

COMMENT ON FUNCTION refund_prepared_generation_credits(UUID, UUID, UUID)
IS '租户范围内退回尚未提交供应商的图片或视频积分交易';

RESET ROLE;
