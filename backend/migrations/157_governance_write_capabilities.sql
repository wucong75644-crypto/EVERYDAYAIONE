-- 157: Atomic organization, member, and invitation governance writes.
-- Prerequisites: migration 156 and 18-table second-wave ownership transfer.

SET LOCAL ROLE everydayai_owner;

ALTER TABLE governance_audit_log
    DROP CONSTRAINT governance_audit_log_authority_check;
ALTER TABLE governance_audit_log
    ADD CONSTRAINT governance_audit_log_authority_check
    CHECK (authority IN ('super_admin', 'owner', 'admin', 'self'));

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
    IF p_authority NOT IN ('super_admin', 'owner', 'admin', 'self')
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
    RETURN to_jsonb(v_org)
        - 'encrypt_key'
        - 'wecom_secret_encrypted';
END;
$$;

CREATE OR REPLACE FUNCTION update_governed_organization(
    p_org_id UUID,
    p_changes JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_org public.organizations%ROWTYPE;
    v_unknown_key TEXT;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], TRUE
    );
    IF p_changes IS NULL
       OR jsonb_typeof(p_changes) <> 'object'
       OR p_changes = '{}'::JSONB THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT key INTO v_unknown_key
      FROM jsonb_object_keys(p_changes) AS key
     WHERE key NOT IN ('name', 'logo_url', 'features', 'wecom_corp_id')
     LIMIT 1;
    IF v_unknown_key IS NOT NULL
       OR (p_changes ? 'name' AND (
           jsonb_typeof(p_changes->'name') <> 'string'
           OR COALESCE(BTRIM(p_changes->>'name'), '') = ''
           OR LENGTH(BTRIM(p_changes->>'name')) > 100
       ))
       OR (p_changes ? 'logo_url' AND p_changes->'logo_url' <> 'null'::JSONB
           AND (jsonb_typeof(p_changes->'logo_url') <> 'string'
                OR LENGTH(p_changes->>'logo_url') > 500))
       OR (p_changes ? 'features'
           AND jsonb_typeof(p_changes->'features') <> 'object')
       OR (p_changes ? 'wecom_corp_id'
           AND p_changes->'wecom_corp_id' <> 'null'::JSONB
           AND (jsonb_typeof(p_changes->'wecom_corp_id') <> 'string'
                OR LENGTH(BTRIM(p_changes->>'wecom_corp_id')) > 100)) THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE public.organizations SET
        name = CASE WHEN p_changes ? 'name'
            THEN BTRIM(p_changes->>'name') ELSE name END,
        logo_url = CASE WHEN p_changes ? 'logo_url'
            THEN NULLIF(p_changes->>'logo_url', '') ELSE logo_url END,
        features = CASE WHEN p_changes ? 'features'
            THEN p_changes->'features' ELSE features END,
        wecom_corp_id = CASE WHEN p_changes ? 'wecom_corp_id'
            THEN NULLIF(BTRIM(p_changes->>'wecom_corp_id'), '')
            ELSE wecom_corp_id END,
        updated_at = NOW()
     WHERE id = p_org_id
     RETURNING * INTO v_org;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'organization.update',
        'organization', p_org_id::TEXT,
        jsonb_build_object('fields', ARRAY(
            SELECT key FROM jsonb_object_keys(p_changes) AS key ORDER BY key
        ))
    );
    RETURN to_jsonb(v_org)
        - 'encrypt_key'
        - 'wecom_secret_encrypted';
EXCEPTION
    WHEN unique_violation THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_NAME_CONFLICT'
            USING ERRCODE = '23505';
END;
$$;

