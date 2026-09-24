-- User-owned configuration only; no model/runtime write grant.
CREATE TABLE public.conversation_skill_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    package_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    skill_key TEXT NOT NULL,
    created_by UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, skill_key),
    FOREIGN KEY (package_id, revision_id) REFERENCES public.skill_revisions(package_id, id)
        ON DELETE RESTRICT
);

CREATE FUNCTION public.skill_binding_conversation_access(target UUID, organization UUID) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
    SELECT organization = public.skill_catalog_org_id() AND EXISTS (
        SELECT 1 FROM public.conversations c
        JOIN public.organizations o ON o.id = c.org_id AND o.status = 'active'
        JOIN public.org_members m ON m.org_id = o.id AND m.status = 'active'
        JOIN public.users u ON u.id = m.user_id AND u.status = 'active'
        WHERE c.id = target AND c.org_id = organization
            AND c.scope_type = 'user' AND c.scope_id = c.user_id::TEXT
            AND u.id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
            AND (c.user_id = u.id OR m.role IN ('owner', 'admin'))
    )
$$;

CREATE FUNCTION public.skill_binding_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'SKILL_BINDING_IMMUTABLE' USING ERRCODE = '23514';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('skill-binding:' || NEW.conversation_id::TEXT, 0));
    IF NOT EXISTS (
        SELECT 1 FROM public.skill_packages p
        JOIN public.skill_revisions r ON r.package_id = p.id
        JOIN public.skill_assignments a ON a.package_id = p.id AND a.revision_id = r.id
        WHERE p.id = NEW.package_id AND p.skill_key = NEW.skill_key
            AND r.id = NEW.revision_id AND r.status = 'published'
            AND a.org_id = NEW.org_id AND a.enabled
            AND (p.org_id IS NULL OR p.org_id = NEW.org_id)
    ) THEN
        RAISE EXCEPTION 'SKILL_BINDING_REVISION_UNAVAILABLE' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.conversation_skill_bindings
                   WHERE conversation_id = NEW.conversation_id AND skill_key = NEW.skill_key)
       AND (SELECT count(*) FROM public.conversation_skill_bindings
            WHERE conversation_id = NEW.conversation_id) >= 4 THEN
        RAISE EXCEPTION 'SKILL_BINDING_LIMIT' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER skill_binding_guard BEFORE INSERT OR UPDATE ON public.conversation_skill_bindings
    FOR EACH ROW EXECUTE FUNCTION public.skill_binding_guard();
ALTER TABLE public.conversation_skill_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversation_skill_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY skill_bindings_read ON public.conversation_skill_bindings FOR SELECT TO everydayai
    USING (public.skill_binding_conversation_access(conversation_id, org_id));
CREATE POLICY skill_bindings_insert ON public.conversation_skill_bindings FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND created_by = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
        AND public.skill_binding_conversation_access(conversation_id, org_id));
CREATE POLICY skill_bindings_delete ON public.conversation_skill_bindings FOR DELETE TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin'
        AND public.skill_binding_conversation_access(conversation_id, org_id));
REVOKE ALL ON public.conversation_skill_bindings FROM PUBLIC;
GRANT SELECT, INSERT, DELETE ON public.conversation_skill_bindings TO everydayai;
REVOKE ALL ON FUNCTION public.skill_binding_conversation_access(UUID, UUID), public.skill_binding_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_binding_conversation_access(UUID, UUID), public.skill_binding_guard() TO everydayai;
