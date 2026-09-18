-- Skill control plane only. No existing tables, runtime functions or roles change.
CREATE TABLE public.skill_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_key TEXT NOT NULL CHECK (skill_key ~ '^[a-z][a-z0-9_-]{0,63}$'),
    source TEXT NOT NULL CHECK (length(btrim(source)) BETWEEN 1 AND 200),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('platform', 'org')),
    org_id UUID REFERENCES public.organizations(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((scope_kind = 'platform' AND org_id IS NULL)
        OR (scope_kind = 'org' AND org_id IS NOT NULL))
);
CREATE UNIQUE INDEX skill_packages_platform_key ON public.skill_packages(skill_key)
    WHERE org_id IS NULL;
CREATE UNIQUE INDEX skill_packages_org_key ON public.skill_packages(org_id, skill_key)
    WHERE org_id IS NOT NULL;

CREATE TABLE public.skill_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id UUID NOT NULL REFERENCES public.skill_packages(id) ON DELETE RESTRICT,
    revision TEXT NOT NULL CHECK (revision ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
    nas_path TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    body_sha256 TEXT NOT NULL CHECK (body_sha256 ~ '^[a-f0-9]{64}$'),
    summary TEXT NOT NULL CHECK (length(btrim(summary)) BETWEEN 1 AND 2000),
    status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (package_id, revision),
    UNIQUE (package_id, id)
);

CREATE TABLE public.skill_assignments (
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    package_id UUID NOT NULL REFERENCES public.skill_packages(id) ON DELETE RESTRICT,
    revision_id UUID NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    priority INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, package_id),
    FOREIGN KEY (package_id, revision_id) REFERENCES public.skill_revisions(package_id, id)
        ON DELETE RESTRICT
);
CREATE INDEX skill_assignments_enabled ON public.skill_assignments(org_id, priority DESC, package_id)
    WHERE enabled;

CREATE TABLE public.skill_activation_audits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    package_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    actor_user_id UUID,
    conversation_id UUID,
    turn_id UUID,
    request_id VARCHAR(128) NOT NULL DEFAULT '',
    outcome TEXT NOT NULL CHECK (outcome IN ('activated', 'skipped', 'failed')),
    reason_code VARCHAR(100) NOT NULL CHECK (reason_code ~ '^[a-zA-Z0-9_.-]+$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (package_id, revision_id) REFERENCES public.skill_revisions(package_id, id)
        ON DELETE RESTRICT
);
CREATE INDEX skill_activation_audits_org_time ON public.skill_activation_audits(org_id, created_at DESC);
COMMENT ON TABLE public.skill_activation_audits IS
    'Reserved append-only activation facts; phase 1 does not emit events from chat or runtime';
COMMENT ON COLUMN public.skill_revisions.nas_path IS
    'Relative canonical SKILL.md path beneath platform-controlled SKILL_STORAGE_ROOT';

CREATE FUNCTION public.skill_catalog_org_id() RETURNS UUID
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::UUID $$;

CREATE FUNCTION public.skill_catalog_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
DECLARE
    package public.skill_packages%ROWTYPE;
    expected_path TEXT;
BEGIN
    IF TG_TABLE_NAME IN ('skill_packages', 'skill_activation_audits')
       AND TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'SKILL_RECORD_IMMUTABLE' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'skill_revisions' AND TG_OP <> 'INSERT' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'SKILL_REVISION_IMMUTABLE' USING ERRCODE = '23514';
        END IF;
        IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status')
           OR (OLD.status = 'retired' AND NEW.status <> 'retired') THEN
            RAISE EXCEPTION 'SKILL_REVISION_IMMUTABLE' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME IN ('skill_revisions', 'skill_assignments', 'skill_activation_audits') THEN
        SELECT * INTO package FROM public.skill_packages WHERE id = NEW.package_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'SKILL_PACKAGE_UNAVAILABLE' USING ERRCODE = '23514';
        END IF;
        IF TG_TABLE_NAME = 'skill_revisions' THEN
            expected_path := CASE WHEN package.scope_kind = 'platform' THEN 'platform'
                ELSE 'org/' || package.org_id::TEXT END
                || '/' || package.skill_key || '/' || NEW.revision || '/SKILL.md';
            IF NEW.nas_path <> expected_path THEN
                RAISE EXCEPTION 'SKILL_PATH_IDENTITY_MISMATCH' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF package.org_id IS NOT NULL AND package.org_id <> NEW.org_id THEN
                RAISE EXCEPTION 'SKILL_ORG_MISMATCH' USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'skill_assignments' THEN
                IF TG_OP = 'UPDATE' AND (NEW.org_id, NEW.package_id) IS DISTINCT FROM (OLD.org_id, OLD.package_id) THEN
                    RAISE EXCEPTION 'SKILL_ASSIGNMENT_IDENTITY_IMMUTABLE' USING ERRCODE = '23514';
                END IF;
                IF NEW.enabled AND NOT EXISTS (
                    SELECT 1 FROM public.skill_revisions
                    WHERE id = NEW.revision_id AND package_id = NEW.package_id AND status = 'published'
                ) THEN
                    RAISE EXCEPTION 'SKILL_REVISION_UNAVAILABLE' USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := now();
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER skill_packages_immutable BEFORE UPDATE OR DELETE ON public.skill_packages
    FOR EACH ROW EXECUTE FUNCTION public.skill_catalog_guard();