CREATE OR REPLACE FUNCTION add_governed_member(
    p_org_id UUID,
    p_target_user_id UUID,
    p_role TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_org public.organizations%ROWTYPE;
    v_member public.org_members%ROWTYPE;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    IF p_role NOT IN ('admin', 'member') OR NOT EXISTS (
        SELECT 1 FROM public.users
         WHERE id = p_target_user_id AND status::TEXT = 'active'
    ) THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_org FROM public.organizations
     WHERE id = p_org_id FOR UPDATE;
    IF (SELECT COUNT(*) FROM public.org_members
         WHERE org_id = p_org_id AND status = 'active') >= v_org.max_members THEN
        RAISE EXCEPTION 'GOVERNANCE_MEMBER_LIMIT_REACHED'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.org_members(
        org_id, user_id, role, status, invited_by
    ) VALUES (
        p_org_id, p_target_user_id, p_role, 'active',
        public.tenant_actor_user_id()
    ) RETURNING * INTO v_member;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'member.add', 'member',
        p_target_user_id::TEXT, jsonb_build_object('role', p_role)
    );
    RETURN to_jsonb(v_member);
EXCEPTION
    WHEN unique_violation THEN
        RAISE EXCEPTION 'GOVERNANCE_MEMBER_EXISTS'
            USING ERRCODE = '23505';
END;
$$;

CREATE OR REPLACE FUNCTION remove_governed_member(
    p_org_id UUID,
    p_target_user_id UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_target_role TEXT;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    IF p_target_user_id = public.tenant_actor_user_id() THEN
        RAISE EXCEPTION 'GOVERNANCE_SELF_MUTATION_DENIED'
            USING ERRCODE = '42501';
    END IF;
    SELECT role INTO v_target_role FROM public.org_members
     WHERE org_id = p_org_id AND user_id = p_target_user_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_MEMBER_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_target_role = 'owner'
       OR (v_authority = 'admin' AND v_target_role = 'admin') THEN
        RAISE EXCEPTION 'GOVERNANCE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM public.org_members
     WHERE org_id = p_org_id AND user_id = p_target_user_id;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'member.remove', 'member',
        p_target_user_id::TEXT,
        jsonb_build_object('previous_role', v_target_role)
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION change_governed_member_role(
    p_org_id UUID,
    p_target_user_id UUID,
    p_role TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_previous_role TEXT;
    v_member public.org_members%ROWTYPE;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner'], FALSE
    );
    IF p_role NOT IN ('admin', 'member')
       OR p_target_user_id = public.tenant_actor_user_id() THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT role INTO v_previous_role FROM public.org_members
     WHERE org_id = p_org_id AND user_id = p_target_user_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_MEMBER_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_previous_role = 'owner' THEN
        RAISE EXCEPTION 'GOVERNANCE_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.org_members SET role = p_role
     WHERE org_id = p_org_id AND user_id = p_target_user_id
     RETURNING * INTO v_member;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'member.role_change', 'member',
        p_target_user_id::TEXT,
        jsonb_build_object(
            'previous_role', v_previous_role, 'role', p_role
        )
    );
    RETURN to_jsonb(v_member);
END;
$$;

CREATE OR REPLACE FUNCTION create_governed_invitation(
    p_org_id UUID,
    p_phone TEXT,
    p_role TEXT,
    p_invite_token TEXT,
    p_expires_at TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
    v_invitation public.org_invitations%ROWTYPE;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin'], FALSE
    );
    IF p_phone !~ '^1[3-9][0-9]{9}$'
       OR p_role NOT IN ('admin', 'member')
       OR COALESCE(BTRIM(p_invite_token), '') = ''
       OR LENGTH(p_invite_token) > 100
       OR p_expires_at <= NOW() THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_org_id::TEXT || '::invite::' || p_phone, 0
    ));
    IF EXISTS (
        SELECT 1 FROM public.users account
        JOIN public.org_members member ON member.user_id = account.id
         WHERE account.phone = p_phone AND member.org_id = p_org_id
    ) OR EXISTS (
        SELECT 1 FROM public.org_invitations
         WHERE org_id = p_org_id
           AND phone = p_phone
           AND status = 'pending'
    ) THEN
        RAISE EXCEPTION 'GOVERNANCE_INVITATION_CONFLICT'
            USING ERRCODE = '23505';
    END IF;
    INSERT INTO public.org_invitations(
        org_id, phone, role, invite_token, invited_by, expires_at
    ) VALUES (
        p_org_id, p_phone, p_role, p_invite_token,
        public.tenant_actor_user_id(), p_expires_at
    ) RETURNING * INTO v_invitation;
    PERFORM public._record_governance_audit(
        p_org_id, v_authority, 'invitation.create', 'invitation',
        v_invitation.id::TEXT, jsonb_build_object('role', p_role)
    );
    RETURN to_jsonb(v_invitation);
