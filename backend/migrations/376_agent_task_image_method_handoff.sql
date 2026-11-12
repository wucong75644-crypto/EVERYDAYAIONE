-- Permit a local, completed text save in a model-directed task checkpoint.
-- Native dispatch and operation reservation continue to use their own guards.
SET LOCAL ROLE everydayai;

ALTER FUNCTION public.agent_task_direct_records_valid(jsonb)
    RENAME TO agent_task_direct_records_valid_before376;

CREATE FUNCTION public.agent_task_direct_records_valid(p_state jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE normalized jsonb; plan_steps jsonb; requirements jsonb;
BEGIN
    IF jsonb_typeof(p_state->'call_bindings') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    SELECT COALESCE(jsonb_agg(b->>'step_id'), '[]'::jsonb) INTO plan_steps
      FROM jsonb_array_elements(p_state->'call_bindings') b WHERE b->>'tool_name'='save_task_image_plan';
    IF plan_steps='[]'::jsonb THEN
        RETURN public.agent_task_direct_records_valid_before376(p_state);
    END IF;
    IF jsonb_typeof(p_state->'requirements') IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_state->'steps') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_state->'steps') s
        WHERE (plan_steps ? (s->>'id')) IS DISTINCT FROM (s->>'capability'='task.image_plan.save')
          AND (plan_steps ? (s->>'id') OR s->>'capability'='task.image_plan.save')) THEN
        RETURN false;
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_state->'requirements') r
        WHERE plan_steps ? replace(r->>'id','host.result.','host.call.')
          AND r->>'artifact_kind' IS DISTINCT FROM 'text') THEN RETURN false; END IF;
    SELECT jsonb_agg(CASE WHEN plan_steps ? replace(r->>'id','host.result.','host.call.')
              THEN jsonb_set(r,'{artifact_kind}','"receipt"') ELSE r END ORDER BY n)
      INTO requirements FROM jsonb_array_elements(p_state->'requirements') WITH ORDINALITY v(r,n);
    normalized := jsonb_set(p_state,'{requirements}',COALESCE(requirements,'[]'::jsonb));
    RETURN public.agent_task_direct_records_valid_before376(normalized);
END $$;
REVOKE ALL ON FUNCTION public.agent_task_direct_records_valid(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_records_valid(jsonb) TO everydayai,everydayai_owner;

ALTER FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb)
    RENAME TO agent_task_direct_extension_valid_before376;

CREATE FUNCTION public.agent_task_direct_extension_valid(p_old jsonb,p_new jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE new_save record; artifact jsonb; result jsonb; stripped jsonb := p_new; plan_count integer := 0;
BEGIN
    IF public.agent_task_direct_extension_valid_before376(p_old,p_new) THEN RETURN true; END IF;
    IF NOT public.agent_task_direct_records_valid(p_new)
       OR NOT public.agent_task_json_array_prefix(p_old->'artifacts',p_new->'artifacts')
       OR NOT public.agent_task_json_array_prefix(p_old->'results',p_new->'results') THEN RETURN false; END IF;
    FOR new_save IN SELECT b FROM jsonb_array_elements(p_new->'call_bindings') b
        WHERE b->>'tool_name'='save_task_image_plan' AND NOT p_old->'call_bindings' @> jsonb_build_array(b)
    LOOP
        plan_count := plan_count + 1;
        artifact := p_new->'artifacts'->jsonb_array_length(p_old->'artifacts');
        result := p_new->'results'->jsonb_array_length(p_old->'results');
        IF plan_count<>1 OR artifact->>'step_id' IS DISTINCT FROM new_save.b->>'step_id'
           OR artifact->>'kind' IS DISTINCT FROM 'text'
           OR artifact->>'source_call_id' IS DISTINCT FROM new_save.b->>'call_id'
           OR artifact->>'reference' IS DISTINCT FROM ('task-text:' || (p_new->>'root_task_id') || ':' || (artifact->>'id'))
           OR NULLIF(artifact->>'inline_text','') IS NULL
           OR artifact->>'sha256' IS DISTINCT FROM encode(sha256(convert_to(artifact->>'inline_text','UTF8')),'hex')
           OR NOT COALESCE(artifact->'validations' @> '["artifact.exists","image.plan.valid","image.direct.plan"]'::jsonb,false)
           OR result->>'step_id' IS DISTINCT FROM artifact->>'step_id'
           OR result->'artifact_ids' IS DISTINCT FROM jsonb_build_array(artifact->>'id')
           OR result->>'completed_task_id' IS DISTINCT FROM artifact->>'delivery_task_id' THEN RETURN false; END IF;
        stripped := jsonb_set(jsonb_set(p_new,'{artifacts}',(p_new->'artifacts')-jsonb_array_length(p_old->'artifacts')),
                              '{results}',(p_new->'results')-jsonb_array_length(p_old->'results'));
    END LOOP;
    RETURN plan_count=1 AND public.agent_task_direct_extension_valid_before376(p_old,stripped);
END $$;
REVOKE ALL ON FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb) TO everydayai,everydayai_owner;
