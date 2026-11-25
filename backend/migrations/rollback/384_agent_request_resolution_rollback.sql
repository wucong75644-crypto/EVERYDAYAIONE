SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 start_at integer; end_at integer;
BEGIN
 -- Refuse to discard live target bindings. Drain or finish their original
 -- windows before reverting this contract.
 IF EXISTS(SELECT FROM public.tasks t JOIN public.conversation_turn_checkpoints cp ON cp.task_id=t.id
           WHERE t.status IN ('pending','running') AND cp.state ? '_request_context_v1') THEN
   RAISE EXCEPTION 'REQUEST_RESOLUTION_LIVE_WINDOWS'; END IF;
 start_at:=strpos(definition,'IF fresh ? ''_request_context_v1'' AND (');
 end_at:=strpos(definition,'IF s.root_task_id IS NULL OR s.root_task_id::text IS DISTINCT FROM source#>>''{state,root_task_id}''');
 IF start_at=0 OR end_at<=start_at THEN RAISE EXCEPTION 'Unexpected target adoption definition'; END IF;
 definition:=overlay(definition placing '' from start_at for end_at-start_at);
 definition:=replace(definition,'''_ecom_workflow'',''_request_context_v1'',''_request_resolution_v1'',''_inline_media_action'',''_request_response_v1''])','''_ecom_workflow''])');
 EXECUTE definition;
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.read_agent_content_followup(uuid,uuid,uuid)'::regprocedure);
BEGIN
 definition:=replace(definition,'''root_task_id'',s.root_task_id,''creation_task_id'',a->>''source_task_id'',''artifact_id'',a->>''id'',',
                               '''root_task_id'',s.root_task_id,''artifact_id'',a->>''id'',');
 EXECUTE definition;
END $migration$;
DROP FUNCTION public.read_agent_request_context(uuid,uuid);
RESET ROLE;
