-- 156a: Governance authority root and secret-free audit ledger.
-- Prerequisites: migrations 150-155 and second-wave ownership transfer.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE governance_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    authority VARCHAR(20) NOT NULL
        CHECK (authority IN ('super_admin', 'owner', 'admin')),
    action VARCHAR(80) NOT NULL,
    target_kind VARCHAR(40) NOT NULL,
    target_key TEXT,
    request_id VARCHAR(128),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (BTRIM(action) <> ''),
    CHECK (BTRIM(target_kind) <> ''),
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX idx_governance_audit_org_created
    ON governance_audit_log(org_id, created_at DESC);
CREATE INDEX idx_governance_audit_actor_created
    ON governance_audit_log(actor_id, created_at DESC);

COMMENT ON TABLE governance_audit_log IS
    'Secret-free audit facts for platform and organization governance writes';
COMMENT ON COLUMN governance_audit_log.metadata IS
    'Non-secret identifiers and state labels only; configuration values are forbidden';

ALTER TABLE governance_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE governance_audit_log FORCE ROW LEVEL SECURITY;

CREATE POLICY governance_audit_owner_only ON governance_audit_log
TO everydayai_owner
USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');

CREATE OR REPLACE FUNCTION _assert_governance_authority(
    p_org_id UUID,
    p_allowed_org_roles TEXT[],
    p_allow_super_admin BOOLEAN DEFAULT FALSE
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
    v_user_role TEXT;
    v_member_role TEXT;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR v_actor IS NULL
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'GOVERNANCE_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_allowed_org_roles IS NULL
       OR p_allowed_org_roles <@ ARRAY['owner', 'admin', 'member']::TEXT[] IS FALSE THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT role::TEXT INTO v_user_role
      FROM public.users
     WHERE id = v_actor
       AND status::TEXT = 'active';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_PRINCIPAL_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    IF p_org_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.organizations
         WHERE id = p_org_id AND status = 'active'
    ) THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    IF p_allow_super_admin AND v_user_role = 'super_admin' THEN
        RETURN 'super_admin';
    END IF;
    IF p_org_id IS NULL OR cardinality(p_allowed_org_roles) = 0 THEN
        RAISE EXCEPTION 'GOVERNANCE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;

    SELECT role INTO v_member_role
      FROM public.org_members
     WHERE org_id = p_org_id
       AND user_id = v_actor
       AND status = 'active';
    IF NOT FOUND OR NOT (v_member_role = ANY(p_allowed_org_roles)) THEN
        RAISE EXCEPTION 'GOVERNANCE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_member_role;
END;
$$;

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

CREATE OR REPLACE FUNCTION _assert_governance_self_scope()
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public.tenant_actor_user_id();
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR v_actor IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.users
            WHERE id = v_actor AND status::TEXT = 'active'
       ) THEN
        RAISE EXCEPTION 'GOVERNANCE_SELF_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN v_actor;
END;
$$;

CREATE OR REPLACE FUNCTION list_actor_organizations()
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
        'org_id', organization.id,
        'name', organization.name,
        'logo_url', organization.logo_url,
        'role', member.role,
        'features', organization.features
    ) ORDER BY member.joined_at), '[]'::JSONB)
      INTO v_result
      FROM public.org_members member
      JOIN public.organizations organization
        ON organization.id = member.org_id
     WHERE member.user_id = v_actor
       AND member.status = 'active'
       AND organization.status = 'active';
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION get_governed_organization(p_org_id UUID)
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
        'id', id,
        'name', name,
        'logo_url', logo_url,
        'status', status,
        'max_members', max_members,
        'features', features,
        'wecom_corp_id', wecom_corp_id,
        'created_at', created_at,
        'updated_at', updated_at
    ) INTO v_result
      FROM public.organizations
     WHERE id = p_org_id;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_governed_members(p_org_id UUID)
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
        'user_id', member.user_id,
        'role', member.role,
        'status', member.status,
        'joined_at', member.joined_at,
        'nickname', account.nickname,
        'phone', CASE
            WHEN LENGTH(COALESCE(account.phone, '')) >= 7
                THEN LEFT(account.phone, 3) || '****' || RIGHT(account.phone, 4)
            ELSE COALESCE(account.phone, '')
        END
    ) ORDER BY member.joined_at), '[]'::JSONB)
      INTO v_result
      FROM public.org_members member
      JOIN public.users account ON account.id = member.user_id
     WHERE member.org_id = p_org_id;
    RETURN v_result;
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
     WHERE account.id = v_actor;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION list_all_governed_organizations()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        NULL, ARRAY[]::TEXT[], TRUE
    );
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', organization.id,
        'name', organization.name,
        'status', organization.status,
        'owner_id', organization.owner_id,
        'created_at', organization.created_at,
        'member_count', (
            SELECT COUNT(*)
              FROM public.org_members member
             WHERE member.org_id = organization.id
               AND member.status = 'active'
        )
    ) ORDER BY organization.created_at DESC), '[]'::JSONB)
      INTO v_result
      FROM public.organizations organization;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION search_governed_user_by_phone(p_phone TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_governance_authority(
        NULL, ARRAY[]::TEXT[], TRUE
    );
    IF p_phone !~ '^1[3-9][0-9]{9}$' THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT jsonb_build_object(
        'found', TRUE,
        'user', jsonb_build_object(
            'id', id,
            'nickname', nickname,
            'phone', LEFT(phone, 3) || '****' || RIGHT(phone, 4),
            'status', status
        )
    ) INTO v_result
      FROM public.users
     WHERE phone = p_phone;
    RETURN COALESCE(v_result, jsonb_build_object(
        'found', FALSE, 'user', NULL
    ));
END;
$$;

REVOKE ALL ON TABLE governance_audit_log
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
REVOKE ALL ON FUNCTION _assert_governance_authority(UUID, TEXT[], BOOLEAN)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
REVOKE ALL ON FUNCTION _record_governance_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
REVOKE ALL ON FUNCTION _assert_governance_self_scope()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
REVOKE ALL ON FUNCTION list_actor_organizations(),
    get_governed_organization(UUID),
    list_governed_members(UUID),
    list_actor_pending_invitations(),
    list_all_governed_organizations(),
    search_governed_user_by_phone(TEXT)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION list_actor_organizations(),
    get_governed_organization(UUID),
    list_governed_members(UUID),
    list_actor_pending_invitations(),
    list_all_governed_organizations(),
    search_governed_user_by_phone(TEXT)
TO everydayai_runtime;

RESET ROLE;
