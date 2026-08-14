-- 227_59: Read-only historical scheduled-task Runtime adoption preflight.
-- It classifies facts without changing scheduled_tasks or creating Runtime rows.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION _agent_runtime_scheduled_adoption_target_shape(
    p_target JSONB, p_depth INTEGER DEFAULT 0
) RETURNS BOOLEAN
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE item JSONB;
BEGIN
    IF p_depth > 4 OR jsonb_typeof(p_target) IS DISTINCT FROM 'object' THEN
        RETURN FALSE;
    END IF;
    IF p_target->>'type' = 'web' THEN
        RETURN NULLIF(btrim(p_target->>'user_id'), '') IS NOT NULL;
    ELSIF p_target->>'type' IN ('wecom_group', 'wecom_user') THEN
        RETURN NULLIF(btrim(COALESCE(p_target->>'chatid', p_target->>'wecom_userid')), '') IS NOT NULL;
    ELSIF p_target->>'type' = 'multi' THEN
        IF jsonb_typeof(p_target->'targets') IS DISTINCT FROM 'array'
           OR jsonb_array_length(p_target->'targets') NOT BETWEEN 1 AND 20 THEN
            RETURN FALSE;
        END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(p_target->'targets') LOOP
            IF NOT _agent_runtime_scheduled_adoption_target_shape(item, p_depth + 1) THEN
                RETURN FALSE;
            END IF;
        END LOOP;
        RETURN TRUE;
    END IF;
    RETURN FALSE;
END;
$$;

CREATE FUNCTION read_agent_runtime_scheduled_adoption_plan_v1(
    p_org_id UUID DEFAULT NULL,
    p_include_inactive BOOLEAN DEFAULT TRUE
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER STABLE
SET search_path = pg_catalog, public AS $$
DECLARE result JSONB;
BEGIN
    IF current_user <> 'everydayai_owner' THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_PREFLIGHT_OWNER_REQUIRED'
            USING ERRCODE = '42501';
    END IF;

    WITH classified AS (
        SELECT
            task.id AS task_id,
            task.org_id,
            task.user_id,
            task.status,
            task.runtime_state_version,
            CASE
                WHEN profile.scheduled_task_id IS NOT NULL THEN 'runtime_owned'
                WHEN task.runtime_action_id IS NOT NULL
                  OR task.runtime_attempt_id IS NOT NULL
                  OR task.runtime_request_hash IS NOT NULL
                  OR task.runtime_idempotency_key IS NOT NULL
                    THEN 'blocked_partial_runtime_facts'
                WHEN task.status = 'running' THEN 'blocked_running'
                WHEN task.status = 'paused' THEN 'preserve_paused'
                WHEN task.status = 'error' THEN 'preserve_error'
                WHEN task.status <> 'active' THEN 'blocked_unknown_status'
                WHEN NOT (
                    NULLIF(btrim(task.name), '') IS NOT NULL
                    AND NULLIF(task.prompt, '') IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM pg_timezone_names zone
                        WHERE zone.name = task.timezone
                    )
                    AND task.schedule_type IN ('once', 'daily', 'weekly', 'monthly', 'cron')
                    AND (
                        (task.schedule_type = 'once' AND task.run_at IS NOT NULL)
                        OR (task.schedule_type <> 'once'
                            AND NULLIF(btrim(task.cron_expr), '') IS NOT NULL)
                    )
                    AND task.next_run_at IS NOT NULL
                    AND _agent_runtime_scheduled_adoption_target_shape(task.push_target)
                ) THEN 'blocked_invalid_task'
                ELSE 'candidate_runtime_source_required'
            END AS category,
            encode(digest(convert_to((jsonb_build_object(
                'id', task.id, 'org_id', task.org_id, 'user_id', task.user_id,
                'name', task.name, 'prompt', task.prompt, 'timezone', task.timezone,
                'push_target', task.push_target, 'template_file', task.template_file,
                'max_credits', task.max_credits, 'retry_count', task.retry_count,
                'timeout_sec', task.timeout_sec, 'schedule_type', task.schedule_type,
                'cron_expr', task.cron_expr, 'run_at', task.run_at,
                'weekdays', task.weekdays, 'day_of_month', task.day_of_month,
                'next_run_at', task.next_run_at, 'last_summary', task.last_summary
            ))::TEXT, 'UTF8'), 'sha256'), 'hex') AS task_semantics_hash,
            encode(digest(convert_to(task.push_target::TEXT, 'UTF8'), 'sha256'), 'hex')
                AS delivery_target_hash
        FROM scheduled_tasks task
        LEFT JOIN agent_runtime_scheduled_execution_profiles profile
          ON profile.scheduled_task_id = task.id
        WHERE (p_org_id IS NULL OR task.org_id = p_org_id)
          AND (p_include_inactive OR task.status IN ('active', 'running'))
    ),
    with_reasons AS (
        SELECT classified.*,
            CASE category
                WHEN 'runtime_owned' THEN jsonb_build_array('runtime_profile_exists')
                WHEN 'blocked_partial_runtime_facts' THEN jsonb_build_array(
                    'partial_runtime_identity_without_profile')
                WHEN 'blocked_running' THEN jsonb_build_array('task_is_in_flight')
                WHEN 'preserve_paused' THEN jsonb_build_array('task_is_paused')
                WHEN 'preserve_error' THEN jsonb_build_array('task_is_error')
                WHEN 'blocked_unknown_status' THEN jsonb_build_array('unknown_task_status')
                WHEN 'blocked_invalid_task' THEN jsonb_build_array('task_shape_or_schedule_invalid')
                ELSE jsonb_build_array(
                    'runtime_source_action_attempt_run_missing',
                    'delivery_target_requires_scope_recheck')
            END AS reason_codes
        FROM classified
    )
    SELECT jsonb_build_object(
        'outcome', 'dry_run',
        'plan_version', 'scheduled-runtime-adoption-v1',
        'total_tasks', (SELECT count(*) FROM with_reasons),
        'counts', COALESCE((
            SELECT jsonb_object_agg(category, count ORDER BY category)
            FROM (SELECT category, count(*) FROM with_reasons GROUP BY category) counts
        ), '{}'::JSONB),
        'adoption_candidate_count', (
            SELECT count(*) FROM with_reasons
            WHERE category = 'candidate_runtime_source_required'
        ),
        'safe_to_adopt_count', 0,
        'tasks', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'task_id', task_id, 'org_id', org_id, 'user_id', user_id,
                'status', status, 'category', category,
                'task_state_version', runtime_state_version,
                'reason_codes', reason_codes,
                'task_semantics_hash', task_semantics_hash,
                'delivery_target_hash', delivery_target_hash,
                'adoption_candidate', category = 'candidate_runtime_source_required',
                'safe_to_adopt', FALSE
            ) ORDER BY task_id)
            FROM with_reasons
        ), '[]'::JSONB)
    ) INTO result;
    RETURN result;
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_runtime_scheduled_adoption_target_shape(JSONB, INTEGER),
    read_agent_runtime_scheduled_adoption_plan_v1(UUID, BOOLEAN)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
    everydayai_sync, everydayai;

RESET ROLE;
