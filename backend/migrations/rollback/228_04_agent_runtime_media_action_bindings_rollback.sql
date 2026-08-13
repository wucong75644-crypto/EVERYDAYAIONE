-- Roll back 228.04 only when no Runtime media binding has been created.
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BINDINGS_IN_USE'
            USING ERRCODE = '55000';
    END IF;
END $$;

DROP FUNCTION refund_agent_runtime_media_credit_v1(UUID,BIGINT);
DROP FUNCTION settle_agent_runtime_media_credit_v1(UUID,BIGINT);
DROP FUNCTION read_agent_runtime_media_binding_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION prepare_agent_runtime_media_batch_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT);
DROP FUNCTION read_agent_runtime_media_manifest_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION _agent_runtime_media_attempt_valid_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION _agent_runtime_media_input_manifest_v1(TEXT);
DROP FUNCTION _agent_runtime_media_projection_v1();
DROP FUNCTION _agent_runtime_media_worker_v1();

DROP TABLE agent_runtime_media_action_bindings;
DROP TRIGGER agent_runtime_media_pricing_immutable
    ON agent_runtime_media_pricing_facts;
DROP FUNCTION _agent_runtime_media_pricing_immutable_v1();
DROP TABLE agent_runtime_media_pricing_facts;

-- Restore the immediately preceding 171 capability as amended by migration 218.
CREATE OR REPLACE FUNCTION worker_discover_media_tasks(
    p_limit INTEGER DEFAULT 100
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_tasks JSONB;
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
END $$;

REVOKE ALL ON FUNCTION worker_discover_media_tasks(INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_agent_runtime_worker, everydayai_projection_worker,
    everydayai_authorization_worker, everydayai_sandbox_worker,
    everydayai_sync, everydayai, everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION worker_discover_media_tasks(INTEGER)
TO everydayai_worker;

RESET ROLE;