CREATE TRIGGER skill_revisions_guard BEFORE INSERT OR UPDATE OR DELETE ON public.skill_revisions
    FOR EACH ROW EXECUTE FUNCTION public.skill_catalog_guard();
CREATE TRIGGER skill_assignments_guard BEFORE INSERT OR UPDATE ON public.skill_assignments
    FOR EACH ROW EXECUTE FUNCTION public.skill_catalog_guard();
CREATE TRIGGER skill_activation_audits_guard BEFORE INSERT OR UPDATE OR DELETE ON public.skill_activation_audits
    FOR EACH ROW EXECUTE FUNCTION public.skill_catalog_guard();

ALTER TABLE public.skill_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_packages FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_assignments FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_activation_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_activation_audits FORCE ROW LEVEL SECURITY;

-- Only the existing backend service role is granted access. DatabaseScope is a
-- trusted backend identity, not an API authorization mechanism. No runtime grants.
CREATE POLICY skill_packages_read ON public.skill_packages FOR SELECT TO everydayai
    USING (org_id IS NULL OR org_id = public.skill_catalog_org_id());
CREATE POLICY skill_packages_insert ON public.skill_packages FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND org_id IS NOT DISTINCT FROM public.skill_catalog_org_id());
CREATE POLICY skill_revisions_read ON public.skill_revisions FOR SELECT TO everydayai
    USING (EXISTS (SELECT 1 FROM public.skill_packages p WHERE p.id = package_id));
CREATE POLICY skill_revisions_insert ON public.skill_revisions FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id
            AND p.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()));
CREATE POLICY skill_revisions_update ON public.skill_revisions FOR UPDATE TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id
            AND p.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()))
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id
            AND p.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()));
CREATE POLICY skill_assignments_read ON public.skill_assignments FOR SELECT TO everydayai
    USING (org_id = public.skill_catalog_org_id());
CREATE POLICY skill_assignments_insert ON public.skill_assignments FOR INSERT TO everydayai
    WITH CHECK (org_id = public.skill_catalog_org_id()
        AND current_setting('app.access_kind', true) = 'runtime_admin');
CREATE POLICY skill_assignments_update ON public.skill_assignments FOR UPDATE TO everydayai
    USING (org_id = public.skill_catalog_org_id()
        AND current_setting('app.access_kind', true) = 'runtime_admin')
    WITH CHECK (org_id = public.skill_catalog_org_id()
        AND current_setting('app.access_kind', true) = 'runtime_admin');
CREATE POLICY skill_audits_read ON public.skill_activation_audits FOR SELECT TO everydayai
    USING (org_id = public.skill_catalog_org_id());
CREATE POLICY skill_audits_insert ON public.skill_activation_audits FOR INSERT TO everydayai
    WITH CHECK (org_id = public.skill_catalog_org_id()
        AND actor_user_id IS NOT DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::UUID
        AND current_setting('app.access_kind', true) = 'runtime_admin');

REVOKE ALL ON public.skill_packages, public.skill_revisions,
    public.skill_assignments, public.skill_activation_audits FROM PUBLIC;
GRANT SELECT, INSERT ON public.skill_packages, public.skill_activation_audits TO everydayai;
GRANT SELECT, INSERT, UPDATE ON public.skill_revisions, public.skill_assignments TO everydayai;
REVOKE ALL ON FUNCTION public.skill_catalog_org_id(), public.skill_catalog_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_catalog_org_id(), public.skill_catalog_guard() TO everydayai;
