-- Published native images are facts, not new paid generation effects.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.agent_task_image_references(p_refs jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('content_sha256',r->'content_sha256','role',r->'role') ORDER BY n),'[]')
 FROM jsonb_array_elements(p_refs) WITH ORDINALITY x(r,n)
$$;
REVOKE ALL ON FUNCTION public.agent_task_image_references(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_image_references(jsonb) TO everydayai;
CREATE FUNCTION public.read_agent_task_reusable_images(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_source_task_id uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; parent public.tasks%ROWTYPE; images jsonb;
BEGIN
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 SELECT * INTO parent FROM public.tasks WHERE id=p_task_id;
 SELECT COALESCE(jsonb_agg(value ORDER BY created_at DESC),'[]') INTO images FROM (
   SELECT child.created_at,jsonb_build_object('task_id',child.id,'message_id',m.id,'content_index',0,'part',child.result_data,
     'part_sha256',encode(sha256(convert_to(child.result_data::text,'UTF8')),'hex'),'request',child.request_params->'_media_request_v1') value
   FROM public.tasks child JOIN public.messages m ON m.id=child.assistant_message_id
   WHERE child.type='image' AND child.user_id=parent.user_id AND child.org_id IS NOT DISTINCT FROM parent.org_id
     AND child.conversation_id=parent.conversation_id AND child.status='completed' AND m.status='completed'
     AND m.context_revision>0 AND m.context_revision<=parent.base_context_revision
     AND child.request_params#>>'{_media_lifecycle_v1,phase}'='published'
     AND child.result_data->>'type'='image' AND NULLIF(child.result_data->>'url','') IS NOT NULL
     AND COALESCE(child.result_data->'failed','false')='false'::jsonb
     AND m.content::jsonb->0=child.result_data AND m.generation_params->>'task_id'=child.id::text
     AND child.request_params#>>'{_media_request_v1,origin,conversation_id}'=parent.conversation_id::text
     AND child.request_params#>>'{_media_request_v1,origin,actor_user_id}'=parent.user_id::text
     AND child.request_params#>>'{_media_request_v1,origin,workspace_owner_id}'=parent.user_id::text
     AND child.request_params#>>'{_media_request_v1,origin,context_scope}'='user'
     AND child.request_params#>>'{_media_request_v1,origin,org_id}' IS NOT DISTINCT FROM parent.org_id::text
     AND (p_source_task_id IS NULL OR child.id=p_source_task_id)
   ORDER BY child.created_at DESC LIMIT 100
 ) candidates;
 RETURN jsonb_build_object('outcome','found','images',images);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_reusable_images(uuid,uuid,uuid,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_reusable_images(uuid,uuid,uuid,bigint,uuid) TO everydayai;
CREATE FUNCTION public.guard_agent_task_image_reuse() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE a jsonb; proof jsonb; child public.tasks%ROWTYPE; parent public.tasks%ROWTYPE; m public.messages%ROWTYPE;
 plan jsonb; brief jsonb; binding jsonb; step jsonb; req jsonb; idx integer; native_plan jsonb; native_item jsonb; expected_prompt text;
BEGIN
 FOR a IN SELECT value FROM jsonb_array_elements(NEW.state->'artifacts') WHERE value ? 'image_reuse'
    AND NOT COALESCE(OLD.state->'artifacts','[]') @> jsonb_build_array(value) LOOP
   proof:=a->'image_reuse';idx:=(proof->>'item_index')::int;
   SELECT * INTO parent FROM public.tasks WHERE id=NEW.owner_task_id;
   SELECT * INTO child FROM public.tasks WHERE id=(a->>'source_task_id')::uuid;
   SELECT * INTO m FROM public.messages WHERE id=child.assistant_message_id;
   SELECT value INTO plan FROM jsonb_array_elements(NEW.state->'artifacts') WHERE value->>'id'=proof->>'planning_artifact_id';
   SELECT value INTO step FROM jsonb_array_elements(NEW.state->'steps') WHERE value->>'id'=a->>'step_id';
   SELECT value INTO req FROM jsonb_array_elements(NEW.state->'requirements') WHERE value->>'id'=proof->>'requirement_id';
   SELECT (value->>'inline_text')::jsonb INTO binding FROM jsonb_array_elements(NEW.state->'artifacts') WITH ORDINALITY x(value,n)
     WHERE value->'validations' @> '["image.resources.bound"]' AND (value->>'inline_text')::jsonb->>'planning_artifact_id'=plan->>'id'
       AND ((value->>'inline_text')::jsonb->>'image_item_index')::int=idx ORDER BY n DESC LIMIT 1;
   brief:=(plan->>'inline_text')::jsonb->'image_plans'->(idx-1);
   native_plan:=NULL;native_item:=NULL;
   expected_prompt:=(brief->>'prompt')||E'\n\n负面提示词：\n'||(brief->>'negative_prompt');
   IF (plan->>'inline_text')::jsonb->>'schema_version'='saved-image-prompts.v1' THEN
     expected_prompt:=brief->>'prompt';
     IF plan->>'reference' IS DISTINCT FROM 'legacy-message:'||(brief#>>'{source_prompt,message_id}')
        OR child.request_params#>>'{_media_request_v1,plan_item_id}' IS DISTINCT FROM brief->>'plan_item_id'
        OR ((child.request_params#>'{_media_request_v1,source_prompt}')-'start'-'end') IS DISTINCT FROM brief->'source_prompt' THEN
       RAISE EXCEPTION 'TASK_IMAGE_REUSE_SOURCE_UNVERIFIED' USING ERRCODE='42501'; END IF;
   ELSIF brief->'plan_source' IS NOT NULL AND brief->'plan_source'<>'null'::jsonb THEN
     SELECT to_jsonb(p) INTO native_plan FROM public.ecom_image_plans p
       WHERE p.id=(brief#>>'{plan_source,plan_id}')::uuid AND p.plan_revision=(brief#>>'{plan_source,revision}')::int
         AND p.user_id=NEW.actor_user_id AND p.org_id IS NOT DISTINCT FROM NEW.org_id AND p.conversation_id=NEW.conversation_id AND p.status='ready';
     SELECT value INTO native_item FROM jsonb_array_elements(native_plan->'items') WHERE value->>'item_id'=brief#>>'{plan_source,item_id}';
     expected_prompt:=native_item->>'request_text';
     IF native_item IS NULL OR expected_prompt IS NULL
        OR encode(sha256(convert_to(expected_prompt,'UTF8')),'hex') IS DISTINCT FROM native_item->>'request_text_sha256'
        OR plan->>'reference' IS DISTINCT FROM 'legacy-plan:'||(native_plan->>'id')||':'||(native_plan->>'plan_revision')
        OR child.request_params#>'{_media_request_v1,origin,plan_source}' IS DISTINCT FROM (brief->'plan_source')||jsonb_build_object('request_text_sha256',native_item->>'request_text_sha256') THEN
       RAISE EXCEPTION 'TASK_IMAGE_REUSE_PLAN_UNVERIFIED' USING ERRCODE='42501'; END IF;
     SELECT jsonb_build_object('mode','image_to_image','resolved',jsonb_agg(frozen||jsonb_build_object('role',selected->>'role') ORDER BY n)) INTO binding
       FROM jsonb_array_elements(native_item->'references') WITH ORDINALITY x(selected,n)
       JOIN LATERAL jsonb_array_elements(native_plan#>'{input_snapshot,resolved_references}') frozen ON frozen->>'source_id'=selected->>'source_id';
     IF jsonb_array_length(COALESCE(binding->'resolved','[]'))<>jsonb_array_length(native_item->'references') THEN
       RAISE EXCEPTION 'TASK_IMAGE_REUSE_PLAN_UNVERIFIED' USING ERRCODE='42501'; END IF;
   END IF;
   IF child.id IS NULL OR parent.id IS NULL OR child.user_id<>NEW.actor_user_id OR child.org_id IS DISTINCT FROM NEW.org_id
      OR child.conversation_id<>NEW.conversation_id OR child.status<>'completed'
      OR child.request_params#>>'{_media_request_v1,origin,actor_user_id}' IS DISTINCT FROM NEW.actor_user_id::text
      OR child.request_params#>>'{_media_request_v1,origin,workspace_owner_id}' IS DISTINCT FROM NEW.actor_user_id::text
      OR child.request_params#>>'{_media_request_v1,origin,context_scope}' IS DISTINCT FROM 'user'
      OR child.request_params#>>'{_media_request_v1,origin,org_id}' IS DISTINCT FROM NEW.org_id::text
      OR COALESCE(child.result_data->'failed','false') IS DISTINCT FROM 'false'::jsonb
      OR child.request_params#>>'{_media_lifecycle_v1,phase}' IS DISTINCT FROM 'published'
      OR m.status IS DISTINCT FROM 'completed' OR m.context_revision IS NULL OR m.context_revision<=0 OR m.context_revision>parent.base_context_revision
      OR m.content::jsonb->0 IS DISTINCT FROM child.result_data OR m.generation_params->>'task_id' IS DISTINCT FROM child.id::text
      OR child.result_data->>'type' IS DISTINCT FROM 'image' OR NULLIF(child.result_data->>'url','') IS NULL
      OR a->>'reference' IS DISTINCT FROM 'task-image:'||child.id::text
      OR a->>'sha256' IS DISTINCT FROM encode(sha256(convert_to(child.result_data::text,'UTF8')),'hex')
      OR a->>'delivery_task_id' IS DISTINCT FROM NEW.owner_task_id::text OR a->>'kind' IS DISTINCT FROM 'image'
      OR a->>'source_call_id' IS DISTINCT FROM child.request_params#>>'{_media_request_v1,origin,tool_call_id}'
      OR proof->'requirements_revision' IS DISTINCT FROM NEW.state->'requirements_revision' OR proof->>'request_sha256' IS DISTINCT FROM child.request_params#>>'{_media_request_v1,request_hash}'
      OR step IS NULL OR step->>'capability' IS DISTINCT FROM 'platform.generate_image' OR req->>'artifact_kind' IS DISTINCT FROM 'image'
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'artifacts') other
          WHERE other->>'kind'='image' AND other->>'source_task_id'=a->>'source_task_id' AND other->>'id'<>a->>'id'
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'steps') current_step WHERE current_step->>'id'=other->>'step_id'))
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'artifacts') other
         WHERE other ? 'image_reuse' AND other->>'id'<>a->>'id'
           AND other#>'{image_reuse,requirements_revision}'=proof->'requirements_revision'
           AND (other->>'source_task_id'=a->>'source_task_id' OR (other#>>'{image_reuse,requirement_id}'=proof->>'requirement_id'
               AND other#>'{image_reuse,item_index}'=proof->'item_index')))
      OR NOT (((step->'requirement_ids')||(step->'contributes_to')) @> jsonb_build_array(req->>'id'))
      OR idx<1 OR brief IS NULL OR (step->>'image_item_index' IS NOT NULL AND (step->>'image_item_index')::int<>idx)
      OR binding IS NULL OR plan IS NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'results') r
          WHERE r->'artifact_ids' @> jsonb_build_array(plan->>'id'))
      OR child.request_params#>>'{_media_request_v1,prompt}' IS DISTINCT FROM expected_prompt
      OR child.request_params#>>'{_media_request_v1,aspect_ratio}' IS DISTINCT FROM brief->>'aspect_ratio'
      OR child.request_params#>>'{_media_request_v1,mode}' IS DISTINCT FROM binding->>'mode'
      OR public.agent_task_image_references(child.request_params#>'{_media_request_v1,references}') IS DISTINCT FROM public.agent_task_image_references(binding->'resolved') THEN
       RAISE EXCEPTION 'TASK_IMAGE_REUSE_UNVERIFIED' USING ERRCODE='42501'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_task_image_reuse() FROM PUBLIC;
CREATE TRIGGER agent_task_image_reuse_guard BEFORE UPDATE OF state ON public.agent_task_states FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_image_reuse();
ALTER FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) RENAME TO begin_agent_task_operation_before366;
CREATE FUNCTION public.begin_agent_task_operation(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,
 p_step_id text,p_capability text,p_effect_key text,p_effect_version integer,p_tool_call_id text,p_tool_name text,p_args_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE;
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 IF p_capability='platform.generate_image' AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'artifacts') a
   WHERE a ? 'image_reuse' AND a#>'{image_reuse,requirements_revision}'=s.state->'requirements_revision'
     AND p_effect_key='delivery-'||encode(sha256(convert_to('['||to_jsonb(a#>>'{image_reuse,requirement_id}')::text||','||(a#>>'{image_reuse,item_index}')||']','UTF8')),'hex')) THEN
   RAISE EXCEPTION 'TASK_IMAGE_SLOT_ALREADY_REUSED' USING ERRCODE='42501'; END IF;
 RETURN public.begin_agent_task_operation_before366(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_step_id,p_capability,
   p_effect_key,p_effect_version,p_tool_call_id,p_tool_name,p_args_hash);
END $$;
REVOKE ALL ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) TO everydayai;
RESET ROLE;
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_task_terminal_images(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; parent public.tasks%ROWTYPE; children jsonb;
BEGIN
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 SELECT * INTO parent FROM public.tasks WHERE id=p_task_id;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('operation_id',e.operation_id,'child_task_id',child.id,'user_id',child.user_id,
   'org_id',child.org_id,'conversation_id',child.conversation_id,'status',child.status,
   'phase',child.request_params#>>'{_media_lifecycle_v1,phase}','origin',child.request_params#>'{_media_request_v1,origin}',
   'part',child.result_data,'part_sha256',encode(sha256(convert_to(child.result_data::text,'UTF8')),'hex'),
   'message_id',m.id,'message_status',m.status,'message_content',m.content,'message_revision',m.context_revision,
   'generation_task_id',m.generation_params->>'task_id') ORDER BY child.created_at,child.id),'[]') INTO children
 FROM public.agent_task_states s JOIN public.agent_task_events e ON e.root_task_id=s.root_task_id
 JOIN public.tasks child ON child.id=e.child_task_id JOIN public.messages m ON m.id=child.assistant_message_id
 WHERE s.root_task_id=p_logical_task_id AND child.user_id=s.actor_user_id AND child.org_id IS NOT DISTINCT FROM s.org_id
   AND child.conversation_id=s.conversation_id AND child.status IN ('completed','failed','cancelled')
   AND m.context_revision>0 AND m.context_revision<=parent.base_context_revision
   AND (child.status<>'completed' OR child.request_params#>>'{_media_lifecycle_v1,phase}'='published')
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'operations') op WHERE op->>'id'=e.operation_id AND op->>'status'='accepted');
 RETURN jsonb_build_object('outcome','found','children',children,'base_revision',parent.base_context_revision);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_terminal_images(uuid,uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_terminal_images(uuid,uuid,uuid,bigint) TO everydayai;
RESET ROLE;
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.settle_agent_task_closed_image_events() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 UPDATE public.agent_task_events e SET status='applied'
 FROM public.tasks child
 WHERE e.root_task_id=NEW.root_task_id AND e.child_task_id=child.id AND e.status='pending'
   AND child.user_id=NEW.actor_user_id AND child.org_id IS NOT DISTINCT FROM NEW.org_id AND child.conversation_id=NEW.conversation_id
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(OLD.state->'operations') before_op,
       jsonb_array_elements(NEW.state->'operations') after_op
     WHERE before_op->>'id'=e.operation_id AND after_op->>'id'=e.operation_id AND before_op->>'status'='accepted'
       AND ((after_op->>'status'='failed_after_dispatch' AND child.status IN ('failed','cancelled'))
         OR (after_op->>'status'='completed' AND child.status='completed'
           AND child.request_params#>>'{_media_lifecycle_v1,phase}'='published'
           AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'artifacts') a WHERE a->>'source_task_id'=child.id::text
             AND a->>'reference'='task-image:'||child.id::text
             AND a->>'sha256'=encode(sha256(convert_to(child.result_data::text,'UTF8')),'hex')))));
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.settle_agent_task_closed_image_events() FROM PUBLIC;
CREATE TRIGGER agent_task_closed_image_events AFTER UPDATE OF state ON public.agent_task_states FOR EACH ROW EXECUTE FUNCTION public.settle_agent_task_closed_image_events();
RESET ROLE;
