SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_events NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_events WHERE payload->>'source_kind'='scheduled' AND status IN ('pending','queued')) THEN
   RAISE EXCEPTION 'TASK_RECEIPT_CONTINUATIONS_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_events FORCE ROW LEVEL SECURITY;
END $$;
DROP TRIGGER agent_task_receipt_wait ON public.agent_task_states;
DROP FUNCTION public.mark_agent_task_receipt_wait();
DROP FUNCTION public.refresh_agent_task_receipt_events(uuid);
DROP FUNCTION public.load_agent_receipt_continuation(uuid,uuid);
DROP FUNCTION public.apply_agent_receipt_continuation(uuid,uuid,bigint,jsonb);
DROP FUNCTION public.list_agent_task_receipt_hints(uuid,integer);
DROP FUNCTION public.agent_task_scheduled_terminal_fact(uuid,text);
DROP INDEX public.agent_task_scheduled_scan;
RESET ROLE;
