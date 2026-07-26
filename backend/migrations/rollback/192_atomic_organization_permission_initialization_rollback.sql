SET LOCAL ROLE everydayai_owner;

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
    PERFORM public._record_governance_audit(
        v_org.id, v_authority, 'organization.create',
        'organization', v_org.id::TEXT,
        jsonb_build_object('owner_id', p_owner_id)
    );
    RETURN to_jsonb(v_org) - 'encrypt_key' - 'wecom_secret_encrypted';
END;
$$;

DROP FUNCTION IF EXISTS _initialize_governed_org_structure(UUID, UUID);
DROP FUNCTION IF EXISTS _initialize_governed_org_roles(UUID);
DROP FUNCTION IF EXISTS _initialize_governed_org_positions(UUID);

REVOKE ALL ON FUNCTION create_governed_organization(TEXT, UUID)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION create_governed_organization(TEXT, UUID)
TO everydayai_runtime;

RESET ROLE;
