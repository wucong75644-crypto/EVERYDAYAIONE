SET LOCAL ROLE everydayai;
DO $$ BEGIN
    ALTER TABLE public.agent_task_events NO FORCE ROW LEVEL SECURITY;
    IF EXISTS(SELECT 1 FROM public.agent_task_events WHERE child_task_id IS NOT NULL) THEN
        RAISE EXCEPTION 'TASK_CHILD_ROLLBACK_REQUIRES_DRAIN';
    END IF;
    ALTER TABLE public.agent_task_events FORCE ROW LEVEL SECURITY;
END $$;
DROP TRIGGER agent_task_image_dependency_after_accept ON public.tasks;
DROP TRIGGER agent_task_child_identity ON public.agent_task_events;
DROP FUNCTION public.register_agent_image_dependency();
DROP FUNCTION public.guard_agent_child_identity();
DROP POLICY agent_task_child_terminal_worker_read ON public.agent_task_events;
ALTER TABLE public.agent_task_events DROP COLUMN child_task_id CASCADE;
ALTER TABLE public.agent_task_events DROP COLUMN operation_id CASCADE;
RESET ROLE;
