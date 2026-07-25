SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON TABLE
    public.scheduled_tasks,
    public.scheduled_task_runs
FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

DROP POLICY IF EXISTS tenant_scheduled_task_runs
ON public.scheduled_task_runs;
DROP POLICY IF EXISTS tenant_scheduled_tasks
ON public.scheduled_tasks;

ALTER TABLE public.scheduled_task_runs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_runs DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_tasks NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_tasks DISABLE ROW LEVEL SECURITY;

DO $legacy_compatibility$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            public.scheduled_tasks,
            public.scheduled_task_runs
        TO everydayai;
    END IF;
END
$legacy_compatibility$;

RESET ROLE;
