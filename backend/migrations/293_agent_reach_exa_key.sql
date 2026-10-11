-- Organization Exa overrides; server environment remains the fallback.
RESET ROLE;
SET LOCAL ROLE everydayai;
GRANT REFERENCES (id) ON public.organizations TO everydayai_owner;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.reach_exa_keys (
    org_id UUID PRIMARY KEY REFERENCES public.organizations(id),
    secret_envelope JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.reach_exa_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reach_exa_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY reach_exa_select ON public.reach_exa_keys FOR SELECT
    USING (public.reach_active_member(org_id));
CREATE POLICY reach_exa_insert ON public.reach_exa_keys FOR INSERT
    WITH CHECK (public.reach_active_member(org_id,TRUE));
CREATE POLICY reach_exa_update ON public.reach_exa_keys FOR UPDATE
    USING (public.reach_active_member(org_id,TRUE))
    WITH CHECK (public.reach_active_member(org_id,TRUE));
GRANT SELECT,INSERT,UPDATE ON public.reach_exa_keys TO everydayai,everydayai_runtime;
RESET ROLE;
SET LOCAL ROLE everydayai;
REVOKE REFERENCES (id) ON public.organizations FROM everydayai_owner;
RESET ROLE;
