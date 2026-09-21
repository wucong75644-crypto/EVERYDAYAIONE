-- Mutable working copies are separate from immutable publications.
ALTER TABLE public.skill_revisions DROP CONSTRAINT skill_revisions_status_check;
ALTER TABLE public.skill_revisions ADD CONSTRAINT skill_revisions_status_check
    CHECK (status IN ('published', 'deprecated', 'disabled', 'retired'));

CREATE TABLE public.skill_drafts (
    package_id UUID PRIMARY KEY REFERENCES public.skill_packages(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'in_review', 'published', 'deprecated', 'disabled')),
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    revision TEXT NOT NULL CHECK (revision ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
    content JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    approved_by UUID,
    approved_sha256 TEXT CHECK (approved_sha256 ~ '^[a-f0-9]{64}$'),
    approved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((approved_by IS NULL AND approved_sha256 IS NULL AND approved_at IS NULL)
        OR (approved_by IS NOT NULL AND approved_sha256 IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE TABLE public.skill_change_audits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id UUID NOT NULL REFERENCES public.skill_packages(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES public.organizations(id) ON DELETE RESTRICT,
    actor_user_id UUID,
    request_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    action TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    draft_version BIGINT,
    revision TEXT,
    from_revision_id UUID,
    to_revision_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX skill_change_audits_package_time
    ON public.skill_change_audits(package_id, created_at, id);

CREATE FUNCTION public.skill_authoring_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE d public.skill_drafts%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'skill_change_audits' THEN
        IF TG_OP <> 'INSERT' OR pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'SKILL_AUDIT_IMMUTABLE' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'skill_revisions' THEN
        IF TG_OP = 'UPDATE' AND NEW.status <> OLD.status AND NOT (
            (OLD.status = 'published' AND NEW.status IN ('deprecated', 'disabled', 'retired'))
            OR (OLD.status = 'deprecated' AND NEW.status IN ('disabled', 'retired'))
        ) THEN
            RAISE EXCEPTION 'SKILL_TRANSITION_INVALID' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT * INTO d FROM public.skill_drafts WHERE package_id = NEW.package_id FOR UPDATE;
            IF FOUND AND (d.status <> 'in_review' OR d.approved_by IS NULL
                OR d.revision <> NEW.revision OR d.approved_sha256 <> NEW.content_sha256) THEN
                RAISE EXCEPTION 'SKILL_APPROVAL_REQUIRED' USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'SKILL_DRAFT_DELETE_FORBIDDEN' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.version <> 1 OR NEW.approved_by IS NOT NULL THEN
            RAISE EXCEPTION 'SKILL_TRANSITION_INVALID' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.package_id <> OLD.package_id OR NEW.version <> OLD.version + 1
        OR NOT ((OLD.status = 'draft' AND NEW.status IN ('draft', 'in_review', 'deprecated', 'disabled'))
            OR (OLD.status = 'in_review' AND NEW.status IN ('draft', 'in_review', 'published', 'deprecated', 'disabled'))
            OR (OLD.status = 'published' AND NEW.status IN ('draft', 'deprecated', 'disabled'))
            OR (OLD.status = 'deprecated' AND NEW.status = 'disabled')) THEN
        RAISE EXCEPTION 'SKILL_TRANSITION_INVALID' USING ERRCODE = '23514';
    END IF;
    IF (NEW.content, NEW.revision) IS DISTINCT FROM (OLD.content, OLD.revision)
        AND NOT (NEW.status = 'draft' AND OLD.status IN ('draft', 'published')) THEN
        RAISE EXCEPTION 'SKILL_REVIEW_CONTENT_FROZEN' USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'draft' AND NEW.approved_by IS NOT NULL THEN
        RAISE EXCEPTION 'SKILL_APPROVAL_INVALID' USING ERRCODE = '23514';
    END IF;
    IF NEW.approved_by IS NOT NULL AND (NEW.approved_by, NEW.approved_sha256, NEW.approved_at)
        IS DISTINCT FROM (OLD.approved_by, OLD.approved_sha256, OLD.approved_at) THEN
        IF OLD.status <> 'in_review' OR NEW.status <> 'in_review' OR OLD.approved_by IS NOT NULL
            OR NEW.approved_by IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid THEN
            RAISE EXCEPTION 'SKILL_APPROVAL_INVALID' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.status = 'published' AND (OLD.approved_by IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.skill_revisions r WHERE r.package_id = NEW.package_id
            AND r.revision = NEW.revision AND r.content_sha256 = OLD.approved_sha256
            AND r.status = 'published'
    )) THEN
        RAISE EXCEPTION 'SKILL_APPROVAL_REQUIRED' USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

CREATE FUNCTION public.skill_record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE old_data JSONB; new_data JSONB; pid UUID; owner_org UUID;
BEGIN
    new_data := to_jsonb(NEW);
    old_data := CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
    IF TG_TABLE_NAME = 'skill_assignments' AND TG_OP = 'UPDATE'
       AND (new_data - 'updated_at') = (old_data - 'updated_at') THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME = 'skill_revisions' AND TG_OP = 'UPDATE'
       AND new_data->>'status' = old_data->>'status' THEN RETURN NEW; END IF;
    pid := (CASE WHEN TG_TABLE_NAME = 'skill_packages' THEN new_data->>'id' ELSE new_data->>'package_id' END)::uuid;
    SELECT org_id INTO owner_org FROM public.skill_packages WHERE id = pid;
    IF TG_TABLE_NAME = 'skill_assignments' THEN owner_org := (new_data->>'org_id')::uuid; END IF;
    INSERT INTO public.skill_change_audits(package_id, org_id, actor_user_id, request_id,
        entity, action, from_state, to_state, draft_version, revision, from_revision_id, to_revision_id)
    VALUES (pid, owner_org, NULLIF(current_setting('app.actor_user_id', true), '')::uuid,
        coalesce(current_setting('app.request_id', true), ''), TG_TABLE_NAME,
        coalesce(NULLIF(current_setting('app.skill_action', true), ''), lower(TG_OP)),
        coalesce(old_data->>'status', old_data->>'enabled'),
        coalesce(new_data->>'status', new_data->>'enabled'),
        (new_data->>'version')::bigint, new_data->>'revision',
        (old_data->>'revision_id')::uuid,
        (CASE WHEN TG_TABLE_NAME = 'skill_revisions' THEN new_data->>'id'
            ELSE new_data->>'revision_id' END)::uuid);
    RETURN NEW;
END $$;

CREATE TRIGGER skill_drafts_guard BEFORE INSERT OR UPDATE OR DELETE ON public.skill_drafts
    FOR EACH ROW EXECUTE FUNCTION public.skill_authoring_guard();
CREATE TRIGGER skill_revision_lifecycle BEFORE INSERT OR UPDATE ON public.skill_revisions
    FOR EACH ROW EXECUTE FUNCTION public.skill_authoring_guard();
CREATE TRIGGER skill_change_audits_guard BEFORE INSERT OR UPDATE OR DELETE ON public.skill_change_audits
    FOR EACH ROW EXECUTE FUNCTION public.skill_authoring_guard();
CREATE TRIGGER skill_packages_audit AFTER INSERT ON public.skill_packages
    FOR EACH ROW EXECUTE FUNCTION public.skill_record_change();
CREATE TRIGGER skill_revisions_audit AFTER INSERT OR UPDATE ON public.skill_revisions
    FOR EACH ROW EXECUTE FUNCTION public.skill_record_change();
CREATE TRIGGER skill_assignments_audit AFTER INSERT OR UPDATE ON public.skill_assignments
    FOR EACH ROW EXECUTE FUNCTION public.skill_record_change();
CREATE TRIGGER skill_drafts_audit AFTER INSERT OR UPDATE ON public.skill_drafts
    FOR EACH ROW EXECUTE FUNCTION public.skill_record_change();

ALTER TABLE public.skill_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_drafts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_change_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_change_audits FORCE ROW LEVEL SECURITY;
CREATE POLICY skill_drafts_owner ON public.skill_drafts FOR ALL TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id
        AND p.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()))
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id
        AND p.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()));
CREATE POLICY skill_change_audits_read ON public.skill_change_audits FOR SELECT TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin'
        AND org_id IS NOT DISTINCT FROM public.skill_catalog_org_id());
CREATE POLICY skill_change_audits_insert ON public.skill_change_audits FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()
        AND actor_user_id IS NOT DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid);
REVOKE ALL ON public.skill_drafts, public.skill_change_audits FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON public.skill_drafts TO everydayai;
GRANT SELECT, INSERT ON public.skill_change_audits TO everydayai;
REVOKE ALL ON FUNCTION public.skill_authoring_guard(), public.skill_record_change() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_authoring_guard(), public.skill_record_change() TO everydayai;
