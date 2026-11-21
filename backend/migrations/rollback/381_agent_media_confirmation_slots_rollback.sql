-- Restore the original input fence. Keep persisted slot records readable.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.agent_task_direct_operation_current(p_state jsonb,p_checkpoint jsonb,p_operation_id text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT CASE WHEN p_state->'model_directed' IS DISTINCT FROM 'true'::jsonb THEN true ELSE EXISTS(
   SELECT 1 FROM jsonb_array_elements(p_state->'operations') op
   JOIN jsonb_array_elements(p_state->'call_bindings') b ON b->>'step_id'=op->>'step_id'
   WHERE op->>'id'=p_operation_id AND b->>'tool_name'=op->>'tool_name'
     AND b->>'input_sha256'=public.agent_task_direct_input_hash(p_checkpoint)
     AND NOT COALESCE(p_state->'retired_call_steps','[]'::jsonb) @> jsonb_build_array(op->>'step_id')) END
$$;
DROP FUNCTION public.agent_media_confirmed_index(text);
RESET ROLE;
