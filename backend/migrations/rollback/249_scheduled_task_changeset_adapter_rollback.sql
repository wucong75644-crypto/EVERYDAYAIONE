-- 249 回滚：仅移除适配器边界，不触碰 ChangeSet 三张通用表。
DROP FUNCTION IF EXISTS public.commit_scheduled_task_changeset(UUID, UUID, UUID, UUID, TEXT, BIGINT, JSONB, TEXT);
DROP TRIGGER IF EXISTS scheduled_tasks_revision_bump ON public.scheduled_tasks;
DROP FUNCTION IF EXISTS public.bump_scheduled_task_revision();
DROP TABLE IF EXISTS public.scheduled_task_change_receipts;
ALTER TABLE public.scheduled_tasks DROP COLUMN IF EXISTS revision;
ALTER TABLE public.scheduled_tasks DROP COLUMN IF EXISTS data_scope;
