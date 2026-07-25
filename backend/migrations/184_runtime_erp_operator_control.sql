-- 184: Runtime 企业管理员通过窄能力绑定/解绑 ERP 运营人员。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION runtime_bind_erp_operator(
    p_org_id UUID,
    p_operator_id UUID,
    p_wecom_userid TEXT,
    p_operator_user_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_org_admin();
    v_org UUID := public.tenant_org_id();
    v_operator_name TEXT;
    v_employee_name TEXT;
BEGIN
    IF p_org_id IS DISTINCT FROM v_org
       OR p_operator_id IS NULL
       OR NULLIF(BTRIM(p_wecom_userid), '') IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_ERP_OPERATOR_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT employee.name INTO v_employee_name
      FROM public.wecom_employees employee
     WHERE employee.org_id = v_org
       AND employee.wecom_userid = p_wecom_userid
       AND employee.status = 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RUNTIME_ERP_OPERATOR_EMPLOYEE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF p_operator_user_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.org_members member
         WHERE member.org_id = v_org
           AND member.user_id = p_operator_user_id
           AND member.status = 'active'
    ) THEN
        RAISE EXCEPTION 'RUNTIME_ERP_OPERATOR_USER_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE public.erp_operators operator
       SET wecom_userid = p_wecom_userid,
           operator_user_id = p_operator_user_id,
           is_bound = TRUE,
           bound_at = NOW(),
           bound_by = v_actor,
           notes = '管理员手动绑定（' || v_employee_name || '）',
           updated_at = NOW()
     WHERE operator.id = p_operator_id
       AND operator.org_id = v_org
    RETURNING operator.operator_name INTO v_operator_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RUNTIME_ERP_OPERATOR_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;
    RETURN jsonb_build_object(
        'bound', TRUE,
        'operator_name', v_operator_name,
        'employee_name', v_employee_name
    );
END;
$$;

CREATE OR REPLACE FUNCTION runtime_unbind_erp_operator(
    p_org_id UUID,
    p_operator_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org UUID := public.tenant_org_id();
BEGIN
    PERFORM public._assert_configuration_runtime_org_admin();
    IF p_org_id IS DISTINCT FROM v_org OR p_operator_id IS NULL THEN
        RAISE EXCEPTION 'RUNTIME_ERP_OPERATOR_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.erp_operators operator
       SET wecom_userid = NULL,
           operator_user_id = NULL,
           is_bound = FALSE,
           notes = '管理员手动解绑',
           updated_at = NOW()
     WHERE operator.id = p_operator_id
       AND operator.org_id = v_org;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'RUNTIME_ERP_OPERATOR_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;
    RETURN jsonb_build_object('unbound', TRUE);
END;
$$;

REVOKE ALL ON FUNCTION runtime_bind_erp_operator(
    UUID, UUID, TEXT, UUID
), runtime_unbind_erp_operator(UUID, UUID)
FROM PUBLIC, everydayai, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION runtime_bind_erp_operator(
    UUID, UUID, TEXT, UUID
), runtime_unbind_erp_operator(UUID, UUID)
TO everydayai_runtime;

RESET ROLE;
