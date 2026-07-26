-- 193: Runtime reads for organization assignments and permission evaluation.
-- The underlying permission-model tables remain unavailable to service roles.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION get_runtime_member_assignment(
    p_org_id UUID,
    p_user_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    SELECT jsonb_build_object(
        'id', assignment.id,
        'user_id', assignment.user_id,
        'org_id', assignment.org_id,
        'department_id', assignment.department_id,
        'position_id', assignment.position_id,
        'position_code', position.code,
        'department_type', department.type,
        'department_name', department.name,
        'job_title', assignment.job_title,
        'data_scope', assignment.data_scope,
        'data_scope_dept_ids', assignment.data_scope_dept_ids,
        'perm_version', assignment.perm_version
    ) INTO v_result
      FROM public.org_member_assignments assignment
      JOIN public.org_members member
        ON member.org_id = assignment.org_id
       AND member.user_id = assignment.user_id
       AND member.status = 'active'
      JOIN public.org_positions position
        ON position.id = assignment.position_id
       AND position.org_id = assignment.org_id
      LEFT JOIN public.org_departments department
        ON department.id = assignment.department_id
       AND department.org_id = assignment.org_id
     WHERE assignment.org_id = p_org_id
       AND assignment.user_id = p_user_id
       AND assignment.is_primary = TRUE;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_runtime_member_assignments(
    p_org_id UUID,
    p_user_ids UUID[]
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    IF p_user_ids IS NULL OR cardinality(p_user_ids) > 500 THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'user_id', assignment.user_id,
        'department_id', assignment.department_id,
        'position_id', assignment.position_id,
        'position_code', position.code,
        'department_type', department.type,
        'department_name', department.name,
        'job_title', assignment.job_title,
        'data_scope', assignment.data_scope,
        'data_scope_dept_ids', assignment.data_scope_dept_ids,
        'perm_version', assignment.perm_version
    )), '[]'::JSONB) INTO v_result
      FROM public.org_member_assignments assignment
      JOIN public.org_members member
        ON member.org_id = assignment.org_id
       AND member.user_id = assignment.user_id
       AND member.status = 'active'
      JOIN public.org_positions position
        ON position.id = assignment.position_id
       AND position.org_id = assignment.org_id
      LEFT JOIN public.org_departments department
        ON department.id = assignment.department_id
       AND department.org_id = assignment.org_id
     WHERE assignment.org_id = p_org_id
       AND assignment.user_id = ANY(p_user_ids)
       AND assignment.is_primary = TRUE;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_runtime_org_departments(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', id, 'name', name, 'type', type, 'sort_order', sort_order
    ) ORDER BY sort_order, created_at), '[]'::JSONB)
      INTO v_result
      FROM public.org_departments
     WHERE org_id = p_org_id;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_runtime_org_positions(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', id, 'code', code, 'name', name, 'level', level
    ) ORDER BY level), '[]'::JSONB)
      INTO v_result
      FROM public.org_positions
     WHERE org_id = p_org_id;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_runtime_department_user_ids(
    p_org_id UUID,
    p_department_ids UUID[]
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    IF p_department_ids IS NULL OR cardinality(p_department_ids) > 100
       OR EXISTS (
           SELECT 1 FROM unnest(p_department_ids) requested(id)
            WHERE NOT EXISTS (
                SELECT 1 FROM public.org_departments department
                 WHERE department.id = requested.id
                   AND department.org_id = p_org_id
            )
       ) THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(DISTINCT assignment.user_id), '[]'::JSONB)
      INTO v_result
      FROM public.org_member_assignments assignment
      JOIN public.org_members member
        ON member.org_id = assignment.org_id
       AND member.user_id = assignment.user_id
       AND member.status = 'active'
     WHERE assignment.org_id = p_org_id
       AND assignment.department_id = ANY(p_department_ids)
       AND assignment.is_primary = TRUE;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION
    get_runtime_member_assignment(UUID, UUID),
    list_runtime_member_assignments(UUID, UUID[]),
    list_runtime_org_departments(UUID),
    list_runtime_org_positions(UUID),
    list_runtime_department_user_ids(UUID, UUID[])
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION
    get_runtime_member_assignment(UUID, UUID),
    list_runtime_member_assignments(UUID, UUID[]),
    list_runtime_org_departments(UUID),
    list_runtime_org_positions(UUID),
    list_runtime_department_user_ids(UUID, UUID[])
TO everydayai_runtime;

RESET ROLE;
