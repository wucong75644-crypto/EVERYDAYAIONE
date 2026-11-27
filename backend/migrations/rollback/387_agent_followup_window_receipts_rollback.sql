SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 guard text := $guard$ FOREACH k IN ARRAY ARRAY['_request_context_v1','_request_resolution_v1','_request_response_v1','_historical_image_catalog_v1','_content_revision_reads_v1','_existing_image_recovery_v1'] LOOP
    IF p_payload->k IS DISTINCT FROM fresh->k THEN RAISE EXCEPTION 'TASK_FOLLOWUP_WINDOW_CONTEXT_CHANGED'; END IF;
 END LOOP;
$guard$;
BEGIN
 IF strpos(definition,guard)=0 THEN RAISE EXCEPTION 'Unexpected followup receipt rollback definition'; END IF;
 EXECUTE replace(replace(definition,guard,''),
   $new$'_request_response_v1','_historical_image_catalog_v1','_content_revision_reads_v1','_existing_image_recovery_v1'])$new$,
   $old$'_request_response_v1','_historical_image_catalog_v1'])$old$);
END $migration$;
RESET ROLE;
