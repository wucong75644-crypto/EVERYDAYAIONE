-- 192: Make organization creation and its permission blueprint one transaction.
-- Prerequisites: migration 157 and runtime/message ownership transfer including
-- the organization permission-model tables.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _initialize_governed_org_positions(p_org_id UUID)
RETURNS VOID
LANGUAGE sql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
    INSERT INTO public.org_positions(org_id, code, name, level, is_system)
    VALUES
        (p_org_id, 'boss', '老板', 1, TRUE),
        (p_org_id, 'vp', '副总', 2, TRUE),
        (p_org_id, 'manager', '主管', 3, TRUE),
        (p_org_id, 'deputy', '副主管', 4, TRUE),
        (p_org_id, 'member', '员工', 5, TRUE);
$$;

CREATE OR REPLACE FUNCTION _initialize_governed_org_roles(p_org_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_expected_permissions CONSTANT INTEGER := 23;
BEGIN
    IF (
        SELECT COUNT(*) FROM public.permissions
         WHERE code = ANY(ARRAY[
             'task.view','task.create','task.edit','task.delete','task.execute',
             'task.push_to_others','order.view','order.edit','order.export',
             'product.view','product.edit','finance.view','finance.export',
             'finance.reconcile','stock.view','stock.edit','stock.inbound',
             'stock.outbound','sys.member.add','sys.member.edit',
             'sys.erp.config','sys.wecom.config','sys.permission.grant'
         ])
    ) <> v_expected_permissions THEN
        RAISE EXCEPTION 'GOVERNANCE_PERMISSION_CATALOG_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.org_roles(org_id, code, name, is_system)
    VALUES
        (p_org_id, 'role_ops', '运营角色', TRUE),
        (p_org_id, 'role_finance', '财务角色', TRUE),
        (p_org_id, 'role_warehouse', '仓库角色', TRUE),
        (p_org_id, 'role_service', '客服角色', TRUE),
        (p_org_id, 'role_design', '设计角色', TRUE),
        (p_org_id, 'role_hr', '人事角色', TRUE),
        (p_org_id, 'role_boss_full', '老板全权', TRUE),
        (p_org_id, 'role_vp_full', '副总全权', TRUE);

    INSERT INTO public.role_permissions(role_id, permission_code)
    SELECT role.id, permission.code
      FROM public.org_roles role
      CROSS JOIN public.permissions permission
     WHERE role.org_id = p_org_id
       AND (
           role.code = 'role_boss_full'
           OR (role.code = 'role_vp_full' AND permission.module <> 'sys')
           OR (role.code = 'role_ops' AND permission.code = ANY(ARRAY[
               'task.view','task.create','task.edit','task.delete','task.execute',
               'task.push_to_others','order.view','order.edit','order.export',
               'product.view','product.edit'
           ]))
           OR (role.code = 'role_finance' AND permission.code = ANY(ARRAY[
               'task.view','task.create','task.edit','task.delete','task.execute',
               'task.push_to_others','finance.view','finance.export',
               'finance.reconcile','order.view','order.export'
           ]))
           OR (role.code = 'role_warehouse' AND permission.code = ANY(ARRAY[
               'task.view','task.create','task.edit','task.delete','task.execute',
               'task.push_to_others','stock.view','stock.edit','stock.inbound',
               'stock.outbound','product.view'
           ]))
           OR (role.code = 'role_service' AND permission.code = ANY(ARRAY[
               'task.view','task.create','task.edit','task.delete','task.execute',
               'task.push_to_others','order.view','order.edit','product.view'
           ]))
           OR (role.code = 'role_design' AND permission.code = ANY(ARRAY[
               'task.view','task.create','task.edit','task.delete',
               'task.push_to_others','product.view'
           ]))
           OR (role.code = 'role_hr' AND permission.code = ANY(ARRAY[
               'task.view','task.create','task.edit','task.delete',
               'task.push_to_others','sys.member.edit'
           ]))
       );
END;
$$;

CREATE OR REPLACE FUNCTION _initialize_governed_org_structure(
    p_org_id UUID,
    p_owner_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_boss_position_id UUID;
BEGIN
    INSERT INTO public.org_departments(org_id, name, type, path)
    SELECT p_org_id, blueprint.name, blueprint.type,
           text2ltree('root.' || blueprint.type || '_' ||
               SUBSTRING(REPLACE(gen_random_uuid()::TEXT, '-', ''), 1, 8))
      FROM (VALUES
          ('运营一部', 'ops'), ('财务部', 'finance'),
          ('仓库部', 'warehouse'), ('客服部', 'service'),
          ('设计部', 'design'), ('人事部', 'hr')
      ) AS blueprint(name, type);

    INSERT INTO public.position_default_roles(
        org_id, position_code, department_type, role_id
    )
    SELECT p_org_id, position_code, mapping.department_type, role.id
      FROM (VALUES
          ('ops', 'role_ops'), ('finance', 'role_finance'),
          ('warehouse', 'role_warehouse'), ('service', 'role_service'),
          ('design', 'role_design'), ('hr', 'role_hr')
      ) AS mapping(department_type, role_code)
      CROSS JOIN (VALUES ('member'), ('deputy'), ('manager')) AS p(position_code)
      JOIN public.org_roles role
        ON role.org_id = p_org_id AND role.code = mapping.role_code;
    INSERT INTO public.position_default_roles(
        org_id, position_code, department_type, role_id
    )
    SELECT p_org_id, mapping.position_code, 'all', role.id
      FROM (VALUES ('boss', 'role_boss_full'), ('vp', 'role_vp_full'))
           AS mapping(position_code, role_code)
      JOIN public.org_roles role
        ON role.org_id = p_org_id AND role.code = mapping.role_code;

    SELECT id INTO STRICT v_boss_position_id
      FROM public.org_positions
     WHERE org_id = p_org_id AND code = 'boss';
    INSERT INTO public.org_member_assignments(
        org_id, user_id, department_id, position_id, data_scope, is_primary
    ) VALUES (
        p_org_id, p_owner_id, NULL, v_boss_position_id, 'all', TRUE
    );
END;
$$;

CREATE OR REPLACE FUNCTION create_governed_organization(
    p_name TEXT,
    p_owner_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_org public.organizations%ROWTYPE;
BEGIN
    v_authority := public._assert_governance_authority(
        NULL, ARRAY[]::TEXT[], TRUE
    );
    IF COALESCE(BTRIM(p_name), '') = ''
       OR LENGTH(BTRIM(p_name)) > 100
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = p_owner_id AND status::TEXT = 'active'
       ) THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'organization-name::' || LOWER(BTRIM(p_name)), 0
    ));
    IF EXISTS (
        SELECT 1 FROM public.organizations WHERE name = BTRIM(p_name)
    ) THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_NAME_CONFLICT'
            USING ERRCODE = '23505';
    END IF;
    INSERT INTO public.organizations(name, owner_id)
    VALUES (BTRIM(p_name), p_owner_id)
    RETURNING * INTO v_org;
    INSERT INTO public.org_members(org_id, user_id, role, status)
    VALUES (v_org.id, p_owner_id, 'owner', 'active');
    PERFORM public._initialize_governed_org_positions(v_org.id);
    PERFORM public._initialize_governed_org_roles(v_org.id);
    PERFORM public._initialize_governed_org_structure(v_org.id, p_owner_id);
    PERFORM public._record_governance_audit(
        v_org.id, v_authority, 'organization.create',
        'organization', v_org.id::TEXT,
        jsonb_build_object('owner_id', p_owner_id)
    );
    RETURN to_jsonb(v_org) - 'encrypt_key' - 'wecom_secret_encrypted';
END;
$$;

REVOKE ALL ON FUNCTION
    _initialize_governed_org_positions(UUID),
    _initialize_governed_org_roles(UUID),
    _initialize_governed_org_structure(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
REVOKE ALL ON FUNCTION create_governed_organization(TEXT, UUID)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION create_governed_organization(TEXT, UUID)
TO everydayai_runtime;

RESET ROLE;
