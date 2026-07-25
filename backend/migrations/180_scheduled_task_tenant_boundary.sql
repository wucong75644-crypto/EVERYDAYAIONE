-- 180: 定时任务控制面租户边界与 Web Runtime 最小表权限。
-- 前置：先执行 deploy/transfer-worker-control-ownership.sh。

DO $preflight$
DECLARE
    invalid_owners TEXT;
BEGIN
    SELECT string_agg(
               relation.relname || '=' || owner_role.rolname,
               ', ' ORDER BY relation.relname
           )
      INTO invalid_owners
      FROM pg_catalog.pg_class relation
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = relation.relnamespace
      JOIN pg_catalog.pg_roles owner_role
        ON owner_role.oid = relation.relowner
     WHERE namespace.nspname = 'public'
       AND relation.relname = ANY(ARRAY[
           'scheduled_tasks',
           'scheduled_task_runs'
       ])
       AND owner_role.rolname <> 'everydayai_owner';
    IF invalid_owners IS NOT NULL THEN
        RAISE EXCEPTION 'SCHEDULED_CONTROL_OWNER_INVALID: %', invalid_owners;
    END IF;
END
$preflight$;

SET LOCAL ROLE everydayai_owner;

ALTER TABLE public.scheduled_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_runs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_scheduled_tasks
ON public.scheduled_tasks
TO everydayai_owner, everydayai_runtime
USING (
    current_user = 'everydayai_owner'
    OR (
        org_id = tenant_org_id()
        AND tenant_actor_is_active_member(org_id)
    )
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR (
        org_id = tenant_org_id()
        AND tenant_actor_is_active_member(org_id)
    )
);

CREATE POLICY tenant_scheduled_task_runs
ON public.scheduled_task_runs
TO everydayai_owner, everydayai_runtime
USING (
    current_user = 'everydayai_owner'
    OR (
        org_id = tenant_org_id()
        AND tenant_actor_is_active_member(org_id)
        AND EXISTS (
            SELECT 1
              FROM public.scheduled_tasks task
             WHERE task.id = scheduled_task_runs.task_id
               AND task.org_id = scheduled_task_runs.org_id
        )
    )
)
WITH CHECK (
    current_user = 'everydayai_owner'
    OR (
        org_id = tenant_org_id()
        AND tenant_actor_is_active_member(org_id)
        AND EXISTS (
            SELECT 1
              FROM public.scheduled_tasks task
             WHERE task.id = scheduled_task_runs.task_id
               AND task.org_id = scheduled_task_runs.org_id
        )
    )
);

REVOKE ALL ON TABLE
    public.scheduled_tasks,
    public.scheduled_task_runs
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE public.scheduled_tasks
TO everydayai_runtime;
GRANT SELECT
ON TABLE public.scheduled_task_runs
TO everydayai_runtime;

GRANT EXECUTE ON FUNCTION
    tenant_org_id(),
    tenant_actor_is_active_member(UUID)
TO everydayai_runtime;

RESET ROLE;
