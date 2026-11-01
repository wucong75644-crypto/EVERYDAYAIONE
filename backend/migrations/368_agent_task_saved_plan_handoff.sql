-- A saved task plan is an exact source, never a fresh analysis or old review grant.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.guard_agent_task_saved_plan_source() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE a jsonb; source jsonb; response jsonb; source_root uuid; source_key text; parent public.tasks%ROWTYPE;
BEGIN
 FOR a IN SELECT value FROM jsonb_array_elements(NEW.state->'artifacts')
   WHERE value->>'reference' LIKE 'task-source:%' AND NOT COALESCE(OLD.state->'artifacts','[]') @> jsonb_build_array(value) LOOP
   source_root:=split_part(a->>'reference',':',2)::uuid;source_key:=substring(a->>'reference' from 50);
   source:=NULL;
   IF source_root=OLD.root_task_id THEN
     SELECT value INTO source FROM jsonb_array_elements(OLD.state->'artifacts') x(value)
       WHERE value->>'id'=source_key AND EXISTS(SELECT 1 FROM jsonb_array_elements(OLD.state->'results') r
         WHERE r->'artifact_ids' @> jsonb_build_array(source_key));
   ELSE
     SELECT * INTO parent FROM public.tasks WHERE id=NEW.owner_task_id;
     response:=public.read_agent_task_text_artifacts(NEW.owner_task_id,parent.execution_token,OLD.root_task_id,OLD.revision,source_root,source_key,1);
     IF response->>'outcome'='found' THEN source:=response#>'{artifacts,0,artifact}';END IF;
   END IF;
   IF source IS NULL OR source->>'kind' IS DISTINCT FROM 'text' OR source->>'inline_text' IS DISTINCT FROM a->>'inline_text'
     OR source->>'sha256' IS DISTINCT FROM a->>'sha256'
     OR source->>'source_task_id' IS DISTINCT FROM a->>'source_task_id' OR source->>'source_call_id' IS DISTINCT FROM a->>'source_call_id'
     OR a->>'reference' IS DISTINCT FROM 'task-source:'||source_root::text||':'||source_key
     OR a->>'delivery_task_id' IS DISTINCT FROM NEW.owner_task_id::text
     OR NOT COALESCE(a->'validations','[]') @> '["image.source.valid"]'::jsonb
     OR jsonb_typeof((a->>'inline_text')::jsonb->'image_plans') IS DISTINCT FROM 'array'
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'steps') step,
       jsonb_array_elements(NEW.state->'requirements') req WHERE step->>'id'=a->>'step_id'
       AND req->>'validator'='image.source.valid' AND ((step->'requirement_ids')||(step->'contributes_to')) @> jsonb_build_array(req->>'id')) THEN
       RAISE EXCEPTION 'TASK_SAVED_PLAN_SOURCE_UNVERIFIED' USING ERRCODE='42501'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_task_saved_plan_source() FROM PUBLIC;
CREATE TRIGGER agent_task_saved_plan_source_guard BEFORE UPDATE OF state ON public.agent_task_states
FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_saved_plan_source();
RESET ROLE;
