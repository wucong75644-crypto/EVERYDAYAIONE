SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION
    suspend_governed_organization(UUID),
    restore_governed_organization(UUID)
FROM everydayai_runtime;
DROP FUNCTION IF EXISTS suspend_governed_organization(UUID);
DROP FUNCTION IF EXISTS restore_governed_organization(UUID);

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
     WHERE account.id = v_actor;
    RETURN v_result;
END;
$$;

RESET ROLE;
