SET LOCAL ROLE everydayai;
DO $$ BEGIN
    -- Inspect every tenant even when the migration session has no actor scope.
    -- Keep both toggles in this statement so any error restores FORCE RLS.
    ALTER TABLE public.agent_task_states NO FORCE ROW LEVEL SECURITY;
    IF EXISTS(SELECT 1 FROM public.agent_task_states WHERE jsonb_array_length(state->'operations')>0) THEN
        RAISE EXCEPTION 'TASK_OPERATION_ROLLBACK_HAS_STATE';
    END IF;
    ALTER TABLE public.agent_task_states FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.complete_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,jsonb,text);
DROP FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text);
DROP FUNCTION public.agent_task_image_rejection_verified(jsonb);
DROP FUNCTION IF EXISTS public.agent_task_rejection_verified(jsonb,jsonb);
DROP FUNCTION IF EXISTS public.agent_task_sandbox_phase(jsonb,jsonb,text);
RESET ROLE;
