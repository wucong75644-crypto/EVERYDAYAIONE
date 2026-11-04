-- Optional model notes and append-only concrete calls in the EXISTING task
-- ledger. This migration does not enable the new execution policy.
SET LOCAL ROLE everydayai;

CREATE FUNCTION public.agent_task_json_array_prefix(p_old jsonb,p_new jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_typeof(p_old)='array' AND jsonb_typeof(p_new)='array'
   AND jsonb_array_length(p_new)>=jsonb_array_length(p_old)
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_old) WITH ORDINALITY item(value,n)
                  WHERE value IS DISTINCT FROM p_new->((n-1)::integer))
$$;
REVOKE ALL ON FUNCTION public.agent_task_json_array_prefix(jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_json_array_prefix(jsonb,jsonb) TO everydayai,everydayai_owner;

CREATE FUNCTION public.agent_task_direct_records_valid(p_state jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE binding jsonb; step jsonb; requirement jsonb;
BEGIN
 IF p_state->'model_directed' IS DISTINCT FROM 'true'::jsonb
   OR jsonb_typeof(p_state->'call_bindings') IS DISTINCT FROM 'array'
   OR jsonb_typeof(p_state->'steps') IS DISTINCT FROM 'array'
   OR jsonb_typeof(p_state->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(p_state->'call_bindings') NOT BETWEEN 1 AND 256
   OR jsonb_array_length(p_state->'steps') NOT BETWEEN 1 AND 32
   OR jsonb_array_length(p_state->'requirements') NOT BETWEEN 1 AND 32
   OR (SELECT count(*)<>count(DISTINCT b->>'call_id') FROM jsonb_array_elements(p_state->'call_bindings') b)
   OR (SELECT count(*)<>count(DISTINCT s->>'id') FROM jsonb_array_elements(p_state->'steps') s)
   OR (SELECT count(*)<>count(DISTINCT r->>'id') FROM jsonb_array_elements(p_state->'requirements') r)
 THEN RETURN false; END IF;
 IF jsonb_typeof(COALESCE(p_state->'resolved_call_bindings','[]'::jsonb)) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF (SELECT count(*)<>count(DISTINCT r->>'call_id') FROM jsonb_array_elements(COALESCE(p_state->'resolved_call_bindings','[]'::jsonb)) r)
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(p_state->'resolved_call_bindings','[]'::jsonb)) r
      WHERE r-ARRAY['call_id','arguments_sha256']<>'{}'::jsonb
        OR COALESCE(r->>'arguments_sha256','') !~ '^[a-f0-9]{64}$'
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_state->'call_bindings') b WHERE b->>'call_id'=r->>'call_id')) THEN RETURN false; END IF;
 FOR binding IN SELECT value FROM jsonb_array_elements(p_state->'call_bindings') LOOP
   IF jsonb_typeof(binding) IS DISTINCT FROM 'object'
      OR binding-ARRAY['call_id','tool_name','arguments_sha256','step_id','input_sha256']<>'{}'::jsonb
      OR COALESCE(length(binding->>'call_id'),0) NOT BETWEEN 1 AND 200
      OR COALESCE(binding->>'arguments_sha256','') !~ '^[a-f0-9]{64}$'
      OR COALESCE(binding->>'input_sha256','') !~ '^[a-f0-9]{64}$'
      OR COALESCE(binding->>'tool_name','') !~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}$'
      OR COALESCE(binding->>'step_id','') !~ '^host[.]call[.][a-f0-9]{32}$'
   THEN RETURN false; END IF;
   SELECT value INTO step FROM jsonb_array_elements(p_state->'steps') WHERE value->>'id'=binding->>'step_id';
   IF step IS NULL OR jsonb_typeof(step->'requirement_ids') IS DISTINCT FROM 'array'
      OR step->'contributes_to' IS DISTINCT FROM '[]'::jsonb OR step->'depends_on' IS DISTINCT FROM '[]'::jsonb
      OR NULLIF(step->>'capability','') IS NULL THEN RETURN false; END IF;
   IF jsonb_array_length(step->'requirement_ids')<>1 THEN RETURN false; END IF;
   SELECT value INTO requirement FROM jsonb_array_elements(p_state->'requirements')
     WHERE value->>'id'=step#>>'{requirement_ids,0}';
   IF requirement IS NULL OR requirement->>'id' IS DISTINCT FROM replace(step->>'id','host.call.','host.result.')
      OR requirement->'minimum' IS DISTINCT FROM '1'::jsonb
      OR requirement->>'validator' IS DISTINCT FROM 'artifact.exists'
      OR requirement->>'artifact_kind' IS DISTINCT FROM (CASE WHEN binding->>'tool_name'='generate_image' THEN 'image' ELSE 'receipt' END)
   THEN RETURN false; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_state->'steps') s WHERE NOT EXISTS(
     SELECT 1 FROM jsonb_array_elements(p_state->'call_bindings') b WHERE b->>'step_id'=s->>'id'))
   OR EXISTS(SELECT b->>'step_id' FROM jsonb_array_elements(p_state->'call_bindings') b
      GROUP BY b->>'step_id' HAVING count(DISTINCT (b->>'tool_name',b->>'arguments_sha256'))<>1)
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_state->'requirements') r WHERE NOT EXISTS(
     SELECT 1 FROM jsonb_array_elements(p_state->'steps') s WHERE s->'requirement_ids' @> jsonb_build_array(r->>'id')))
 THEN RETURN false; END IF;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.agent_task_direct_records_valid(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_records_valid(jsonb) TO everydayai,everydayai_owner;

CREATE FUNCTION public.agent_task_direct_extension_valid(p_old jsonb,p_new jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF p_old->'model_directed' IS DISTINCT FROM 'true'::jsonb
   OR p_old->>'status' IS DISTINCT FROM 'active'
   OR NOT public.agent_task_direct_records_valid(p_new)
   OR (p_old-ARRAY['revision','requirements','steps','call_bindings','working_plan']) IS DISTINCT FROM
      (p_new-ARRAY['revision','requirements','steps','call_bindings','working_plan'])
 THEN RETURN false; END IF;
 RETURN public.agent_task_json_array_prefix(p_old->'requirements',p_new->'requirements')
   AND public.agent_task_json_array_prefix(p_old->'steps',p_new->'steps')
   AND public.agent_task_json_array_prefix(p_old->'call_bindings',p_new->'call_bindings');
END $$;
REVOKE ALL ON FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb) TO everydayai,everydayai_owner;

ALTER FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb) RENAME TO agent_task_method_extension_valid_before371;
CREATE FUNCTION public.agent_task_method_extension_valid(p_old jsonb,p_new jsonb,p_skills jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT CASE WHEN p_old->'model_directed'='true'::jsonb OR p_new->'model_directed'='true'::jsonb
   THEN public.agent_task_direct_extension_valid(p_old,p_new)
   ELSE public.agent_task_method_extension_valid_before371(p_old,p_new,p_skills) END
$$;
REVOKE ALL ON FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb) TO everydayai,everydayai_owner;

