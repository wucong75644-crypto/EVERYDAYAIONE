-- Retain durable recovery associations until managed requests are drained.
SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_scheduled_bindings NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_scheduled_bindings) THEN
   RAISE EXCEPTION 'TASK_SCHEDULED_BINDINGS_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_scheduled_bindings FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.read_agent_task_scheduled_receipt(uuid,uuid,uuid,bigint,text);
DROP FUNCTION public.agent_task_scheduled_fact(uuid,text);
DROP FUNCTION public.agent_task_scheduled_request_key(uuid,uuid,text,text,text);
DROP TABLE public.agent_task_scheduled_bindings;
RESET ROLE;
