DO $$ DECLARE owner name; forced boolean; BEGIN
 SELECT pg_get_userbyid(relowner),relforcerowsecurity INTO owner,forced FROM pg_class WHERE oid='public.agent_task_states'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER TABLE public.agent_task_states NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_states s,jsonb_array_elements(s.state->'artifacts') a
     WHERE a->>'reference' LIKE 'task-source:%') THEN
   RAISE EXCEPTION 'TASK_SAVED_PLAN_SOURCE_DRAIN_REQUIRED'; END IF;
 IF forced THEN ALTER TABLE public.agent_task_states FORCE ROW LEVEL SECURITY; END IF;
END $$;
RESET ROLE;
SET LOCAL ROLE everydayai;
DROP TRIGGER agent_task_saved_plan_source_guard ON public.agent_task_states;
DROP FUNCTION public.guard_agent_task_saved_plan_source();
RESET ROLE;
