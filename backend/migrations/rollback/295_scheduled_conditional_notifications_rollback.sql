-- Use the existing scheduler table owner; production scheduler objects predate
-- everydayai_owner. Do not change ownership or broaden application grants.
DO $$ DECLARE scheduler_owner TEXT; BEGIN
 SELECT pg_get_userbyid(relowner) INTO STRICT scheduler_owner
 FROM pg_class WHERE oid='public.scheduled_tasks'::regclass;
 EXECUTE format('SET LOCAL ROLE %I', scheduler_owner);
END $$;
-- Roll back application code first only after draining new-style task execution.
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM scheduled_task_deliveries WHERE target_context->>'type'='web' AND status IN ('pending','delivering')) THEN
  RAISE EXCEPTION 'DRAIN_WEB_DELIVERIES_BEFORE_ROLLBACK';
 END IF;
 IF EXISTS (SELECT 1 FROM scheduled_tasks WHERE (notification_policy->>'mode'='conditional' OR origin_conversation_id IS NOT NULL) AND schedule_enabled) THEN
  RAISE EXCEPTION 'PAUSE_CONDITIONAL_TASKS_BEFORE_ROLLBACK';
 END IF;
END $$;

CREATE OR REPLACE FUNCTION complete_scheduled_task_success(
    p_task_id UUID,
    p_run_id UUID,
    p_next_status TEXT,
    p_next_run_at TIMESTAMPTZ,
    p_last_summary TEXT,
    p_last_result JSONB,
    p_credits_used INTEGER,
    p_tokens_used INTEGER,
    p_duration_ms INTEGER,
    p_deliveries JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task scheduled_tasks%ROWTYPE;
    v_run scheduled_task_runs%ROWTYPE;
    v_delivery JSONB;
    v_push_status TEXT;
BEGIN
    IF p_next_status NOT IN ('active', 'paused', 'error')
       OR p_last_result IS NULL OR jsonb_typeof(p_last_result) <> 'object'
       OR p_deliveries IS NULL OR jsonb_typeof(p_deliveries) <> 'array'
       OR p_credits_used < 0 OR p_tokens_used < 0 OR p_duration_ms < 0 THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_SUCCESS_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    -- All lifecycle operations lock task before run, including recovery.
    SELECT * INTO v_task FROM scheduled_tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome','task_missing');
    END IF;
    SELECT * INTO v_run FROM scheduled_task_runs
      WHERE id=p_run_id AND task_id=p_task_id AND org_id=v_task.org_id FOR UPDATE;
    IF NOT FOUND OR v_run.status <> 'running'
       OR (v_task.run_token IS NOT NULL AND v_task.run_token <> p_run_id) THEN
        RETURN jsonb_build_object('outcome','run_not_running');
    END IF;
    IF NOT v_task.schedule_enabled THEN
        p_next_status := CASE WHEN v_task.run_previous_status='error' THEN 'error' ELSE 'paused' END;
        p_next_run_at := NULL;
    ELSIF v_task.schedule_type='once' THEN
        p_next_status := 'paused';
        p_next_run_at := NULL;
    ELSE
        p_next_status := 'active';
    END IF;

    FOR v_delivery IN SELECT value FROM jsonb_array_elements(p_deliveries) LOOP
        IF jsonb_typeof(v_delivery) <> 'object'
           OR COALESCE(v_delivery->>'delivery_key', '') = ''
           OR v_delivery->>'delivery_kind' <> 'result'
           OR jsonb_typeof(v_delivery->'target_context') <> 'object'
           OR jsonb_typeof(v_delivery->'payload') <> 'object'
           OR v_delivery->'target_context'->>'type' NOT IN ('wecom_user', 'wecom_group')
           OR COALESCE(v_delivery->'target_context'->>'chatid', '') = '' THEN
            RAISE EXCEPTION 'SCHEDULED_TASK_DELIVERY_INVALID'
                USING ERRCODE = '22023';
        END IF;

        INSERT INTO scheduled_task_deliveries(
            run_id, task_id, org_id, delivery_key, delivery_kind,
            target_context, payload
        ) VALUES (
            p_run_id, p_task_id, v_task.org_id,
            v_delivery->>'delivery_key', 'result',
            v_delivery->'target_context', v_delivery->'payload'
        ) ON CONFLICT (run_id, delivery_key) DO NOTHING;
    END LOOP;

    UPDATE scheduled_tasks
       SET retry_config_revision = NULL, status = CASE WHEN v_task.status = 'running' THEN p_next_status ELSE v_task.status END,
           next_run_at = CASE
               WHEN v_task.status = 'running' THEN p_next_run_at
               ELSE v_task.next_run_at
           END,
           last_run_at = NOW(),
           last_summary = p_last_summary,
           last_result = p_last_result,
           run_count = v_task.run_count + 1,
           consecutive_failures = 0,
           updated_at = NOW()
     WHERE id = p_task_id;

    UPDATE scheduled_task_runs
       SET status = 'success',
           result_summary = p_last_summary,
           result_files = COALESCE(p_last_result->'files', '[]'::JSONB),
           credits_used = p_credits_used,
           tokens_used = p_tokens_used,
           duration_ms = p_duration_ms,
           finished_at = NOW()
     WHERE id = p_run_id;

    v_push_status := refresh_scheduled_task_run_push_status(p_run_id);
    RETURN jsonb_build_object(
        'outcome', 'completed',
        'push_status', v_push_status, 'next_run_at', p_next_run_at, 'schedule_status', p_next_status
    );
END;
$$;
RESET ROLE;
