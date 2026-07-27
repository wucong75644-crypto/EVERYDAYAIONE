-- 217: Atomic platform organization suspension and restoration.
-- Prerequisites: migrations 156, 157, 189 and 191.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION suspend_governed_organization(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_org public.organizations%ROWTYPE;
BEGIN
    IF public.tenant_org_id() IS NOT NULL THEN
        RAISE EXCEPTION 'GOVERNANCE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    v_authority := public._assert_governance_authority(
        NULL, ARRAY[]::TEXT[], TRUE
    );
    SELECT * INTO v_org
      FROM public.organizations
     WHERE id = p_org_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_org.status <> 'active' THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_STATUS_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
    UPDATE public.organizations
       SET status = 'suspended', updated_at = NOW()
     WHERE id = p_org_id AND status = 'active'
     RETURNING * INTO v_org;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_STATUS_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'organization.suspend',
        'organization', p_org_id::TEXT,
        jsonb_build_object(
            'previous_status', 'active',
            'new_status', 'suspended'
        )
    );
    RETURN jsonb_build_object(
        'id', v_org.id,
        'name', v_org.name,
        'status', v_org.status,
        'owner_id', v_org.owner_id,
        'created_at', v_org.created_at,
        'updated_at', v_org.updated_at
    );
END;
$$;

CREATE FUNCTION restore_governed_organization(p_org_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_org public.organizations%ROWTYPE;
BEGIN
    IF public.tenant_org_id() IS NOT NULL THEN
        RAISE EXCEPTION 'GOVERNANCE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    v_authority := public._assert_governance_authority(
        NULL, ARRAY[]::TEXT[], TRUE
    );
    SELECT * INTO v_org
      FROM public.organizations
     WHERE id = p_org_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_org.status <> 'suspended' THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_STATUS_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
    UPDATE public.organizations
       SET status = 'active', updated_at = NOW()
     WHERE id = p_org_id AND status = 'suspended'
     RETURNING * INTO v_org;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_STATUS_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'organization.restore',
        'organization', p_org_id::TEXT,
        jsonb_build_object(
            'previous_status', 'suspended',
            'new_status', 'active'
        )
    );
    RETURN jsonb_build_object(
        'id', v_org.id,
        'name', v_org.name,
        'status', v_org.status,
        'owner_id', v_org.owner_id,
        'created_at', v_org.created_at,
        'updated_at', v_org.updated_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION list_actor_pending_invitations()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_governance_self_scope();
    v_result JSONB;
BEGIN
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'invite_token', invitation.invite_token,
        'org_name', organization.name,
        'role', invitation.role,
        'expires_at', invitation.expires_at
    ) ORDER BY invitation.created_at), '[]'::JSONB)
      INTO v_result
      FROM public.users account
      JOIN public.org_invitations invitation
        ON invitation.phone = account.phone
       AND invitation.status = 'pending'
       AND invitation.expires_at > NOW()
      JOIN public.organizations organization
        ON organization.id = invitation.org_id
       AND organization.status = 'active'
     WHERE account.id = v_actor;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION
    suspend_governed_organization(UUID),
    restore_governed_organization(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    suspend_governed_organization(UUID),
    restore_governed_organization(UUID)
TO everydayai_runtime;

RESET ROLE;
