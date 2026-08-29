-- 回滚 243：恢复迁移前的 RLS 配置。
--
-- scheduled_tasks / scheduled_task_runs 在生产迁移前已强制 RLS，
-- 但没有策略会使服务完全不可用。因此回滚时取消强制 RLS，使表 owner
-- 可以继续按既有的 OrgScopedDB 与权限校验链路访问。

DROP POLICY IF EXISTS scheduled_task_deliveries_legacy_service
    ON public.scheduled_task_deliveries;
DROP POLICY IF EXISTS scheduled_task_runs_legacy_service
    ON public.scheduled_task_runs;
DROP POLICY IF EXISTS scheduled_tasks_legacy_service
    ON public.scheduled_tasks;

ALTER TABLE public.scheduled_task_deliveries NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_deliveries DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_runs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_tasks NO FORCE ROW LEVEL SECURITY;
