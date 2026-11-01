SET LOCAL ROLE everydayai;
DO $$ BEGIN
    ALTER TABLE public.agent_task_states NO FORCE ROW LEVEL SECURITY;
    IF EXISTS(SELECT 1 FROM public.agent_task_states WHERE state->>'status'='paused') THEN
        RAISE EXCEPTION 'TASK_CONTROL_ROLLBACK_REQUIRES_DRAIN';
    END IF;
    ALTER TABLE public.agent_task_states FORCE ROW LEVEL SECURITY;
END $$;
DROP TRIGGER agent_task_owner_marker ON public.agent_task_states;
DROP TRIGGER agent_task_owner_control ON public.tasks;
DROP FUNCTION public.mark_agent_task_owner(),public.sync_agent_task_owner_control(),public.control_agent_task(uuid,uuid,uuid,text),public.transition_agent_task_control(uuid,text);
RESET ROLE;
