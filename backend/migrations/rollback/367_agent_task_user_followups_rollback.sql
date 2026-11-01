DO $$ DECLARE owner name; forced boolean; BEGIN
 SELECT pg_get_userbyid(relowner),relforcerowsecurity INTO owner,forced FROM pg_class WHERE oid='public.tasks'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER TABLE public.tasks NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.tasks WHERE delivery_context->>'input_kind'='agent_task_followup') THEN
   RAISE EXCEPTION 'TASK_FOLLOWUPS_NOT_DRAINED'; END IF;
 IF forced THEN ALTER TABLE public.tasks FORCE ROW LEVEL SECURITY; END IF;
END $$;
RESET ROLE;
SET LOCAL ROLE everydayai;
DROP FUNCTION public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb);
DROP FUNCTION public.read_agent_task_followup(uuid,uuid);
RESET ROLE;
