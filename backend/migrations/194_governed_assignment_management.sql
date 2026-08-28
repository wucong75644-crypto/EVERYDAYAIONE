-- 194: Governed organization assignment management.

SET LOCAL ROLE everydayai_owner;

ALTER TABLE org_members
    ADD COLUMN display_name VARCHAR(50);

COMMENT ON COLUMN org_members.display_name IS
    'Enterprise-local member display name; never overwrites users.nickname';

CREATE OR REPLACE FUNCTION list_governed_member_assignments(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'user_id', member.user_id,
        'nickname', COALESCE(member.display_name, account.nickname, '未知'),
        'avatar_url', account.avatar_url,
        'phone', CASE WHEN LENGTH(COALESCE(account.phone, '')) >= 7
            THEN LEFT(account.phone, 3) || '****' || RIGHT(account.phone, 4)
            ELSE COALESCE(account.phone, '') END,
        'org_role', member.role,
        'assignment', CASE WHEN assignment.id IS NULL THEN NULL
            ELSE jsonb_build_object(
                'department_id', assignment.department_id,
                'department_name', department.name,
                'department_type', department.type,
                'position_id', assignment.position_id,
                'position_code', position.code,
                'position_name', position.name,
                'job_title', assignment.job_title,
                'data_scope', assignment.data_scope,
                'data_scope_dept_ids',
                    COALESCE(assignment.data_scope_dept_ids, ARRAY[]::UUID[])
            ) END
    ) ORDER BY member.joined_at), '[]'::JSONB) INTO v_result
      FROM public.org_members member
      JOIN public.users account ON account.id = member.user_id
      LEFT JOIN public.org_member_assignments assignment
        ON assignment.org_id = member.org_id
       AND assignment.user_id = member.user_id
       AND assignment.is_primary = TRUE
      LEFT JOIN public.org_departments department
        ON department.id = assignment.department_id
       AND department.org_id = member.org_id
      LEFT JOIN public.org_positions position
        ON position.id = assignment.position_id
       AND position.org_id = member.org_id
     WHERE member.org_id = p_org_id AND member.status = 'active';
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_governed_wecom_assignments(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'user_id', mapping.user_id,
        'nickname', COALESCE(
            member.display_name, account.nickname, mapping.wecom_nickname, '未知'
        ),
        'avatar_url', account.avatar_url,
        'wecom_userid', mapping.wecom_userid,
        'wecom_nickname', mapping.wecom_nickname,
        'channel', mapping.channel,
        'last_chat_type', mapping.last_chat_type,
        'joined_at', mapping.created_at,
        'assignment', CASE WHEN assignment.id IS NULL THEN NULL
            ELSE jsonb_build_object(
                'department_id', assignment.department_id,
                'department_name', department.name,
                'department_type', department.type,
                'position_id', assignment.position_id,
                'position_code', position.code,
                'position_name', position.name,
                'job_title', assignment.job_title,
                'data_scope', assignment.data_scope,
                'data_scope_dept_ids',
                    COALESCE(assignment.data_scope_dept_ids, ARRAY[]::UUID[])
            ) END
    ) ORDER BY mapping.created_at DESC), '[]'::JSONB) INTO v_result
      FROM public.wecom_user_mappings mapping
      JOIN public.users account ON account.id = mapping.user_id
      JOIN public.org_members member
        ON member.org_id = mapping.org_id
       AND member.user_id = mapping.user_id
       AND member.status = 'active'
      LEFT JOIN public.org_member_assignments assignment
        ON assignment.org_id = mapping.org_id
       AND assignment.user_id = mapping.user_id
       AND assignment.is_primary = TRUE
      LEFT JOIN public.org_departments department
        ON department.id = assignment.department_id
       AND department.org_id = mapping.org_id
      LEFT JOIN public.org_positions position
        ON position.id = assignment.position_id
       AND position.org_id = mapping.org_id
     WHERE mapping.org_id = p_org_id;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION _validate_governed_assignment_change(
    p_org_id UUID,
    p_target_user_id UUID,
    p_changes JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_target_role TEXT;
    v_owner_id UUID;
    v_position_id UUID;
    v_position_code TEXT;
    v_unknown_key TEXT;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    IF p_changes IS NULL OR jsonb_typeof(p_changes) <> 'object'
       OR p_changes = '{}'::JSONB THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT key INTO v_unknown_key FROM jsonb_object_keys(p_changes) AS key
     WHERE key NOT IN (
         'department_id', 'position_code', 'job_title',
         'data_scope', 'data_scope_dept_ids'
     ) LIMIT 1;
    SELECT member.role, organization.owner_id
      INTO v_target_role, v_owner_id
      FROM public.org_members member
      JOIN public.organizations organization ON organization.id = member.org_id
     WHERE member.org_id = p_org_id
       AND member.user_id = p_target_user_id
       AND member.status = 'active' FOR UPDATE OF member;
    IF v_unknown_key IS NOT NULL OR v_target_role IS NULL THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF v_authority = 'admin' AND v_target_role IN ('owner', 'admin') THEN
        RAISE EXCEPTION 'GOVERNANCE_AUTHORITY_DENIED' USING ERRCODE = '42501';
    END IF;
    IF p_changes ? 'position_code' THEN
        v_position_code := p_changes->>'position_code';
        SELECT id INTO v_position_id FROM public.org_positions
         WHERE org_id = p_org_id AND code = v_position_code;
        IF v_position_id IS NULL THEN
            RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
                USING ERRCODE = '22023';
        END IF;
        IF (v_position_code = 'boss' AND p_target_user_id <> v_owner_id)
           OR (v_authority = 'admin' AND v_position_code IN ('boss', 'vp')) THEN
            RAISE EXCEPTION 'GOVERNANCE_AUTHORITY_DENIED'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    IF p_changes ? 'department_id'
       AND p_changes->'department_id' <> 'null'::JSONB
       AND NOT EXISTS (
           SELECT 1 FROM public.org_departments
            WHERE id = (p_changes->>'department_id')::UUID
              AND org_id = p_org_id
       ) THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF p_changes ? 'data_scope'
       AND p_changes->>'data_scope' NOT IN ('all', 'dept_subtree', 'self') THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF p_changes ? 'job_title'
       AND LENGTH(COALESCE(p_changes->>'job_title', '')) > 50 THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF p_changes ? 'data_scope_dept_ids' AND (
        jsonb_typeof(p_changes->'data_scope_dept_ids') <> 'array'
        OR jsonb_array_length(p_changes->'data_scope_dept_ids') > 100
        OR EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(
                p_changes->'data_scope_dept_ids'
            ) requested(id)
            WHERE NOT EXISTS (
                SELECT 1 FROM public.org_departments
                 WHERE org_id = p_org_id AND id = requested.id::UUID
            )
        )
    ) THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
        'authority', v_authority,
        'position_id', v_position_id,
        'position_code', v_position_code
    );
