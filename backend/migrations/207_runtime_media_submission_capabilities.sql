-- 207: Seal media submission transitions behind validated Runtime capabilities.

SET LOCAL ROLE everydayai_owner;

ALTER FUNCTION attach_generation_external_task(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
) RENAME TO _attach_generation_external_task_owner;
ALTER FUNCTION fail_prepared_generation_task(
    UUID, TEXT, TEXT, UUID
) RENAME TO _fail_prepared_generation_task_owner;

REVOKE ALL ON FUNCTION _attach_generation_external_task_owner(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION _fail_prepared_generation_task_owner(
    UUID, TEXT, TEXT, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

CREATE FUNCTION attach_generation_external_task(
    p_task_id UUID,
    p_external_task_id TEXT,
    p_credit_transaction_id UUID,
    p_org_id UUID,
    p_actual_model_id TEXT DEFAULT NULL,
    p_actual_request_params JSONB DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS NULL
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'GENERATION_SUBMISSION_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id = p_task_id;
    IF NOT FOUND
       OR v_task.user_id IS DISTINCT FROM public.tenant_actor_user_id()
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR NOT public.tenant_user_fact_visible(v_task.org_id, v_task.user_id) THEN
        RAISE EXCEPTION 'GENERATION_SUBMISSION_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN public._attach_generation_external_task_owner(
        p_task_id, p_external_task_id, p_credit_transaction_id, p_org_id,
        p_actual_model_id, p_actual_request_params
    );
END;
$$;

CREATE FUNCTION fail_prepared_generation_task(
    p_task_id UUID,
    p_terminal_reason TEXT,
    p_error_message TEXT,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS NULL
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'GENERATION_SUBMISSION_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id = p_task_id;
    IF NOT FOUND
       OR v_task.user_id IS DISTINCT FROM public.tenant_actor_user_id()
       OR v_task.org_id IS DISTINCT FROM p_org_id
       OR NOT public.tenant_user_fact_visible(v_task.org_id, v_task.user_id) THEN
        RAISE EXCEPTION 'GENERATION_SUBMISSION_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN public._fail_prepared_generation_task_owner(
        p_task_id, p_terminal_reason, p_error_message, p_org_id
    );
END;
$$;

REVOKE ALL ON FUNCTION attach_generation_external_task(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION fail_prepared_generation_task(
    UUID, TEXT, TEXT, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION attach_generation_external_task(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
) TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION fail_prepared_generation_task(
    UUID, TEXT, TEXT, UUID
) TO everydayai_runtime;

RESET ROLE;
