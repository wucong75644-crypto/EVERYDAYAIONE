DO $$ DECLARE owner name; forced boolean; BEGIN
 SELECT pg_get_userbyid(relowner),relforcerowsecurity INTO owner,forced FROM pg_class WHERE oid='public.agent_task_states'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER TABLE public.agent_task_states NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_states WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(state->'artifacts') a WHERE a ? 'image_reuse')) THEN
   RAISE EXCEPTION 'TASK_IMAGE_REUSE_NOT_DRAINED'; END IF;
 IF forced THEN ALTER TABLE public.agent_task_states FORCE ROW LEVEL SECURITY; END IF;
END $$;
RESET ROLE;
SET LOCAL ROLE everydayai;
DROP FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text);
ALTER FUNCTION public.begin_agent_task_operation_before366(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) RENAME TO begin_agent_task_operation;
DROP TRIGGER agent_task_closed_image_events ON public.agent_task_states;
DROP FUNCTION public.settle_agent_task_closed_image_events();
DROP TRIGGER agent_task_image_reuse_guard ON public.agent_task_states;
DROP FUNCTION public.guard_agent_task_image_reuse();
DROP FUNCTION public.read_agent_task_reusable_images(uuid,uuid,uuid,bigint,uuid);
DROP FUNCTION public.agent_task_image_references(jsonb);
DROP FUNCTION public.read_agent_task_terminal_images(uuid,uuid,uuid,bigint);
RESET ROLE;
