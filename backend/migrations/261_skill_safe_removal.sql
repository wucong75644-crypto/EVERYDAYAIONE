-- Removal retains immutable revisions, grants and audit facts. No NAS changes.
ALTER TABLE public.skill_drafts ADD COLUMN deleted_at TIMESTAMPTZ, ADD COLUMN deleted_by UUID;
ALTER TABLE public.skill_drafts ADD CONSTRAINT skill_deleted_metadata_check
    CHECK ((deleted_at IS NULL AND deleted_by IS NULL)
        OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL AND status = 'deprecated'));

-- RLS filtering must never turn hidden task references into a false zero. Do
-- not read Skill tables here: they deliberately FORCE RLS. Callers validate
-- ownership first; all task counts are still limited to the trusted org scope.
CREATE FUNCTION public.skill_deletion_blockers(target_package UUID, target_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public SET row_security = off AS $$
DECLARE result JSONB;
BEGIN
    IF NOT coalesce((current_setting('app.access_kind', true) = 'runtime_admin'
        AND NULLIF(current_setting('app.org_id', true), '') IS NOT NULL
        AND NULLIF(current_setting('app.actor_user_id', true), '') IS NOT NULL), false) THEN
        RAISE EXCEPTION 'SKILL_CONTROL_ACCESS_REQUIRED' USING ERRCODE = '42501';
    END IF;
    WITH candidates AS (
        SELECT t.status, t.turn_id, t.request_params,
            CASE WHEN c.status IN ('ready','paused') THEN
                CASE WHEN jsonb_typeof(c.state->'payload') = 'object'
                    THEN c.state->'payload'->'skill_runtime' ELSE c.state->'skill_runtime' END
                END AS runtime,
            c.task_id IS NOT NULL AS has_checkpoint
        FROM public.tasks t
        LEFT JOIN public.conversation_turn_checkpoints c ON c.task_id = t.id
        WHERE t.org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
            AND t.type = 'chat' AND t.status NOT IN ('completed','failed','cancelled')
    ), shapes AS (
        SELECT *,
            CASE WHEN jsonb_typeof(runtime->'directory') = 'array' THEN runtime->'directory' ELSE '[]'::jsonb END AS directory,
            CASE WHEN jsonb_typeof(runtime->'active') = 'array' THEN runtime->'active' ELSE '[]'::jsonb END AS active
        FROM candidates
    ), dependencies AS (
        SELECT
            request_params #>> '{_selected_skill,skill_id}' = target_key
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(directory) entry
                WHERE entry->>'package_id' = target_package::text)
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(active) entry
                WHERE entry->>'skill_key' = target_key) AS referenced,
            -- Serialized legacy params and incomplete replay state are unknown,
            -- never evidence that deletion is safe. Pending fresh chats with no
            -- selection have not resolved a Skill and cannot activate deprecated revisions.
            (request_params IS NOT NULL AND jsonb_typeof(request_params) <> 'object')
            OR (request_params ? '_selected_skill' AND NOT coalesce((
                jsonb_typeof(request_params->'_selected_skill') = 'object'
                AND jsonb_typeof(request_params #> '{_selected_skill,skill_id}') = 'string'
                AND jsonb_typeof(request_params #> '{_selected_skill,revision}') = 'string'), false))
            OR ((status <> 'pending' OR has_checkpoint) AND NOT coalesce((
                jsonb_typeof(runtime) = 'object' AND runtime->>'version' = '1'
                AND runtime->>'turn_id' = turn_id::text
                AND jsonb_typeof(runtime->'directory') = 'array'
                AND jsonb_typeof(runtime->'active') = 'array'
                AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(directory) entry
                    WHERE NOT coalesce((jsonb_typeof(entry->'package_id') = 'string'
                        AND entry->>'package_id' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
                        AND entry->>'skill_key' ~ '^[a-z][a-z0-9_-]{0,63}$'
                        AND entry->>'revision' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
                        AND jsonb_typeof(entry->'skill_key') = 'string'
                        AND jsonb_typeof(entry->'revision') = 'string'), false))
                AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(active) entry
                    WHERE NOT coalesce((jsonb_typeof(entry->'skill_key') = 'string'
                        AND jsonb_typeof(entry->'revision') = 'string'), false)
                        OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(directory) candidate
                            WHERE candidate->>'skill_key' = entry->>'skill_key'
                                AND candidate->>'revision' = entry->>'revision'))
            ), false)) AS uncertain
        FROM shapes
    )
    SELECT jsonb_build_object('blocking_tasks', count(*) FILTER (WHERE referenced),
        'uncertain_tasks', count(*) FILTER (WHERE uncertain)) INTO result FROM dependencies;
    RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.skill_deletion_blockers(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_deletion_blockers(UUID, TEXT) TO everydayai;

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
    IF TG_TABLE_NAME IN ('skill_revisions', 'skill_assignments') THEN
        SELECT * INTO d FROM public.skill_drafts WHERE package_id = NEW.package_id FOR UPDATE;
        IF FOUND AND d.deleted_at IS NOT NULL THEN
            RAISE EXCEPTION 'SKILL_PACKAGE_DELETED' USING ERRCODE = '23514';
        END IF;
        IF TG_TABLE_NAME = 'skill_assignments' THEN RETURN NEW; END IF;
    END IF;
    IF TG_TABLE_NAME = 'skill_revisions' THEN
        IF TG_OP = 'UPDATE' AND NEW.status <> OLD.status AND NOT coalesce((
            (OLD.status = 'published' AND NEW.status IN ('deprecated', 'disabled', 'retired'))
            OR (OLD.status = 'deprecated' AND NEW.status IN ('disabled', 'retired'))
            OR (OLD.status = 'disabled' AND NEW.status IN ('published', 'deprecated')
                AND ((current_setting('app.skill_action', true) = 'enable'
                    AND NEW.status = public.skill_disabled_restore_state(OLD.package_id,
                        (SELECT version FROM public.skill_drafts
                            WHERE package_id = OLD.package_id AND status = 'disabled'), OLD.id))
                OR (current_setting('app.skill_action', true) = 'deprecate' AND NEW.status = 'deprecated'
                    AND public.skill_disabled_restore_state(OLD.package_id,
                        (SELECT version FROM public.skill_drafts
                            WHERE package_id = OLD.package_id AND status = 'disabled'), OLD.id)
                        IN ('published', 'deprecated'))))
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
        IF NEW.status <> 'draft' OR NEW.version <> 1 OR NEW.approved_by IS NOT NULL
            OR NEW.deleted_at IS NOT NULL OR NEW.deleted_by IS NOT NULL THEN
            RAISE EXCEPTION 'SKILL_TRANSITION_INVALID' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'SKILL_PACKAGE_DELETED' USING ERRCODE = '23514';
    END IF;
    IF (NEW.deleted_at, NEW.deleted_by) IS DISTINCT FROM (OLD.deleted_at, OLD.deleted_by) THEN
        IF NOT coalesce((current_setting('app.skill_action', true) = 'delete'
            AND OLD.status = 'deprecated' AND NEW.status = 'deprecated'
            AND NEW.deleted_at = now()
            AND NEW.deleted_by = NULLIF(current_setting('app.actor_user_id', true), '')::uuid
            AND (to_jsonb(NEW) - ARRAY['deleted_at','deleted_by','version','updated_at']) =
                (to_jsonb(OLD) - ARRAY['deleted_at','deleted_by','version','updated_at'])), false) THEN
            RAISE EXCEPTION 'SKILL_TRANSITION_INVALID' USING ERRCODE = '23514';
        END IF;
        PERFORM set_config('lock_timeout', '2s', true);
        LOCK TABLE public.tasks, public.conversation_turn_checkpoints IN SHARE MODE;
        IF public.skill_deletion_blockers(OLD.package_id,
            (SELECT skill_key FROM public.skill_packages WHERE id = OLD.package_id))
            <> '{"blocking_tasks":0,"uncertain_tasks":0}'::jsonb THEN
            RAISE EXCEPTION 'SKILL_DELETE_IN_USE' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.package_id <> OLD.package_id OR NEW.version <> OLD.version + 1
        OR NOT coalesce(((OLD.status = 'draft' AND NEW.status IN ('draft', 'in_review', 'deprecated', 'disabled'))
            OR (OLD.status = 'in_review' AND NEW.status IN ('draft', 'in_review', 'published', 'deprecated', 'disabled'))
            OR (OLD.status = 'published' AND NEW.status IN ('draft', 'deprecated', 'disabled'))
            OR (OLD.status = 'deprecated' AND NEW.status = 'deprecated' AND NEW.deleted_at IS NOT NULL)
            OR (OLD.status = 'disabled' AND current_setting('app.skill_action', true) = 'enable'
                AND NEW.status = public.skill_disabled_restore_state(OLD.package_id, OLD.version))
            OR (OLD.status = 'disabled' AND NEW.status = 'deprecated'
                AND current_setting('app.skill_action', true) = 'deprecate'
                AND public.skill_disabled_restore_state(OLD.package_id, OLD.version)
                    IN ('draft', 'in_review', 'published', 'deprecated'))), false) THEN
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
        CASE WHEN new_data->>'deleted_at' IS NOT NULL THEN 'deleted'
            ELSE coalesce(new_data->>'status', new_data->>'enabled') END,
        (new_data->>'version')::bigint, new_data->>'revision',
        (old_data->>'revision_id')::uuid,
        (CASE WHEN TG_TABLE_NAME = 'skill_revisions' THEN new_data->>'id'
            ELSE new_data->>'revision_id' END)::uuid);
    RETURN NEW;
END $$;

CREATE TRIGGER skill_assignment_lifecycle BEFORE INSERT OR UPDATE ON public.skill_assignments
    FOR EACH ROW EXECUTE FUNCTION public.skill_authoring_guard();
