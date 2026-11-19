SET LOCAL ROLE everydayai;
-- Existing registered content must remain readable. Application rollback keeps
-- this compatible database guard; SQL downgrade is only safe without records.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.agent_task_states s,
   LATERAL jsonb_array_elements(COALESCE(s.state->'call_bindings','[]'::jsonb)) b
   WHERE b->>'tool_name'='save_task_text' AND s.state->'model_directed'='true'::jsonb)
 THEN RAISE EXCEPTION 'CONTENT_OBJECT_TASKS_REQUIRE_DRAIN'; END IF;
END $$;
DROP FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb);
ALTER FUNCTION public.agent_task_direct_extension_valid_before377(jsonb,jsonb) RENAME TO agent_task_direct_extension_valid;
DROP FUNCTION public.agent_task_direct_records_valid(jsonb);
ALTER FUNCTION public.agent_task_direct_records_valid_before377(jsonb) RENAME TO agent_task_direct_records_valid;
DROP FUNCTION public.agent_task_content_record_projection(jsonb);
