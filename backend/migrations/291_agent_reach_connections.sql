-- Additive Agent Reach foundations; feature remains disabled by default.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE public.reach_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope TEXT NOT NULL DEFAULT 'organization' CHECK (scope IN ('organization','user')),
    org_id UUID REFERENCES public.organizations(id),
    owner_user_id UUID REFERENCES public.users(id),
    platform TEXT NOT NULL CHECK (platform IN ('twitter','bilibili','reddit','xiaohongshu','youtube')),
    account_id TEXT NOT NULL CHECK (length(account_id) BETWEEN 1 AND 200),
    display_name TEXT NOT NULL DEFAULT '',
    secret_envelope JSONB,
    credential_version INTEGER NOT NULL DEFAULT 1 CHECK (credential_version > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    created_by UUID NOT NULL REFERENCES public.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((scope='organization' AND org_id IS NOT NULL AND owner_user_id IS NULL)
        OR (scope='user' AND org_id IS NULL AND owner_user_id IS NOT NULL)),
    UNIQUE(id,org_id)
);
CREATE TABLE public.reach_connection_grants (
    org_id UUID NOT NULL,
    connection_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES public.users(id),
    can_read BOOLEAN NOT NULL DEFAULT TRUE,
    can_write BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(connection_id,user_id),
    FOREIGN KEY(connection_id,org_id) REFERENCES public.reach_connections(id,org_id)
);
CREATE TABLE public.reach_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    connection_id UUID NOT NULL,
    actor_user_id UUID NOT NULL REFERENCES public.users(id),
    call_id TEXT NOT NULL CHECK (length(call_id) BETWEEN 1 AND 200),
    payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^[a-f0-9]{64}$'),
    credential_version INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('executing','succeeded','failed','uncertain')),
    receipt JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(org_id,actor_user_id,call_id),
    FOREIGN KEY(connection_id,org_id) REFERENCES public.reach_connections(id,org_id)
);

-- Scope comes only from the transaction-level server context. Personal
-- connections remain inaccessible until their product flow is implemented.
CREATE FUNCTION public.reach_active_member(p_org UUID, p_admin BOOLEAN DEFAULT FALSE)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
    SELECT p_org=public.tenant_org_id() AND public.tenant_actor_user_id() IS NOT NULL
    AND EXISTS (SELECT 1 FROM public.org_members m
        JOIN public.organizations o ON o.id=m.org_id JOIN public.users u ON u.id=m.user_id
        WHERE m.org_id=p_org AND m.user_id=public.tenant_actor_user_id()
        AND m.status='active' AND o.status='active' AND u.status='active'
        AND (NOT p_admin OR m.role IN ('owner','admin')))
$$;
REVOKE ALL ON FUNCTION public.reach_active_member(UUID,BOOLEAN) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reach_active_member(UUID,BOOLEAN) TO everydayai,everydayai_runtime;

ALTER TABLE public.reach_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reach_connections FORCE ROW LEVEL SECURITY;
ALTER TABLE public.reach_connection_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reach_connection_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE public.reach_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reach_operations FORCE ROW LEVEL SECURITY;
CREATE POLICY reach_connection_select ON public.reach_connections FOR SELECT
    USING (scope='organization' AND public.reach_active_member(org_id));
CREATE POLICY reach_connection_insert ON public.reach_connections FOR INSERT
    WITH CHECK (scope='organization' AND public.reach_active_member(org_id,TRUE)
        AND created_by=public.tenant_actor_user_id());
CREATE POLICY reach_connection_update ON public.reach_connections FOR UPDATE
    USING (scope='organization' AND public.reach_active_member(org_id,TRUE))
    WITH CHECK (scope='organization' AND public.reach_active_member(org_id,TRUE));
CREATE POLICY reach_grant_select ON public.reach_connection_grants FOR SELECT
    USING (public.reach_active_member(org_id));
CREATE POLICY reach_grant_insert ON public.reach_connection_grants FOR INSERT
    WITH CHECK (public.reach_active_member(org_id,TRUE));
CREATE POLICY reach_grant_update ON public.reach_connection_grants FOR UPDATE
    USING (public.reach_active_member(org_id,TRUE)) WITH CHECK (public.reach_active_member(org_id,TRUE));
CREATE POLICY reach_operation_select ON public.reach_operations FOR SELECT
    USING (public.reach_active_member(org_id) AND actor_user_id=public.tenant_actor_user_id());
CREATE POLICY reach_operation_insert ON public.reach_operations FOR INSERT
    WITH CHECK (public.reach_active_member(org_id) AND actor_user_id=public.tenant_actor_user_id()
        AND EXISTS (SELECT 1 FROM public.reach_connections c WHERE c.id=connection_id
            AND c.org_id=reach_operations.org_id AND c.status='active'
            AND c.credential_version=reach_operations.credential_version
            AND (public.reach_active_member(c.org_id,TRUE) OR EXISTS (
                SELECT 1 FROM public.reach_connection_grants g WHERE g.connection_id=c.id
                AND g.org_id=c.org_id AND g.user_id=public.tenant_actor_user_id() AND g.can_write))));
CREATE POLICY reach_operation_update ON public.reach_operations FOR UPDATE
    USING (public.reach_active_member(org_id) AND actor_user_id=public.tenant_actor_user_id())
    WITH CHECK (public.reach_active_member(org_id) AND actor_user_id=public.tenant_actor_user_id());
GRANT SELECT,INSERT,UPDATE ON public.reach_connections,public.reach_connection_grants,
    public.reach_operations TO everydayai,everydayai_runtime;
RESET ROLE;
