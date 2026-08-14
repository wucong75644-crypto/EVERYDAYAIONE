SET LOCAL ROLE everydayai_owner;

DROP TRIGGER IF EXISTS tasks_suspended_organization_fence ON tasks;
DROP TRIGGER IF EXISTS scheduled_tasks_suspended_organization_fence ON scheduled_tasks;
DROP TRIGGER IF EXISTS scheduled_task_runs_suspended_organization_fence ON scheduled_task_runs;
DROP TRIGGER IF EXISTS agent_runtime_sessions_suspended_organization_fence ON agent_runtime_sessions;
DROP TRIGGER IF EXISTS agent_session_commands_suspended_organization_fence ON agent_session_commands;
DROP TRIGGER IF EXISTS agent_runs_suspended_organization_fence ON agent_runs;
DROP TRIGGER IF EXISTS agent_run_attempts_suspended_organization_fence ON agent_run_attempts;
DROP TRIGGER IF EXISTS agent_model_steps_suspended_organization_fence ON agent_model_steps;
DROP TRIGGER IF EXISTS agent_runtime_events_suspended_organization_fence ON agent_runtime_events;
DROP TRIGGER IF EXISTS agent_projection_outbox_suspended_organization_fence ON agent_projection_outbox;
DROP TRIGGER IF EXISTS wecom_callback_inbox_suspended_organization_fence ON wecom_callback_inbox;
DROP TRIGGER IF EXISTS conversation_deliveries_suspended_organization_fence ON conversation_deliveries;
DROP FUNCTION IF EXISTS reject_suspended_organization_service_write();
DROP FUNCTION IF EXISTS reject_suspended_delivery_service_write();

CREATE OR REPLACE FUNCTION discover_generation_turn_candidates(
    p_limit INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_candidates JSONB;
BEGIN
    PERFORM public._assert_actor_worker_discovery_scope();
    IF p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'ACTOR_DISCOVERY_LIMIT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'task_id', candidate.id,
        'conversation_id', candidate.conversation_id,
        'execution_mode', candidate.execution_mode
    ) ORDER BY candidate.queue_sequence, candidate.id), '[]'::JSONB)
      INTO v_candidates
      FROM (
          SELECT task.id, task.conversation_id, task.execution_mode,
                 task.queue_sequence
            FROM public.tasks task
           WHERE task.type = 'chat'
             AND task.delivery_context @> '{"actor": true}'::JSONB
             AND task.status IN ('pending', 'running')
             AND task.conversation_id IS NOT NULL
             AND task.execution_mode IN ('serial', 'branch')
           ORDER BY task.queue_sequence, task.id
           LIMIT p_limit
      ) candidate;
    RETURN v_candidates;
END;
$$;

CREATE OR REPLACE FUNCTION worker_discover_media_tasks(
    p_limit INTEGER DEFAULT 100
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'MEDIA_WORKER_LIMIT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(task_row)), '[]'::JSONB)
      INTO v_tasks
      FROM (
          SELECT task.*
            FROM public.tasks task
           WHERE task.status IN ('pending', 'running')
             AND task.type IN ('image', 'video')
           ORDER BY COALESCE(task.last_polled_at, task.created_at), task.id
           LIMIT p_limit
      ) task_row;
    RETURN v_tasks;
END;
$$;

CREATE OR REPLACE FUNCTION worker_claim_due_scheduled_tasks(
    p_now TIMESTAMPTZ,
    p_limit INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_now IS NULL OR p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    WITH claimed AS (
        UPDATE public.scheduled_tasks
           SET status = 'running',
               next_run_at = NULL,
               updated_at = p_now
         WHERE id IN (
             SELECT id
               FROM public.scheduled_tasks
              WHERE status = 'active'
                AND next_run_at IS NOT NULL
                AND next_run_at <= p_now
              ORDER BY next_run_at
              LIMIT p_limit
              FOR UPDATE SKIP LOCKED
         )
         RETURNING *
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_tasks
      FROM claimed;
    RETURN v_tasks;
END;
$$;

RESET ROLE;