CREATE FUNCTION public.guard_agent_task_execution_policy() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='UPDATE' AND COALESCE(OLD.state->'model_directed','false'::jsonb)
      IS DISTINCT FROM COALESCE(NEW.state->'model_directed','false'::jsonb) THEN
   RAISE EXCEPTION 'TASK_EXECUTION_POLICY_IMMUTABLE' USING ERRCODE='42501';
 END IF;
 IF NEW.state->'model_directed'='true'::jsonb THEN
   IF NOT public.agent_task_direct_records_valid(NEW.state) THEN
     RAISE EXCEPTION 'TASK_DIRECT_RECORD_INVALID' USING ERRCODE='22023';
   END IF;
   IF TG_OP='UPDATE' AND (
       OLD.state->'call_bindings' IS DISTINCT FROM NEW.state->'call_bindings'
       OR OLD.state->'steps' IS DISTINCT FROM NEW.state->'steps')
      AND NOT public.agent_task_direct_extension_valid(OLD.state,NEW.state) THEN
     RAISE EXCEPTION 'TASK_DIRECT_RECORD_IMMUTABLE' USING ERRCODE='42501';
   END IF;
   IF TG_OP='UPDATE' AND NOT public.agent_task_json_array_prefix(
       COALESCE(OLD.state->'resolved_call_bindings','[]'::jsonb),COALESCE(NEW.state->'resolved_call_bindings','[]'::jsonb)) THEN
     RAISE EXCEPTION 'TASK_DIRECT_RESOLVED_BINDING_IMMUTABLE' USING ERRCODE='42501';
   END IF;
   IF TG_OP='UPDATE' AND NOT public.agent_task_json_array_prefix(
       COALESCE(OLD.state->'retired_call_steps','[]'::jsonb),COALESCE(NEW.state->'retired_call_steps','[]'::jsonb)) THEN
     RAISE EXCEPTION 'TASK_DIRECT_RETIREMENT_IMMUTABLE' USING ERRCODE='42501';
   END IF;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(NEW.state->'retired_call_steps','[]'::jsonb)) r(id)
       WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'steps') s WHERE s->>'id'=r.id)
          OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'operations') o WHERE o->>'step_id'=r.id AND o->>'status'<>'failed_before_dispatch')) THEN
     RAISE EXCEPTION 'TASK_DIRECT_RETIREMENT_INVALID' USING ERRCODE='42501';
   END IF;
 ELSIF NEW.state ? 'call_bindings' OR NEW.state ? 'resolved_call_bindings' OR NEW.state ? 'working_plan' OR NEW.state ? 'retired_call_steps'
       OR (NEW.state ? 'model_directed' AND NEW.state->'model_directed' IS DISTINCT FROM 'false'::jsonb) THEN
   RAISE EXCEPTION 'TASK_DIRECT_POLICY_REQUIRED' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_task_execution_policy() FROM PUBLIC;
CREATE TRIGGER agent_task_execution_policy_guard BEFORE INSERT OR UPDATE OF state ON public.agent_task_states
 FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_execution_policy();
