-- 249: scheduled_tasks 的 ChangeSet 适配器边界。
--
-- 这不是第二套 ChangeSet 表。scheduled_task_change_receipts 只保存适配器
-- 的幂等回执，真实任务写入仍由固定参数 RPC 完成。

ALTER TABLE public.scheduled_tasks
    ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS data_scope JSONB NOT NULL DEFAULT '{"kind":"task_prompt"}'::JSONB;

CREATE TABLE IF NOT EXISTS public.scheduled_task_change_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    change_set_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    task_id UUID NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('create','update','pause','resume','delete')),
    base_revision BIGINT NOT NULL,
    new_revision BIGINT,
    result JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_change_receipts_task
    ON public.scheduled_task_change_receipts(org_id, task_id, created_at DESC);

ALTER TABLE public.scheduled_task_change_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_change_receipts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scheduled_task_change_receipts_legacy_service
    ON public.scheduled_task_change_receipts;
CREATE POLICY scheduled_task_change_receipts_legacy_service
    ON public.scheduled_task_change_receipts
    FOR ALL TO everydayai
    USING (SESSION_USER = 'everydayai')
    WITH CHECK (SESSION_USER = 'everydayai');

-- 旧调度器/投递 RPC 也必须推进版本，否则运行期间生成的草案可能覆盖
-- 调度状态变化。适配器显式设置 revision 时触发器不会重复加一。
CREATE OR REPLACE FUNCTION public.bump_scheduled_task_revision()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.revision = OLD.revision THEN
        NEW.revision := OLD.revision + 1;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS scheduled_tasks_revision_bump ON public.scheduled_tasks;
CREATE TRIGGER scheduled_tasks_revision_bump
BEFORE UPDATE ON public.scheduled_tasks
FOR EACH ROW EXECUTE FUNCTION public.bump_scheduled_task_revision();

