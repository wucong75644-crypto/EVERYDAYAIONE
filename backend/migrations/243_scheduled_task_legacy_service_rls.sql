-- 243: 恢复传统定时任务服务的 RLS 边界。
--
-- 定时任务控制面与执行/投递 Worker 都由 everydayai 服务账号运行。
-- 这三张表必须在强制 RLS 下显式允许该账号；没有策略时 PostgreSQL
-- 会默认拒绝全部读写，导致创建任务和后续投递都不可用。

ALTER TABLE public.scheduled_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_deliveries FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS scheduled_tasks_legacy_service
    ON public.scheduled_tasks;
DROP POLICY IF EXISTS scheduled_task_runs_legacy_service
    ON public.scheduled_task_runs;
DROP POLICY IF EXISTS scheduled_task_deliveries_legacy_service
    ON public.scheduled_task_deliveries;

CREATE POLICY scheduled_tasks_legacy_service
ON public.scheduled_tasks
FOR ALL TO everydayai
USING (SESSION_USER = 'everydayai')
WITH CHECK (SESSION_USER = 'everydayai');

CREATE POLICY scheduled_task_runs_legacy_service
ON public.scheduled_task_runs
FOR ALL TO everydayai
USING (SESSION_USER = 'everydayai')
WITH CHECK (SESSION_USER = 'everydayai');

CREATE POLICY scheduled_task_deliveries_legacy_service
ON public.scheduled_task_deliveries
FOR ALL TO everydayai
USING (SESSION_USER = 'everydayai')
WITH CHECK (SESSION_USER = 'everydayai');
