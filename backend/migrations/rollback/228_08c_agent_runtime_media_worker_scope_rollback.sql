SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION worker_discover_media_tasks(p_limit INTEGER DEFAULT 100) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ DECLARE discovered JSONB;
BEGIN IF session_user<>'everydayai_worker' THEN RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE='42501';
END IF;
IF p_limit IS NULL OR p_limit<1 OR p_limit>500 THEN RAISE EXCEPTION 'MEDIA_WORKER_LIMIT_INVALID' USING ERRCODE='22023';
END IF;
SELECT COALESCE(jsonb_agg(to_jsonb(task_row)),'[]'::JSONB) INTO discovered FROM (SELECT task.* FROM tasks task WHERE task.status IN ('pending','running') AND task.type IN ('image','video') AND NOT EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding WHERE binding.task_id=task.id) AND NOT EXISTS (SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding WHERE binding.task_id=task.id) AND (task.org_id IS NULL OR EXISTS (SELECT 1 FROM organizations organization WHERE organization.id=task.org_id AND organization.status='active')) ORDER BY COALESCE(task.last_polled_at,task.created_at),task.id LIMIT p_limit ) task_row;
RETURN discovered;
END;
$$;

REVOKE ALL ON FUNCTION worker_discover_media_tasks(INTEGER) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime, everydayai_agent_runtime_worker,everydayai_projection_worker, everydayai_authorization_worker,everydayai_sandbox_worker, everydayai_sync,everydayai,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION worker_discover_media_tasks(INTEGER) TO everydayai_worker;

RESET ROLE;
