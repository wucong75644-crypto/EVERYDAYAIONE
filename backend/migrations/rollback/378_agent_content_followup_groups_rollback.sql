SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
BEGIN
 IF strpos(definition,'checked:=public.read_agent_content_followup(p_task_id,p_execution_token,p_root_task_id);')=0 THEN RAISE EXCEPTION 'Unexpected followup adoption definition'; END IF;
 EXECUTE replace(definition,'checked:=public.read_agent_content_followup(p_task_id,p_execution_token,p_root_task_id);','checked:=public.read_agent_task_followup(p_task_id,p_execution_token);');
END $migration$;
DROP FUNCTION public.read_agent_task_followup(uuid,uuid);
ALTER FUNCTION public.read_agent_task_followup_before378(uuid,uuid) RENAME TO read_agent_task_followup;
DROP FUNCTION public.read_agent_content_followup(uuid,uuid,uuid);
RESET ROLE;
