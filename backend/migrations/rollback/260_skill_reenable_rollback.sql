-- Restore the previous guard without deleting drafts, versions or audit history.
CREATE OR REPLACE FUNCTION public.skill_authoring_guard() RETURNS trigger
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

DROP FUNCTION public.skill_disabled_restore_state(UUID, BIGINT, UUID);
