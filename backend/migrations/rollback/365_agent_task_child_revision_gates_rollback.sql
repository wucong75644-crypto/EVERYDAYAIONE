-- Stop the original consumers and drain v3 work before removing dispatch gates.
DO $$ DECLARE owner name; forced boolean; BEGIN
 SELECT pg_get_userbyid(relowner),relforcerowsecurity INTO owner,forced FROM pg_class WHERE oid='public.agent_task_states'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER TABLE public.agent_task_states NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_states WHERE state->>'image_binding_version'='3'
     AND (state->>'status' NOT IN ('completed','cancelled','failed') OR state ? 'requirements_history')) THEN
   RAISE EXCEPTION 'TASK_REVISION_DISPATCH_NOT_DRAINED'; END IF;
 IF forced THEN ALTER TABLE public.agent_task_states FORCE ROW LEVEL SECURITY; END IF;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.mark_chat_image_dispatch(uuid,uuid,uuid)'::regprocedure;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 DROP FUNCTION public.mark_chat_image_dispatch(uuid,uuid,uuid);
 ALTER FUNCTION public.mark_chat_image_dispatch_before365(uuid,uuid,uuid) RENAME TO mark_chat_image_dispatch;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.mark_agent_video_dispatch_before365(uuid,uuid,uuid)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.mark_agent_video_dispatch(uuid,uuid,uuid)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   DROP FUNCTION public.mark_agent_video_dispatch(uuid,uuid,uuid);
   ALTER FUNCTION public.mark_agent_video_dispatch_before365(uuid,uuid,uuid) RENAME TO mark_agent_video_dispatch;
 END IF;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.agent_video_submission_gate_before365(uuid,uuid)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.agent_video_submission_gate(uuid,uuid)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   DROP FUNCTION public.agent_video_submission_gate(uuid,uuid);
   ALTER FUNCTION public.agent_video_submission_gate_before365(uuid,uuid) RENAME TO agent_video_submission_gate;
 END IF;
END $$;
RESET ROLE;
SET LOCAL ROLE everydayai;
DROP FUNCTION public.agent_child_revision_gate(uuid,uuid);
RESET ROLE;

DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.authorize_agent_task_file_delete_item_before365(text,uuid,integer)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.authorize_agent_task_file_delete_item(text,uuid,integer)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   DROP FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer);
   ALTER FUNCTION public.authorize_agent_task_file_delete_item_before365(text,uuid,integer) RENAME TO authorize_agent_task_file_delete_item;
 END IF;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.authorize_agent_task_file_delete_resume_before365(uuid,uuid,uuid,bigint,text,text,integer)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   DROP FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer);
   ALTER FUNCTION public.authorize_agent_task_file_delete_resume_before365(uuid,uuid,uuid,bigint,text,text,integer) RENAME TO authorize_agent_task_file_delete_resume;
 END IF;
END $$;
RESET ROLE;