CREATE OR REPLACE FUNCTION public.commit_scheduled_task_changeset(
    p_change_set_id UUID,
    p_org_id UUID,
    p_user_id UUID,
    p_task_id UUID,
    p_operation TEXT,
    p_base_revision BIGINT,
    p_definition JSONB,
    p_idempotency_key TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
    v_receipt public.scheduled_task_change_receipts%ROWTYPE;
    v_new_revision BIGINT;
    v_result JSONB;
    v_next_run_at TIMESTAMPTZ;
    v_weekdays SMALLINT[];
BEGIN
    IF p_operation NOT IN ('create','update','pause','resume','delete')
       OR p_definition IS NULL OR jsonb_typeof(p_definition) <> 'object'
       OR COALESCE(p_idempotency_key, '') = '' THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_CHANGE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_receipt
      FROM public.scheduled_task_change_receipts
     WHERE org_id = p_org_id AND idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'outcome', 'duplicate', 'task_id', v_receipt.task_id,
            'new_revision', v_receipt.new_revision, 'result', v_receipt.result
        );
    END IF;

    IF p_operation = 'create' THEN
        IF p_base_revision <> 0 THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'reason', 'create_base_revision_must_be_zero');
        END IF;
        INSERT INTO public.scheduled_tasks(
            id, org_id, user_id, name, prompt, cron_expr, schedule_type,
            weekdays, day_of_month, run_at, timezone, push_target, template_file,
            status, max_credits, retry_count, timeout_sec, next_run_at,
            run_count, consecutive_failures, execution_policy, plan_snapshot, data_scope, revision
        ) VALUES (
            p_task_id, p_org_id, p_user_id, p_definition->>'name', p_definition->>'prompt',
            NULLIF(p_definition->>'cron_expr',''), p_definition->>'schedule_type',
            CASE WHEN jsonb_typeof(p_definition->'weekdays') = 'array'
                 THEN ARRAY(SELECT jsonb_array_elements_text(p_definition->'weekdays')::smallint)
                 ELSE NULL END,
            NULLIF(p_definition->>'day_of_month','')::smallint,
            NULLIF(p_definition->>'run_at','')::timestamptz,
            COALESCE(p_definition->>'timezone', 'Asia/Shanghai'),
            p_definition->'push_target', p_definition->'template_file', 'active',
            COALESCE((p_definition->>'max_credits')::integer, 10),
            COALESCE((p_definition->>'retry_count')::smallint, 1),
            COALESCE((p_definition->>'timeout_sec')::integer, 180),
            NULLIF(p_definition->>'next_run_at','')::timestamptz,
            0, 0, p_definition->'execution_policy', p_definition->'plan_snapshot',
            COALESCE(p_definition->'data_scope', '{"kind":"task_prompt"}'::JSONB), 1
        ) RETURNING * INTO v_task;
        v_new_revision := v_task.revision;
        v_result := jsonb_build_object('outcome','created','task_id',v_task.id,'new_revision',v_new_revision);
    ELSE
        SELECT * INTO v_task
          FROM public.scheduled_tasks
         WHERE id = p_task_id AND org_id = p_org_id
         FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'reason', 'task_not_found');
        END IF;
        IF v_task.revision <> p_base_revision THEN
            RETURN jsonb_build_object(
                'outcome', 'conflict', 'reason', 'base_revision_mismatch',
                'current_revision', v_task.revision, 'base_revision', p_base_revision
            );
        END IF;
        IF v_task.status = 'running' THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'reason', 'task_running');
        END IF;

        IF p_operation = 'delete' THEN
            v_new_revision := v_task.revision + 1;
            DELETE FROM public.scheduled_tasks WHERE id = v_task.id;
            v_result := jsonb_build_object('outcome','deleted','task_id',v_task.id,'new_revision',v_new_revision);
        ELSE
            IF p_operation = 'pause' THEN
                UPDATE public.scheduled_tasks
                   SET status = 'paused', next_run_at = NULL, revision = revision + 1,
                       updated_at = NOW()
                 WHERE id = v_task.id
                 RETURNING * INTO v_task;
                v_result := jsonb_build_object('outcome','paused','task_id',v_task.id,'new_revision',v_task.revision);
            ELSIF p_operation = 'resume' THEN
                v_next_run_at := NULLIF(p_definition->>'next_run_at','')::timestamptz;
                IF v_next_run_at IS NULL THEN
                    RAISE EXCEPTION 'SCHEDULED_TASK_RESUME_TIME_REQUIRED' USING ERRCODE = '22023';
                END IF;
                UPDATE public.scheduled_tasks
                   SET status = 'active', next_run_at = v_next_run_at,
                       consecutive_failures = 0, revision = revision + 1,
                       updated_at = NOW()
                 WHERE id = v_task.id
                 RETURNING * INTO v_task;
                v_result := jsonb_build_object('outcome','resumed','task_id',v_task.id,'new_revision',v_task.revision);
            ELSE
                UPDATE public.scheduled_tasks
                   SET name = p_definition->>'name',
                       prompt = p_definition->>'prompt',
                       cron_expr = NULLIF(p_definition->>'cron_expr',''),
                       schedule_type = p_definition->>'schedule_type',
                       weekdays = CASE WHEN jsonb_typeof(p_definition->'weekdays') = 'array'
                                      THEN ARRAY(SELECT jsonb_array_elements_text(p_definition->'weekdays')::smallint)
                                      ELSE NULL END,
                       day_of_month = NULLIF(p_definition->>'day_of_month','')::smallint,
                       run_at = NULLIF(p_definition->>'run_at','')::timestamptz,
                       timezone = COALESCE(p_definition->>'timezone','Asia/Shanghai'),
                       push_target = p_definition->'push_target',
                       template_file = p_definition->'template_file',
                       max_credits = COALESCE((p_definition->>'max_credits')::integer, 10),
                       retry_count = COALESCE((p_definition->>'retry_count')::smallint, 1),
                       timeout_sec = COALESCE((p_definition->>'timeout_sec')::integer, 180),
                       next_run_at = NULLIF(p_definition->>'next_run_at','')::timestamptz,
                       execution_policy = p_definition->'execution_policy',
                       plan_snapshot = p_definition->'plan_snapshot',
                       data_scope = COALESCE(p_definition->'data_scope', '{"kind":"task_prompt"}'::JSONB),
                       consecutive_failures = 0, revision = revision + 1,
                       updated_at = NOW()
                 WHERE id = v_task.id
                 RETURNING * INTO v_task;
                v_result := jsonb_build_object('outcome','updated','task_id',v_task.id,'new_revision',v_task.revision);
            END IF;
            v_new_revision := v_task.revision;
        END IF;
    END IF;

    INSERT INTO public.scheduled_task_change_receipts(
        org_id, change_set_id, idempotency_key, task_id, operation,
        base_revision, new_revision, result
    ) VALUES (
        p_org_id, p_change_set_id, p_idempotency_key, p_task_id, p_operation,
        p_base_revision, v_new_revision, v_result
    );
    RETURN v_result || jsonb_build_object('task', CASE WHEN p_operation = 'delete' THEN NULL ELSE to_jsonb(v_task) END);
END;
$$;

REVOKE ALL ON FUNCTION public.commit_scheduled_task_changeset(UUID, UUID, UUID, UUID, TEXT, BIGINT, JSONB, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.commit_scheduled_task_changeset(UUID, UUID, UUID, UUID, TEXT, BIGINT, JSONB, TEXT) TO everydayai;
