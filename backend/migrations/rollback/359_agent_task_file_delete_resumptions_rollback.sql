SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_file_delete_resumptions NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_file_delete_resumptions) THEN
   RAISE EXCEPTION 'TASK_FILE_RESUMPTIONS_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_file_delete_resumptions FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.complete_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,jsonb,text);
DROP FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,int);
DROP FUNCTION public.begin_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,text);
DROP FUNCTION public.read_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,text);
DROP TABLE public.agent_task_file_delete_resumptions;
RESET ROLE;
