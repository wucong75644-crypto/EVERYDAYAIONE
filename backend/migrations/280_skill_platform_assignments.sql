-- Platform-wide availability is explicit and opt-in. Existing assignments stay
-- organization-scoped; an explicit organization row (including disabled) wins.
-- These legacy Skill tables/functions are owned by the existing application
-- role, not everydayai_owner. Keep their ownership and FORCE RLS unchanged.
SET LOCAL ROLE everydayai;
ALTER TABLE public.skill_assignments DROP CONSTRAINT skill_assignments_pkey;
ALTER TABLE public.skill_assignments ALTER COLUMN org_id DROP NOT NULL;
ALTER TABLE public.skill_assignments ADD CONSTRAINT skill_assignments_org_package_key UNIQUE (org_id, package_id);
CREATE UNIQUE INDEX skill_assignments_platform_key ON public.skill_assignments(package_id) WHERE org_id IS NULL;

CREATE FUNCTION public.skill_platform_assignment_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.org_id IS NULL AND NOT EXISTS (
        SELECT 1 FROM public.skill_packages WHERE id=NEW.package_id AND scope_kind='platform'
    ) THEN
        RAISE EXCEPTION 'SKILL_GLOBAL_ASSIGNMENT_REQUIRES_PLATFORM' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER skill_platform_assignment_guard BEFORE INSERT OR UPDATE ON public.skill_assignments
    FOR EACH ROW EXECUTE FUNCTION public.skill_platform_assignment_guard();

DROP POLICY skill_assignments_read ON public.skill_assignments;
CREATE POLICY skill_assignments_read ON public.skill_assignments FOR SELECT TO everydayai
    USING (org_id=public.skill_catalog_org_id() OR (org_id IS NULL AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id=package_id AND p.scope_kind='platform')));

-- app.* is the trusted server scope. Null-actor operation is reserved for an
-- audited server operator; HTTP callers must be current active super admins.
CREATE FUNCTION public.skill_platform_assignment_write_allowed() RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public AS $$
    SELECT current_setting('app.access_kind', true)='runtime_admin'
        AND public.skill_catalog_org_id() IS NULL
        AND current_setting('app.skill_action', true)='platform_assign'
        AND CASE WHEN NULLIF(current_setting('app.actor_user_id', true), '') IS NULL
            THEN length(coalesce(current_setting('app.request_id', true), '')) > 0
            ELSE EXISTS (SELECT 1 FROM public.users WHERE id=NULLIF(current_setting('app.actor_user_id', true), '')::UUID
                AND status='active' AND role='super_admin') END
$$;
DROP POLICY skill_assignments_insert ON public.skill_assignments;
CREATE POLICY skill_assignments_insert ON public.skill_assignments FOR INSERT TO everydayai
    WITH CHECK ((org_id=public.skill_catalog_org_id() AND current_setting('app.access_kind', true)='runtime_admin')
        OR (org_id IS NULL AND public.skill_platform_assignment_write_allowed()));
DROP POLICY skill_assignments_update ON public.skill_assignments;
CREATE POLICY skill_assignments_update ON public.skill_assignments FOR UPDATE TO everydayai
    USING ((org_id=public.skill_catalog_org_id() AND current_setting('app.access_kind', true)='runtime_admin')
        OR (org_id IS NULL AND public.skill_platform_assignment_write_allowed()))
    WITH CHECK ((org_id=public.skill_catalog_org_id() AND current_setting('app.access_kind', true)='runtime_admin')
        OR (org_id IS NULL AND public.skill_platform_assignment_write_allowed()));
REVOKE ALL ON FUNCTION public.skill_platform_assignment_guard(), public.skill_platform_assignment_write_allowed() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_platform_assignment_guard(), public.skill_platform_assignment_write_allowed() TO everydayai;

CREATE OR REPLACE FUNCTION public.skill_binding_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE actor UUID := NULLIF(current_setting('app.actor_user_id', true), '')::UUID;
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'SKILL_BINDING_IMMUTABLE' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('skill-binding:' || NEW.conversation_id::TEXT, 0));
    IF NOT EXISTS (
        SELECT 1 FROM public.skill_packages p JOIN public.skill_revisions r ON r.package_id=p.id
        LEFT JOIN LATERAL (SELECT * FROM public.skill_assignments
            WHERE package_id=p.id AND (org_id=NEW.org_id OR org_id IS NULL)
            ORDER BY org_id NULLS LAST LIMIT 1) a ON true
        WHERE p.id=NEW.package_id AND p.skill_key=NEW.skill_key AND r.id=NEW.revision_id AND r.status='published'
            AND ((p.scope_kind='personal' AND p.owner_user_id=actor)
                OR (p.scope_kind='org' AND p.org_id=NEW.org_id AND a.enabled AND a.revision_id=r.id)
                OR (p.scope_kind='platform' AND a.enabled AND a.revision_id=r.id))
    ) THEN
        RAISE EXCEPTION 'SKILL_BINDING_REVISION_UNAVAILABLE' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.conversation_skill_bindings
            WHERE conversation_id=NEW.conversation_id AND skill_key=NEW.skill_key)
        AND (SELECT count(*) FROM public.conversation_skill_bindings WHERE conversation_id=NEW.conversation_id) >= 4 THEN
        RAISE EXCEPTION 'SKILL_BINDING_LIMIT' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
RESET ROLE;
