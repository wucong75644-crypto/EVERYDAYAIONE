-- Fresh read/recovery receipts belong to the authenticated input window.
-- Preserve old task facts, while verifying every replaced field against fresh.
SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 old_fragment text := $old$'_request_response_v1','_historical_image_catalog_v1'])$old$;
 new_fragment text := $new$'_request_response_v1','_historical_image_catalog_v1','_content_revision_reads_v1','_existing_image_recovery_v1'])$new$;
 marker text := $marker$ FOR k IN SELECT jsonb_object_keys(cp-ARRAY[$marker$;
 guard text := $guard$ FOREACH k IN ARRAY ARRAY['_request_context_v1','_request_resolution_v1','_request_response_v1','_historical_image_catalog_v1','_content_revision_reads_v1','_existing_image_recovery_v1'] LOOP
    IF p_payload->k IS DISTINCT FROM fresh->k THEN RAISE EXCEPTION 'TASK_FOLLOWUP_WINDOW_CONTEXT_CHANGED'; END IF;
 END LOOP;
$guard$;
BEGIN
 IF strpos(definition,old_fragment)=0 OR strpos(definition,marker)=0 THEN
   RAISE EXCEPTION 'Unexpected followup receipt definition';
 END IF;
 EXECUTE replace(replace(definition,old_fragment,new_fragment),marker,guard||marker);
END $migration$;
RESET ROLE;
