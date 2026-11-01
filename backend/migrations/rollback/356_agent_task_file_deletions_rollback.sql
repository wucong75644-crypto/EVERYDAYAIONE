SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_file_deletions NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_file_deletions) OR EXISTS(SELECT 1 FROM public.deleted_files WHERE org_id IS NULL OR native_status IS NOT NULL) THEN
   RAISE EXCEPTION 'TASK_FILE_DELETIONS_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_file_deletions FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.reconcile_agent_task_file_delete(uuid,uuid,uuid,bigint,text,int,jsonb);
DROP FUNCTION public.complete_agent_task_file_delete_item(text,uuid,int,jsonb);
DROP FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,int);
DROP FUNCTION public.record_agent_task_file_backup(text,uuid,int,text);
DROP FUNCTION public.read_agent_task_file_delete(uuid,uuid,uuid,bigint,text);
DROP FUNCTION public.prepare_agent_task_file_delete(uuid,uuid,text,text,jsonb);
DROP FUNCTION public.agent_task_file_delete_items(uuid);
DROP TABLE public.agent_task_file_deletions;
ALTER TABLE public.deleted_files DROP COLUMN native_status;
ALTER TABLE public.deleted_files ALTER COLUMN org_id SET NOT NULL;
RESET ROLE;
