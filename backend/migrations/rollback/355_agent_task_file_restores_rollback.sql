SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_native_writes NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_native_writes WHERE domain='file') THEN RAISE EXCEPTION 'TASK_FILE_RESTORES_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_native_writes FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.reconcile_agent_task_file_restore(uuid,uuid,uuid,bigint,text,jsonb);
DROP FUNCTION public.complete_agent_task_file_restore(text,uuid,jsonb);
DROP FUNCTION public.prepare_agent_task_file_restore(uuid,uuid,text,text,jsonb);
DROP INDEX public.agent_task_file_restore_record;
RESET ROLE;
