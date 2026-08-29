-- 244 回滚：仅在尚未产生草稿／预检数据时执行，保护用户已创建的工作流记录。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.scheduled_task_drafts)
       OR EXISTS (SELECT 1 FROM public.scheduled_task_preflight_runs)
       OR EXISTS (SELECT 1 FROM public.scheduled_task_execution_events) THEN
        RAISE EXCEPTION 'cannot roll back 244 while scheduled task workflow data exists';
    END IF;
END $$;

DROP FUNCTION IF EXISTS public.confirm_scheduled_task_draft(UUID, UUID, UUID, TEXT, UUID, TIMESTAMPTZ);
DROP TABLE IF EXISTS public.scheduled_task_execution_events;
DROP TABLE IF EXISTS public.scheduled_task_preflight_runs;
DROP TABLE IF EXISTS public.scheduled_task_drafts;
ALTER TABLE public.scheduled_task_runs DROP COLUMN IF EXISTS plan_snapshot, DROP COLUMN IF EXISTS completion_gate, DROP COLUMN IF EXISTS execution_id;
ALTER TABLE public.scheduled_tasks DROP COLUMN IF EXISTS plan_snapshot, DROP COLUMN IF EXISTS execution_policy;
