-- 245 rollback: 仅移除新增表单状态函数和修订草稿字段。
DROP FUNCTION IF EXISTS public.transition_chat_form_state(
    UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT
);
ALTER TABLE public.scheduled_tasks
    DROP CONSTRAINT IF EXISTS scheduled_tasks_execution_policy_required;

-- 恢复 244 的“仅创建”确认函数，再移除其不存在的修订字段。
CREATE OR REPLACE FUNCTION public.confirm_scheduled_task_draft(
    p_draft_id UUID,
    p_org_id UUID,
    p_user_id UUID,
    p_config_hash TEXT,
    p_task_id UUID,
    p_next_run_at TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_draft scheduled_task_drafts%ROWTYPE;
    v_definition JSONB;
BEGIN
    SELECT * INTO v_draft FROM scheduled_task_drafts
      WHERE id = p_draft_id AND org_id = p_org_id AND user_id = p_user_id
      FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;
    IF v_draft.status = 'confirmed' THEN
        RETURN jsonb_build_object('outcome', 'confirmed', 'task_id', v_draft.confirmed_task_id);
    END IF;
    IF v_draft.status <> 'ready' OR v_draft.config_hash <> p_config_hash
       OR v_draft.preflight_config_hash <> p_config_hash OR v_draft.expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'not_ready');
    END IF;
    v_definition := v_draft.definition;
    INSERT INTO scheduled_tasks(
        id, org_id, user_id, name, prompt, cron_expr, schedule_type, weekdays,
        day_of_month, run_at, timezone, push_target, template_file, status,
        max_credits, retry_count, timeout_sec, next_run_at, run_count,
        consecutive_failures, execution_policy, plan_snapshot
    ) VALUES (
        p_task_id, p_org_id, p_user_id, v_definition->>'name', v_definition->>'prompt',
        NULLIF(v_definition->>'cron_expr',''), v_definition->>'schedule_type',
        CASE WHEN jsonb_typeof(v_definition->'weekdays') = 'array' THEN ARRAY(SELECT jsonb_array_elements_text(v_definition->'weekdays')::smallint) END,
        NULLIF(v_definition->>'day_of_month','')::smallint,
        NULLIF(v_definition->>'run_at','')::timestamptz, COALESCE(v_definition->>'timezone','Asia/Shanghai'),
        v_definition->'push_target', v_definition->'template_file', 'active',
        COALESCE((v_definition->>'max_credits')::integer, 10),
        COALESCE((v_definition->>'retry_count')::smallint, 1),
        COALESCE((v_definition->>'timeout_sec')::integer, 180), p_next_run_at,
        0, 0, v_draft.execution_policy, v_draft.plan
    );
    UPDATE scheduled_task_drafts
       SET status = 'confirmed', confirmed_task_id = p_task_id, updated_at = NOW()
     WHERE id = p_draft_id;
    RETURN jsonb_build_object('outcome', 'created', 'task_id', p_task_id);
END;
$$;

ALTER TABLE public.scheduled_task_drafts
    DROP COLUMN IF EXISTS source_task_id;
