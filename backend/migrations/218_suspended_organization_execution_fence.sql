-- 218: Fail closed when a service runtime touches suspended organization work.
-- Prerequisites: migration 217 and Worker/Agent Runtime migrations through 216.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION reject_suspended_organization_service_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org_id UUID;
BEGIN
    IF session_user NOT IN (
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker',
        'everydayai_sync'
    ) THEN
        RETURN NEW;
    END IF;
    v_org_id := NEW.org_id;
    IF v_org_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM public.organizations organization
         WHERE organization.id = v_org_id
           AND organization.status = 'active'
    ) THEN
        RAISE EXCEPTION 'ORGANIZATION_EXECUTION_SUSPENDED'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION reject_suspended_delivery_service_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user NOT IN (
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker'
    ) THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.tasks task
          JOIN public.organizations organization
            ON organization.id = task.org_id
         WHERE task.id = NEW.task_id
           AND organization.status <> 'active'
    ) THEN
        RAISE EXCEPTION 'ORGANIZATION_EXECUTION_SUSPENDED'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tasks_suspended_organization_fence
BEFORE INSERT OR UPDATE ON tasks
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER scheduled_tasks_suspended_organization_fence
BEFORE INSERT OR UPDATE ON scheduled_tasks
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER scheduled_task_runs_suspended_organization_fence
BEFORE INSERT OR UPDATE ON scheduled_task_runs
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER agent_runtime_sessions_suspended_organization_fence
BEFORE INSERT OR UPDATE ON agent_runtime_sessions
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER agent_session_commands_suspended_organization_fence
BEFORE INSERT OR UPDATE ON agent_session_commands
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER agent_runs_suspended_organization_fence
BEFORE INSERT OR UPDATE ON agent_runs
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER agent_run_attempts_suspended_organization_fence
BEFORE INSERT OR UPDATE ON agent_run_attempts
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER agent_model_steps_suspended_organization_fence
BEFORE INSERT OR UPDATE ON agent_model_steps
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER agent_runtime_events_suspended_organization_fence
BEFORE INSERT OR UPDATE ON agent_runtime_events
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER agent_projection_outbox_suspended_organization_fence
BEFORE INSERT OR UPDATE ON agent_projection_outbox
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER wecom_callback_inbox_suspended_organization_fence
BEFORE INSERT OR UPDATE ON wecom_callback_inbox
FOR EACH ROW EXECUTE FUNCTION reject_suspended_organization_service_write();
CREATE TRIGGER conversation_deliveries_suspended_organization_fence
BEFORE INSERT OR UPDATE ON conversation_deliveries
FOR EACH ROW EXECUTE FUNCTION reject_suspended_delivery_service_write();

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
             AND (
                 task.org_id IS NULL OR EXISTS (
                     SELECT 1 FROM public.organizations organization
                      WHERE organization.id = task.org_id
                        AND organization.status = 'active'
                 )
             )
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
             AND (
                 task.org_id IS NULL OR EXISTS (
                     SELECT 1 FROM public.organizations organization
                      WHERE organization.id = task.org_id
                        AND organization.status = 'active'
                 )
             )
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
        UPDATE public.scheduled_tasks task
           SET status = 'running', next_run_at = NULL, updated_at = p_now
         WHERE task.id IN (
             SELECT candidate.id
               FROM public.scheduled_tasks candidate
              WHERE candidate.status = 'active'
                AND candidate.next_run_at IS NOT NULL
                AND candidate.next_run_at <= p_now
                AND (
                    candidate.org_id IS NULL OR EXISTS (
                        SELECT 1 FROM public.organizations organization
                         WHERE organization.id = candidate.org_id
                           AND organization.status = 'active'
                    )
                )
              ORDER BY candidate.next_run_at
              LIMIT p_limit
              FOR UPDATE OF candidate SKIP LOCKED
         )
         RETURNING task.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_tasks FROM claimed;
    RETURN v_tasks;
END;
$$;

REVOKE ALL ON FUNCTION reject_suspended_organization_service_write()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION reject_suspended_delivery_service_write()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;
