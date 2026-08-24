-- 232: Extend the suspended-organization write fence to the Agent Runtime roles.
-- Prerequisites: migrations 217 and 218, plus the Agent Runtime role migrations.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION reject_suspended_organization_service_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org_id UUID;
BEGIN
    IF session_user NOT IN (
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker',
        'everydayai_sync',
        'everydayai_agent_runtime_worker',
        'everydayai_projection_worker',
        'everydayai_authorization_worker',
        'everydayai_sandbox_worker',
        'everydayai_runtime_admin'
    ) THEN
        RETURN NEW;
    END IF;
    v_org_id := NEW.org_id;
    IF v_org_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM public.organizations organization
         WHERE organization.id = v_org_id
           AND organization.status = 'active'
    ) THEN
        RAISE EXCEPTION 'ORGANIZATION_EXECUTION_SUSPENDED'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION reject_suspended_delivery_service_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user NOT IN (
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker',
        'everydayai_agent_runtime_worker',
        'everydayai_projection_worker',
        'everydayai_authorization_worker',
        'everydayai_sandbox_worker',
        'everydayai_runtime_admin'
    ) THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.tasks task
          JOIN public.organizations organization
            ON organization.id = task.org_id
         WHERE task.id = NEW.task_id
           AND organization.status <> 'active'
    ) THEN
        RAISE EXCEPTION 'ORGANIZATION_EXECUTION_SUSPENDED'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

RESET ROLE;
