SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_native_writes NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_native_writes) THEN RAISE EXCEPTION 'TASK_NATIVE_WRITES_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_native_writes FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.read_agent_task_native_write(uuid,uuid,uuid,bigint,text);
DROP FUNCTION public.record_agent_task_native_write(text,uuid,text,jsonb);
DROP FUNCTION public.prepare_agent_task_native_write(uuid,uuid,text,text,text,jsonb);
DROP TABLE public.agent_task_native_writes;
RESET ROLE;
