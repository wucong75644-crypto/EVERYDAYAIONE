-- Never make removed Skills visible again by dropping their tombstones.
SET LOCAL row_security = off;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.skill_drafts WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'SKILL_REMOVAL_ROLLBACK_REQUIRES_FORWARD_FIX';
    END IF;
END $$;
DROP TRIGGER skill_assignment_lifecycle ON public.skill_assignments;
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

CREATE OR REPLACE FUNCTION public.skill_record_change() RETURNS trigger
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


DROP FUNCTION public.skill_deletion_blockers(UUID, TEXT);
ALTER TABLE public.skill_drafts DROP CONSTRAINT skill_deleted_metadata_check,
    DROP COLUMN deleted_at, DROP COLUMN deleted_by;
