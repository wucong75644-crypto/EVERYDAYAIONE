-- Refuse removing the verifier while active tasks depend on its recorded facts.
SET LOCAL ROLE everydayai;
DO $$ BEGIN
 ALTER TABLE public.agent_task_native_writes NO FORCE ROW LEVEL SECURITY;
 IF EXISTS(SELECT 1 FROM public.agent_task_native_writes WHERE dispatch#>>'{postcondition,kind}'='product.title') THEN
   RAISE EXCEPTION 'TASK_ERP_POSTCONDITIONS_NOT_DRAINED'; END IF;
 ALTER TABLE public.agent_task_native_writes FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.read_agent_task_erp_postcondition(uuid,uuid,uuid,bigint,text);
RESET ROLE;
