-- 195: Enterprise-local member display name.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION update_governed_member_display_name(
    p_org_id UUID,
    p_target_user_id UUID,
    p_display_name TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_target_role TEXT;
    v_result JSONB;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    IF NULLIF(BTRIM(p_display_name), '') IS NULL
       OR LENGTH(BTRIM(p_display_name)) > 50 THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT role INTO v_target_role FROM public.org_members
     WHERE org_id = p_org_id AND user_id = p_target_user_id
       AND status = 'active' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_MEMBER_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_authority = 'admin' AND v_target_role IN ('owner', 'admin') THEN
        RAISE EXCEPTION 'GOVERNANCE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.org_members
       SET display_name = BTRIM(p_display_name)
     WHERE org_id = p_org_id AND user_id = p_target_user_id
     RETURNING jsonb_build_object(
         'user_id', user_id, 'display_name', display_name
     ) INTO v_result;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'member.display_name_update', 'member',
        p_target_user_id::TEXT, '{}'::JSONB
    );
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION update_governed_member_display_name(UUID, UUID, TEXT)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION update_governed_member_display_name(UUID, UUID, TEXT)
TO everydayai_runtime;

RESET ROLE;