END;
$$;

CREATE OR REPLACE FUNCTION update_governed_member_assignment(
    p_org_id UUID,
    p_target_user_id UUID,
    p_changes JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_position_id UUID;
    v_position_code TEXT;
    v_assignment public.org_member_assignments%ROWTYPE;
    v_validation JSONB;
BEGIN
    v_validation := public._validate_governed_assignment_change(
        p_org_id, p_target_user_id, p_changes
    );
    v_authority := v_validation->>'authority';
    v_position_id := (v_validation->>'position_id')::UUID;
    v_position_code := v_validation->>'position_code';
    SELECT * INTO v_assignment FROM public.org_member_assignments
     WHERE org_id = p_org_id AND user_id = p_target_user_id
       AND is_primary = TRUE FOR UPDATE;
    IF NOT FOUND THEN
        SELECT id INTO v_position_id FROM public.org_positions
         WHERE org_id = p_org_id
           AND code = COALESCE(v_position_code, 'member');
        INSERT INTO public.org_member_assignments(
            org_id, user_id, department_id, position_id, job_title,
            data_scope, data_scope_dept_ids, is_primary
        ) VALUES (
            p_org_id, p_target_user_id,
            CASE WHEN p_changes ? 'department_id'
                THEN (p_changes->>'department_id')::UUID END,
            v_position_id, NULLIF(p_changes->>'job_title', ''),
            COALESCE(p_changes->>'data_scope', 'self'),
            CASE WHEN p_changes ? 'data_scope_dept_ids' THEN ARRAY(
                SELECT value::UUID FROM jsonb_array_elements_text(
                    p_changes->'data_scope_dept_ids'
                ) value
            ) END, TRUE
        ) RETURNING * INTO v_assignment;
    ELSE
        UPDATE public.org_member_assignments SET
            department_id = CASE WHEN p_changes ? 'department_id'
                THEN (p_changes->>'department_id')::UUID ELSE department_id END,
            position_id = COALESCE(v_position_id, position_id),
            job_title = CASE WHEN p_changes ? 'job_title'
                THEN NULLIF(p_changes->>'job_title', '') ELSE job_title END,
            data_scope = CASE WHEN p_changes ? 'data_scope'
                THEN p_changes->>'data_scope' ELSE data_scope END,
            data_scope_dept_ids = CASE
                WHEN p_changes ? 'data_scope_dept_ids' THEN ARRAY(
                    SELECT value::UUID FROM jsonb_array_elements_text(
                        p_changes->'data_scope_dept_ids'
                    ) value
                ) ELSE data_scope_dept_ids END,
            perm_version = perm_version + 1,
            updated_at = NOW()
         WHERE id = v_assignment.id RETURNING * INTO v_assignment;
    END IF;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'member.assignment_update', 'member',
        p_target_user_id::TEXT,
        jsonb_build_object('fields', ARRAY(
            SELECT key FROM jsonb_object_keys(p_changes) key ORDER BY key
        ))
    );
    RETURN to_jsonb(v_assignment);
EXCEPTION
    WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID' USING ERRCODE = '22023';
END;
$$;

REVOKE ALL ON FUNCTION
    _validate_governed_assignment_change(UUID, UUID, JSONB),
    list_governed_member_assignments(UUID),
    list_governed_wecom_assignments(UUID),
    update_governed_member_assignment(UUID, UUID, JSONB)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION
    list_governed_member_assignments(UUID),
    list_governed_wecom_assignments(UUID),
    update_governed_member_assignment(UUID, UUID, JSONB)
TO everydayai_runtime;

RESET ROLE;
