SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS accept_governed_invitation(TEXT);
DROP FUNCTION IF EXISTS create_governed_invitation(
    UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ
);
DROP FUNCTION IF EXISTS change_governed_member_role(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS remove_governed_member(UUID, UUID);
DROP FUNCTION IF EXISTS add_governed_member(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS update_governed_organization(UUID, JSONB);
DROP FUNCTION IF EXISTS create_governed_organization(TEXT, UUID);

CREATE OR REPLACE FUNCTION _record_governance_audit(
    p_org_id UUID,
    p_authority TEXT,
    p_action TEXT,
    p_target_kind TEXT,
    p_target_key TEXT DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::JSONB
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_audit_id UUID;
BEGIN
    IF p_authority NOT IN ('super_admin', 'owner', 'admin')
       OR COALESCE(BTRIM(p_action), '') = ''
       OR COALESCE(BTRIM(p_target_kind), '') = ''
       OR p_metadata IS NULL
       OR jsonb_typeof(p_metadata) <> 'object' THEN
        RAISE EXCEPTION 'GOVERNANCE_AUDIT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.governance_audit_log(
        org_id, actor_id, authority, action, target_kind,
        target_key, request_id, metadata
    ) VALUES (
        p_org_id, public.tenant_actor_user_id(), p_authority,
        BTRIM(p_action), BTRIM(p_target_kind), p_target_key,
        NULLIF(current_setting('app.request_id', TRUE), ''), p_metadata
    ) RETURNING id INTO v_audit_id;
    RETURN v_audit_id;
END;
$$;

RESET ROLE;
