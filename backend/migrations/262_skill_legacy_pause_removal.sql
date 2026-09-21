-- Read the immutable package timestamp under its existing forced RLS policy.
-- A separate function restores row_security=off before the task scan below;
-- hidden task rows must still fail closed rather than become a false zero.
CREATE FUNCTION public.skill_deletion_package_created_at(target_package UUID, target_key TEXT)
RETURNS TIMESTAMPTZ LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = pg_catalog, public SET row_security = on AS $$
    SELECT p.created_at FROM public.skill_packages p
    WHERE p.id = target_package AND p.skill_key = target_key
        AND p.scope_kind = 'org'
        AND p.org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        AND current_setting('app.access_kind', true) = 'runtime_admin'
        AND NULLIF(current_setting('app.actor_user_id', true), '') IS NOT NULL
$$;
REVOKE ALL ON FUNCTION public.skill_deletion_package_created_at(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_deletion_package_created_at(UUID, TEXT) TO everydayai;

CREATE OR REPLACE FUNCTION public.skill_deletion_blockers(target_package UUID, target_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public SET row_security = off AS $$
DECLARE result JSONB; package_created TIMESTAMPTZ;
BEGIN
    IF NOT coalesce((current_setting('app.access_kind', true) = 'runtime_admin'
        AND NULLIF(current_setting('app.org_id', true), '') IS NOT NULL
        AND NULLIF(current_setting('app.actor_user_id', true), '') IS NOT NULL), false) THEN
        RAISE EXCEPTION 'SKILL_CONTROL_ACCESS_REQUIRED' USING ERRCODE = '42501';
    END IF;
    package_created := public.skill_deletion_package_created_at(target_package, target_key);
    IF package_created IS NULL THEN
        RAISE EXCEPTION 'SKILL_PACKAGE_UNAVAILABLE' USING ERRCODE = '42501';
    END IF;
    WITH candidates AS (
        SELECT t.status, t.turn_id, t.request_params,
            CASE WHEN c.status IN ('ready','paused') THEN
                CASE WHEN jsonb_typeof(c.state->'payload') = 'object'
                    THEN c.state->'payload'->'skill_runtime' ELSE c.state->'skill_runtime' END
                END AS runtime,
            c.task_id IS NOT NULL AS has_checkpoint,
            -- Only a still-paused, matching snapshot from before the immutable
            -- package existed can prove that a missing Skill record is legacy.
            -- Running/resumed, malformed and newer snapshots remain uncertain.
            coalesce((t.status = 'paused' AND c.status = 'paused'
                AND c.turn_id = t.turn_id AND c.updated_at < package_created
                AND jsonb_typeof(t.request_params) = 'object'
                AND NOT (t.request_params ? '_selected_skill')
                AND jsonb_typeof(c.state) = 'object'
                AND NOT (c.state ? 'skill_runtime')
                AND (NOT (c.state ? 'payload') OR (
                    jsonb_typeof(c.state->'payload') = 'object'
                    AND NOT (c.state->'payload' ? 'skill_runtime')))), false) AS legacy_before_package
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
            OR ((status <> 'pending' OR has_checkpoint) AND NOT legacy_before_package AND NOT coalesce((
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
