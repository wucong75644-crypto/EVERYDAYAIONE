-- Do not strand already-acknowledged input in an older reader.
DO $$ DECLARE owner name; forced boolean; BEGIN
 SELECT relforcerowsecurity INTO forced FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 SELECT pg_get_userbyid(relowner) INTO owner FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER TABLE public.conversation_turn_checkpoints NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.conversation_turn_checkpoints WHERE state ? '_task_user_changes') THEN
   RAISE EXCEPTION 'TASK_USER_CHANGES_NOT_DRAINED';
 END IF;
 IF forced THEN ALTER TABLE public.conversation_turn_checkpoints FORCE ROW LEVEL SECURITY; END IF;
 DROP TRIGGER agent_task_user_changes_guard ON public.conversation_turn_checkpoints;
END $$;
RESET ROLE;
SET LOCAL ROLE everydayai;
DROP FUNCTION public.capture_agent_task_user_change(uuid,uuid,uuid);
DROP FUNCTION public.guard_agent_task_user_changes();
RESET ROLE;
