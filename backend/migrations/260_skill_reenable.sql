-- Restore only states recorded by the current disable transaction. No row or
-- published content is rewritten by this migration; existing suspensions work.
CREATE FUNCTION public.skill_disabled_restore_state(
    target_package UUID, target_version BIGINT, target_revision UUID DEFAULT NULL
) RETURNS TEXT LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = pg_catalog, public AS $$
    SELECT CASE WHEN target_revision IS NULL THEN d.from_state ELSE (
        SELECT r.from_state FROM public.skill_change_audits r
        WHERE r.package_id = d.package_id AND r.entity = 'skill_revisions'
            AND r.action = 'disable' AND r.to_state = 'disabled'
            AND r.to_revision_id = target_revision
            AND r.request_id = d.request_id AND r.created_at = d.created_at
            AND r.from_state IN ('published', 'deprecated')
    ) END
    FROM public.skill_change_audits d
    WHERE d.package_id = target_package AND d.entity = 'skill_drafts'
        AND d.draft_version = target_version AND d.action = 'disable'
        AND d.to_state = 'disabled'
        AND d.from_state IN ('draft', 'in_review', 'published', 'deprecated')
$$;
REVOKE ALL ON FUNCTION public.skill_disabled_restore_state(UUID, BIGINT, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_disabled_restore_state(UUID, BIGINT, UUID) TO everydayai;

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
        IF TG_OP = 'UPDATE' AND NEW.status <> OLD.status AND NOT coalesce((
            (OLD.status = 'published' AND NEW.status IN ('deprecated', 'disabled', 'retired'))
            OR (OLD.status = 'deprecated' AND NEW.status IN ('disabled', 'retired'))
            OR (OLD.status = 'disabled' AND NEW.status IN ('published', 'deprecated')
                AND current_setting('app.skill_action', true) = 'enable'
                AND NEW.status = public.skill_disabled_restore_state(OLD.package_id,
                    (SELECT version FROM public.skill_drafts
                        WHERE package_id = OLD.package_id AND status = 'disabled'), OLD.id))
        ), false) THEN
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
        OR NOT coalesce(((OLD.status = 'draft' AND NEW.status IN ('draft', 'in_review', 'deprecated', 'disabled'))
            OR (OLD.status = 'in_review' AND NEW.status IN ('draft', 'in_review', 'published', 'deprecated', 'disabled'))
            OR (OLD.status = 'published' AND NEW.status IN ('draft', 'deprecated', 'disabled'))
            OR (OLD.status = 'deprecated' AND NEW.status = 'disabled')
            OR (OLD.status = 'disabled' AND current_setting('app.skill_action', true) = 'enable'
                AND NEW.status = public.skill_disabled_restore_state(OLD.package_id, OLD.version))), false) THEN
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