EXCEPTION
    WHEN unique_violation THEN
        RAISE EXCEPTION 'GOVERNANCE_INVITATION_CONFLICT'
            USING ERRCODE = '23505';
END;
$$;

CREATE OR REPLACE FUNCTION accept_governed_invitation(p_invite_token TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_governance_self_scope();
    v_phone TEXT;
    v_invitation public.org_invitations%ROWTYPE;
    v_org public.organizations%ROWTYPE;
BEGIN
    IF COALESCE(BTRIM(p_invite_token), '') = '' THEN
        RAISE EXCEPTION 'GOVERNANCE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_invitation FROM public.org_invitations
     WHERE invite_token = p_invite_token FOR UPDATE;
    IF NOT FOUND OR v_invitation.status <> 'pending' THEN
        RAISE EXCEPTION 'GOVERNANCE_INVITATION_MISSING'
            USING ERRCODE = 'P0002';
    END IF;
    IF v_invitation.expires_at <= NOW() THEN
        RAISE EXCEPTION 'GOVERNANCE_INVITATION_EXPIRED'
            USING ERRCODE = '22023';
    END IF;
    SELECT phone INTO v_phone FROM public.users WHERE id = v_actor;
    IF v_phone IS DISTINCT FROM v_invitation.phone THEN
        RAISE EXCEPTION 'GOVERNANCE_INVITATION_RECIPIENT_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_org FROM public.organizations
     WHERE id = v_invitation.org_id AND status = 'active'
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'GOVERNANCE_ORG_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.org_members
         WHERE org_id = v_org.id AND user_id = v_actor
    ) THEN
        RAISE EXCEPTION 'GOVERNANCE_MEMBER_EXISTS'
            USING ERRCODE = '23505';
    END IF;
    IF (SELECT COUNT(*) FROM public.org_members
         WHERE org_id = v_org.id AND status = 'active') >= v_org.max_members THEN
        RAISE EXCEPTION 'GOVERNANCE_MEMBER_LIMIT_REACHED'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.org_members(
        org_id, user_id, role, status, invited_by
    ) VALUES (
        v_org.id, v_actor, v_invitation.role,
        'active', v_invitation.invited_by
    );
    UPDATE public.org_invitations SET status = 'accepted'
     WHERE id = v_invitation.id;
    PERFORM public._record_governance_audit(
        v_org.id, 'self', 'invitation.accept', 'invitation',
        v_invitation.id::TEXT,
        jsonb_build_object('role', v_invitation.role)
    );
    RETURN jsonb_build_object(
        'org_id', v_org.id,
        'role', v_invitation.role,
        'org_name', v_org.name
    );
END;
$$;

REVOKE ALL ON FUNCTION create_governed_organization(TEXT, UUID),
    update_governed_organization(UUID, JSONB),
    add_governed_member(UUID, UUID, TEXT),
    remove_governed_member(UUID, UUID),
    change_governed_member_role(UUID, UUID, TEXT),
    create_governed_invitation(UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ),
    accept_governed_invitation(TEXT)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION create_governed_organization(TEXT, UUID),
    update_governed_organization(UUID, JSONB),
    add_governed_member(UUID, UUID, TEXT),
    remove_governed_member(UUID, UUID),
    change_governed_member_role(UUID, UUID, TEXT),
    create_governed_invitation(UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ),
    accept_governed_invitation(TEXT)
TO everydayai_runtime;

RESET ROLE;
