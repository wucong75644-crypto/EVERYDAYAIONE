-- 245: 定时任务工作流完整性。
--
-- 1) 聊天表单的取消/提交状态落在 messages.content，刷新与跨端恢复同一事实；
-- 2) 修改活跃任务必须经过新的草稿预检，再由原子确认替换执行定义；
-- 3) 未经过预检的历史任务停止自动调度，避免无边界工具权限继续执行。

ALTER TABLE public.scheduled_task_drafts
    ADD COLUMN IF NOT EXISTS source_task_id UUID REFERENCES public.scheduled_tasks(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_scheduled_task_drafts_source_task
    ON public.scheduled_task_drafts(source_task_id, updated_at DESC)
    WHERE source_task_id IS NOT NULL;

-- 旧任务没有 execution_policy，不能在新运行时回退为全工具可见。
UPDATE public.scheduled_tasks
   SET status = 'paused',
       next_run_at = NULL,
       updated_at = NOW()
 WHERE execution_policy IS NULL
   AND status = 'active';

ALTER TABLE public.scheduled_tasks
    DROP CONSTRAINT IF EXISTS scheduled_tasks_execution_policy_required,
    ADD CONSTRAINT scheduled_tasks_execution_policy_required
        CHECK (execution_policy IS NOT NULL AND jsonb_typeof(execution_policy) = 'object')
        NOT VALID;

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
    v_source scheduled_tasks%ROWTYPE;
    v_source_status TEXT;
BEGIN
    SELECT * INTO v_draft FROM scheduled_task_drafts
      WHERE id = p_draft_id AND org_id = p_org_id AND user_id = p_user_id
      FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;
    IF v_draft.status = 'confirmed' THEN
        RETURN jsonb_build_object(
            'outcome', 'confirmed',
            'task_id', COALESCE(v_draft.confirmed_task_id, v_draft.source_task_id)
        );
    END IF;
    IF v_draft.status <> 'ready' OR v_draft.config_hash <> p_config_hash
       OR v_draft.preflight_config_hash <> p_config_hash OR v_draft.expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'not_ready');
    END IF;

    v_definition := v_draft.definition;
    IF v_draft.source_task_id IS NULL THEN
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
    END IF;

    SELECT * INTO v_source FROM scheduled_tasks
      WHERE id = v_draft.source_task_id AND org_id = p_org_id
      FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'source_missing');
    END IF;
    IF v_source.status = 'running' THEN
        RETURN jsonb_build_object('outcome', 'source_running');
    END IF;

    v_source_status := CASE
        WHEN v_source.status = 'active' THEN 'active'
        ELSE 'paused'
    END;
    UPDATE scheduled_tasks
       SET name = v_definition->>'name',
           prompt = v_definition->>'prompt',
           cron_expr = NULLIF(v_definition->>'cron_expr',''),
           schedule_type = v_definition->>'schedule_type',
           weekdays = CASE WHEN jsonb_typeof(v_definition->'weekdays') = 'array' THEN ARRAY(SELECT jsonb_array_elements_text(v_definition->'weekdays')::smallint) END,
           day_of_month = NULLIF(v_definition->>'day_of_month','')::smallint,
           run_at = NULLIF(v_definition->>'run_at','')::timestamptz,
           timezone = COALESCE(v_definition->>'timezone','Asia/Shanghai'),
           push_target = v_definition->'push_target',
           template_file = v_definition->'template_file',
           max_credits = COALESCE((v_definition->>'max_credits')::integer, 10),
           retry_count = COALESCE((v_definition->>'retry_count')::smallint, 1),
           timeout_sec = COALESCE((v_definition->>'timeout_sec')::integer, 180),
           status = v_source_status,
           next_run_at = CASE WHEN v_source_status = 'active' THEN p_next_run_at ELSE NULL END,
           consecutive_failures = 0,
           execution_policy = v_draft.execution_policy,
           plan_snapshot = v_draft.plan,
           updated_at = NOW()
     WHERE id = v_source.id;
    UPDATE scheduled_task_drafts
       SET status = 'confirmed', confirmed_task_id = v_source.id, updated_at = NOW()
     WHERE id = p_draft_id;
    RETURN jsonb_build_object('outcome', 'updated', 'task_id', v_source.id);
END;
$$;

-- 仅按 form_id 修改已经完成的 assistant 消息中的一个表单块。
-- 调用方先校验会话所有权；这里使用 SELECT FOR UPDATE 消除多端双提交竞态。
CREATE OR REPLACE FUNCTION public.transition_chat_form_state(
    p_message_id UUID,
    p_conversation_id UUID,
    p_form_id TEXT,
    p_expected_status TEXT,
    p_next_status TEXT,
    p_result_message TEXT DEFAULT NULL,
    p_next_form JSONB DEFAULT NULL,
    p_error_message TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_content JSONB;
    v_block JSONB;
    v_updated JSONB := '[]'::JSONB;
    v_found BOOLEAN := FALSE;
    v_current_status TEXT;
BEGIN
    IF p_expected_status NOT IN ('open', 'submitting')
       OR p_next_status NOT IN ('open', 'submitting', 'cancelled', 'submitted') THEN
        RAISE EXCEPTION 'CHAT_FORM_STATE_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    -- 生产 messages.content 的契约是 TEXT，内容保存为 JSONB 的文本表示。
    SELECT content::JSONB INTO v_content FROM messages
      WHERE id = p_message_id AND conversation_id = p_conversation_id AND role = 'assistant'
      FOR UPDATE;
    IF NOT FOUND OR jsonb_typeof(v_content) <> 'array' THEN
        RETURN jsonb_build_object('outcome', 'message_missing');
    END IF;

    FOR v_block IN SELECT value FROM jsonb_array_elements(v_content) LOOP
        IF v_block->>'type' = 'form' AND v_block->>'form_id' = p_form_id THEN
            v_found := TRUE;
            v_current_status := COALESCE(v_block->>'status', 'open');
            IF v_current_status <> p_expected_status THEN
                RETURN jsonb_build_object('outcome', 'state_conflict', 'status', v_current_status);
            END IF;
            v_block := v_block || jsonb_build_object('status', p_next_status);
            IF p_result_message IS NOT NULL THEN
                v_block := v_block || jsonb_build_object('result_message', p_result_message);
            END IF;
            IF p_next_form IS NOT NULL THEN
                v_block := v_block || jsonb_build_object('next_form', p_next_form);
            END IF;
            IF p_error_message IS NOT NULL THEN
                v_block := v_block || jsonb_build_object('error_message', p_error_message);
            ELSIF p_next_status <> 'open' THEN
                v_block := v_block - 'error_message';
            END IF;
        END IF;
        v_updated := v_updated || jsonb_build_array(v_block);
    END LOOP;

    IF NOT v_found THEN
        RETURN jsonb_build_object('outcome', 'form_missing');
    END IF;
    UPDATE messages SET content = v_updated::TEXT WHERE id = p_message_id;
    RETURN jsonb_build_object('outcome', 'transitioned', 'status', p_next_status);
END;
$$;

REVOKE ALL ON FUNCTION public.transition_chat_form_state(
    UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.transition_chat_form_state(
    UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT
) TO everydayai;
