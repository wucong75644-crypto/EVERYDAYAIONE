-- Reuse the existing atomic local-text save guard for generic content objects.
-- This projection is validation-only: stored facts, ownership, native dispatch,
-- confirmation and billing are unchanged.
SET LOCAL ROLE everydayai;

CREATE FUNCTION public.agent_task_content_record_projection(p_state jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE selected jsonb; result jsonb := p_state; values_json jsonb;
BEGIN
    IF p_state->'model_directed' IS DISTINCT FROM 'true'::jsonb THEN RETURN p_state; END IF;
    IF jsonb_typeof(p_state->'call_bindings') IS DISTINCT FROM 'array' THEN RETURN NULL; END IF;
    SELECT COALESCE(jsonb_agg(b->>'step_id'),'[]'::jsonb) INTO selected
      FROM jsonb_array_elements(p_state->'call_bindings') b WHERE b->>'tool_name'='save_task_text';
    IF selected='[]'::jsonb THEN RETURN p_state; END IF;
    IF jsonb_typeof(p_state->'steps') IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_state->'artifacts') IS DISTINCT FROM 'array' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_state->'steps') s
       WHERE (selected ? (s->>'id')) IS DISTINCT FROM (s->>'capability'='task.text.save')
         AND (selected ? (s->>'id') OR s->>'capability'='task.text.save')) THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_state->'artifacts') a
       WHERE selected ? (a->>'step_id') AND (a->>'kind' IS DISTINCT FROM 'text'
         OR NOT COALESCE(a->'validations' @> '["content.object.v2"]'::jsonb,false))) THEN RETURN NULL; END IF;
    SELECT jsonb_agg(CASE WHEN b->>'tool_name'='save_task_text'
        THEN jsonb_set(b,'{tool_name}','"save_task_image_plan"') ELSE b END ORDER BY n)
      INTO values_json FROM jsonb_array_elements(p_state->'call_bindings') WITH ORDINALITY v(b,n);
    result:=jsonb_set(result,'{call_bindings}',values_json);
    SELECT jsonb_agg(CASE WHEN selected ? (s->>'id')
        THEN jsonb_set(s,'{capability}','"task.image_plan.save"') ELSE s END ORDER BY n)
      INTO values_json FROM jsonb_array_elements(p_state->'steps') WITH ORDINALITY v(s,n);
    result:=jsonb_set(result,'{steps}',COALESCE(values_json,'[]'::jsonb));
    SELECT jsonb_agg(CASE WHEN selected ? (a->>'step_id')
        THEN jsonb_set(a,'{validations}',(a->'validations') || '["image.plan.valid","image.direct.plan"]'::jsonb) ELSE a END ORDER BY n)
      INTO values_json FROM jsonb_array_elements(p_state->'artifacts') WITH ORDINALITY v(a,n);
    RETURN jsonb_set(result,'{artifacts}',COALESCE(values_json,'[]'::jsonb));
END $$;
REVOKE ALL ON FUNCTION public.agent_task_content_record_projection(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_content_record_projection(jsonb) TO everydayai,everydayai_owner;

ALTER FUNCTION public.agent_task_direct_records_valid(jsonb) RENAME TO agent_task_direct_records_valid_before377;
CREATE FUNCTION public.agent_task_direct_records_valid(p_state jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
    SELECT COALESCE(public.agent_task_direct_records_valid_before377(public.agent_task_content_record_projection(p_state)),false)
$$;
REVOKE ALL ON FUNCTION public.agent_task_direct_records_valid(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_records_valid(jsonb) TO everydayai,everydayai_owner;

ALTER FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb) RENAME TO agent_task_direct_extension_valid_before377;
CREATE FUNCTION public.agent_task_direct_extension_valid(p_old jsonb,p_new jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
    SELECT COALESCE(public.agent_task_direct_extension_valid_before377(
        public.agent_task_content_record_projection(p_old),public.agent_task_content_record_projection(p_new)),false)
$$;
REVOKE ALL ON FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb) TO everydayai,everydayai_owner;
