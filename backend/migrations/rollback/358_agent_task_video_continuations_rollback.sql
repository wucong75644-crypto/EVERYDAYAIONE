SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_events NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_events WHERE payload->>'source_kind'='video' AND status IN ('pending','queued')) THEN
   RAISE EXCEPTION 'AGENT_VIDEO_CONTINUATIONS_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_events FORCE ROW LEVEL SECURITY;
END $$;
DROP TRIGGER agent_task_video_wait ON public.agent_task_states;
DROP FUNCTION public.mark_agent_task_video_wait();
DROP FUNCTION public.refresh_agent_task_video_events(uuid);
DROP FUNCTION public.list_agent_task_video_hints(uuid,int);
DROP INDEX public.agent_task_video_scan;
DROP FUNCTION public.agent_task_video_terminal_fact(uuid,text);
RESET ROLE;
