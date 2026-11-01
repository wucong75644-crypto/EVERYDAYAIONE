SET LOCAL ROLE everydayai;
-- The table owner may inspect all rows only for this transaction's rollback guard.
ALTER TABLE public.agent_task_states NO FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM public.agent_task_states) THEN
        RAISE EXCEPTION 'TASK_EXECUTION_ROLLBACK_REQUIRES_DRAIN';
    END IF;
END $$;
DO $$ DECLARE v_owner text; BEGIN
    SELECT pg_get_userbyid(relowner) INTO v_owner FROM pg_class
     WHERE oid='public.conversation_turn_checkpoints'::regclass;
    IF v_owner NOT IN ('everydayai','everydayai_owner') THEN
        RAISE EXCEPTION 'TASK_EXECUTION_CHECKPOINT_OWNER_UNSUPPORTED';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',v_owner);
END $$;
DROP TRIGGER agent_task_checkpoint_consistency ON public.conversation_turn_checkpoints;
SET LOCAL ROLE everydayai;
DROP TRIGGER agent_task_state_consistency ON public.agent_task_states;
DROP FUNCTION public.guard_agent_task_checkpoint();
DROP FUNCTION public.save_agent_task_checkpoint(uuid,uuid,text,jsonb,bigint);
DROP FUNCTION public.read_agent_task_text_artifacts(uuid,uuid,uuid,bigint,uuid,text,integer);
DROP FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb);
DROP TABLE public.agent_task_events;
DROP TABLE public.agent_task_states;
RESET ROLE;
