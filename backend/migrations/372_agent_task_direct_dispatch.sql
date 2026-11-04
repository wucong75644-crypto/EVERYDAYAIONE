-- The original inbox/fence/operation chain remains authoritative.
SET LOCAL ROLE everydayai;
ALTER FUNCTION public.agent_task_unreviewed_changes(uuid) RENAME TO agent_task_unreviewed_changes_before372;
CREATE FUNCTION public.agent_task_unreviewed_changes(p_root_task_id uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; cp jsonb; events jsonb;
BEGIN
 events:=public.agent_task_unreviewed_changes_before372(p_root_task_id);
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id;
 IF s.state->'model_directed' IS DISTINCT FROM 'true'::jsonb THEN RETURN events; END IF;
 SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id;
 RETURN (SELECT COALESCE(jsonb_agg(e),'[]'::jsonb) FROM jsonb_array_elements(events) e
   WHERE NOT (COALESCE(cp->'_task_direct_seen','[]'::jsonb) @> jsonb_build_array(e->>'event_id')
     AND EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(cp#>'{_task_user_changes,items}','[]'::jsonb)) i
       WHERE i->>'event_id'=e->>'event_id' AND i->'delivered'='true'::jsonb
         AND i->>'message'=e->>'message' AND i->>'message_sha256'=e->>'message_sha256')));
END $$;
REVOKE ALL ON FUNCTION public.agent_task_unreviewed_changes(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_unreviewed_changes(uuid) TO everydayai;

CREATE FUNCTION public.agent_task_direct_input_hash(p_checkpoint jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT encode(sha256(convert_to('['||COALESCE(string_agg(to_jsonb(value)::text,',' ORDER BY value),'')||']','UTF8')),'hex')
 FROM jsonb_array_elements_text(COALESCE(p_checkpoint->'_task_direct_seen','[]'::jsonb)) item(value)
$$;
REVOKE ALL ON FUNCTION public.agent_task_direct_input_hash(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_input_hash(jsonb) TO everydayai,everydayai_owner;

ALTER FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) RENAME TO begin_agent_task_operation_before372;
CREATE FUNCTION public.begin_agent_task_operation(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,
 p_step_id text,p_capability text,p_effect_key text,p_effect_version integer,p_tool_call_id text,p_tool_name text,p_args_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; cp jsonb;
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 IF s.state->'model_directed'='true'::jsonb THEN
   SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=p_task_id;
   IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'call_bindings') b
     WHERE b->>'call_id'=p_tool_call_id AND b->>'tool_name'=p_tool_name
       AND COALESCE((SELECT r->>'arguments_sha256' FROM jsonb_array_elements(COALESCE(s.state->'resolved_call_bindings','[]'::jsonb)) r
           WHERE r->>'call_id'=p_tool_call_id),b->>'arguments_sha256')=p_args_hash
       AND b->>'step_id'=p_step_id AND b->>'input_sha256'=public.agent_task_direct_input_hash(cp))
     OR COALESCE(s.state->'retired_call_steps','[]'::jsonb) @> jsonb_build_array(p_step_id) THEN
     RAISE EXCEPTION 'TASK_DIRECT_CALL_BINDING_STALE' USING ERRCODE='42501';
   END IF;
 END IF;
 RETURN public.begin_agent_task_operation_before372(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,
   p_step_id,p_capability,p_effect_key,p_effect_version,p_tool_call_id,p_tool_name,p_args_hash);
END $$;
REVOKE ALL ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) TO everydayai;

CREATE OR REPLACE FUNCTION public.register_agent_image_dependency() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    s public.agent_task_states%ROWTYPE;
    origin jsonb;
    op jsonb;
    matches integer;
BEGIN
    IF NEW.type IS DISTINCT FROM 'image' THEN RETURN NEW; END IF;
    origin := NEW.request_params#>'{_media_request_v1,origin}';
    IF origin->>'parent_task_id' IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO s FROM public.agent_task_states
      WHERE owner_task_id::text=origin->>'parent_task_id' FOR SHARE;
    IF NOT FOUND OR (COALESCE(s.state->'model_directed','false'::jsonb)<>'true'::jsonb AND jsonb_array_length(s.state->'method_bindings')=0 AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(s.state->'artifacts') a WHERE a->'validations' @> '["image.source.valid"]'::jsonb
          OR (s.state->>'image_binding_version' IN ('2','3') AND a->'validations' @> '["image.plan.valid"]'::jsonb))) THEN
        RETURN NEW; -- Old/unadapted requests keep their original acceptance.
    END IF;
    IF NEW.user_id IS DISTINCT FROM s.actor_user_id OR NEW.org_id IS DISTINCT FROM s.org_id
       OR NEW.conversation_id IS DISTINCT FROM s.conversation_id
       OR origin->>'actor_user_id' IS DISTINCT FROM s.actor_user_id::text
       OR origin->>'conversation_id' IS DISTINCT FROM s.conversation_id::text
       OR s.state->>'status' IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'TASK_IMAGE_DEPENDENCY_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT count(*) INTO matches FROM jsonb_array_elements(s.state->'operations') item
     WHERE item->>'owner_task_id'=s.owner_task_id::text AND item->>'owner_turn_id'=origin->>'turn_id'
       AND item->>'invocation_id'=origin->>'tool_call_id' AND item->>'tool_name'='generate_image'
       AND item->>'capability'='platform.generate_image' AND item->>'status'='reserved';
    IF matches<>1 THEN
        RAISE EXCEPTION 'TASK_IMAGE_OPERATION_BINDING_REQUIRED' USING ERRCODE='42501';
    END IF;
    SELECT item INTO op FROM jsonb_array_elements(s.state->'operations') item
     WHERE item->>'owner_task_id'=s.owner_task_id::text AND item->>'owner_turn_id'=origin->>'turn_id'
       AND item->>'invocation_id'=origin->>'tool_call_id' AND item->>'tool_name'='generate_image'
       AND item->>'capability'='platform.generate_image' AND item->>'status'='reserved';
    INSERT INTO public.agent_task_events(root_task_id,actor_user_id,org_id,dedupe_key,expected_revision,payload,
        child_task_id,operation_id)
    VALUES(s.root_task_id,s.actor_user_id,s.org_id,'image-child:'||NEW.id::text,s.revision,
        jsonb_build_object('kind','image_child','version',1,'child_task_id',NEW.id,'operation_id',op->>'id',
            'requirements_revision',s.state->'requirements_revision','step_id',op->>'step_id'),NEW.id,op->>'id');
    -- Routing hint only; the event remains authoritative. Existing task scans
    -- can find these children without widening event-table tenant policies.
    UPDATE public.tasks SET request_params=request_params||jsonb_build_object('_agent_task_dependency_v1',
        jsonb_build_object('version',1,'pending',true,'root_task_id',s.root_task_id)) WHERE id=NEW.id;
    RETURN NEW;
END $$;

-- Legacy saved-plan verification remains unchanged for legacy tasks.
DROP TRIGGER agent_task_image_reuse_guard ON public.agent_task_states;
CREATE TRIGGER agent_task_image_reuse_guard BEFORE UPDATE OF state ON public.agent_task_states
 FOR EACH ROW WHEN (NEW.state->'model_directed' IS DISTINCT FROM 'true'::jsonb)
 EXECUTE FUNCTION public.guard_agent_task_image_reuse();
CREATE FUNCTION public.guard_agent_task_direct_image_reuse() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE a jsonb; proof jsonb; recipe jsonb; recipe_artifact jsonb; step jsonb; binding jsonb;
 child public.tasks%ROWTYPE; parent public.tasks%ROWTYPE; m public.messages%ROWTYPE;
BEGIN
 FOR a IN SELECT value FROM jsonb_array_elements(NEW.state->'artifacts') WHERE value ? 'image_reuse'
    AND NOT COALESCE(OLD.state->'artifacts','[]'::jsonb) @> jsonb_build_array(value) LOOP
   proof:=a->'image_reuse';
   SELECT * INTO parent FROM public.tasks WHERE id=NEW.owner_task_id;
   SELECT * INTO child FROM public.tasks WHERE id=(a->>'source_task_id')::uuid;
   SELECT * INTO m FROM public.messages WHERE id=child.assistant_message_id;
   SELECT value INTO recipe_artifact FROM jsonb_array_elements(NEW.state->'artifacts')
     WHERE value->>'id'=proof->>'planning_artifact_id' AND value->'validations' @> '["image.direct.recipe"]'::jsonb;
   recipe:=(recipe_artifact->>'inline_text')::jsonb;
   SELECT value INTO step FROM jsonb_array_elements(NEW.state->'steps') WHERE value->>'id'=a->>'step_id';
   SELECT value INTO binding FROM jsonb_array_elements(NEW.state->'call_bindings')
     WHERE value->>'step_id'=step->>'id' AND value->>'tool_name'='generate_image'
       AND value->>'arguments_sha256'=recipe->>'arguments_sha256' LIMIT 1;
   IF child.id IS NULL OR parent.id IS NULL OR m.id IS NULL OR step IS NULL OR binding IS NULL OR recipe IS NULL
      OR recipe_artifact->>'step_id' IS DISTINCT FROM step->>'id'
      OR child.user_id IS DISTINCT FROM NEW.actor_user_id OR child.org_id IS DISTINCT FROM NEW.org_id
      OR child.conversation_id IS DISTINCT FROM NEW.conversation_id OR child.type IS DISTINCT FROM 'image'
      OR child.status IS DISTINCT FROM 'completed' OR m.status IS DISTINCT FROM 'completed'
      OR child.request_params#>>'{_media_lifecycle_v1,phase}' IS DISTINCT FROM 'published'
      OR child.request_params#>>'{_media_request_v1,origin,actor_user_id}' IS DISTINCT FROM NEW.actor_user_id::text
      OR child.request_params#>>'{_media_request_v1,origin,workspace_owner_id}' IS DISTINCT FROM NEW.actor_user_id::text
      OR child.request_params#>>'{_media_request_v1,origin,context_scope}' IS DISTINCT FROM 'user'
      OR child.request_params#>>'{_media_request_v1,origin,org_id}' IS DISTINCT FROM NEW.org_id::text
      OR m.context_revision<=0 OR m.context_revision>parent.base_context_revision
      OR m.generation_params->>'task_id' IS DISTINCT FROM child.id::text
      OR child.result_data->>'type' IS DISTINCT FROM 'image' OR NULLIF(child.result_data->>'url','') IS NULL
      OR COALESCE(child.result_data->'failed','false'::jsonb) IS DISTINCT FROM 'false'::jsonb
      OR m.content::jsonb->0 IS DISTINCT FROM child.result_data
      OR a->>'reference' IS DISTINCT FROM 'task-image:'||child.id::text OR a->>'kind' IS DISTINCT FROM 'image'
      OR a->>'sha256' IS DISTINCT FROM encode(sha256(convert_to(child.result_data::text,'UTF8')),'hex')
      OR a->>'source_call_id' IS DISTINCT FROM child.request_params#>>'{_media_request_v1,origin,tool_call_id}'
      OR a->>'delivery_task_id' IS DISTINCT FROM NEW.owner_task_id::text
      OR proof->'requirements_revision' IS DISTINCT FROM NEW.state->'requirements_revision'
      OR proof->>'request_sha256' IS DISTINCT FROM child.request_params#>>'{_media_request_v1,request_hash}'
      OR proof->'item_index' IS DISTINCT FROM '1'::jsonb
      OR step->>'capability' IS DISTINCT FROM 'platform.generate_image'
      OR NOT (step->'requirement_ids' @> jsonb_build_array(proof->>'requirement_id'))
      OR public.agent_task_image_references(child.request_params#>'{_media_request_v1,references}') IS DISTINCT FROM
         public.agent_task_image_references(recipe->'resolved')
      OR EXISTS(SELECT 1 FROM unnest(ARRAY['prompt','aspect_ratio','mode','model','resolution','output_format','background','source_prompt','variant_id','plan_item_id']) k
         WHERE recipe->'normalized' ? k AND child.request_params->'_media_request_v1'->k IS DISTINCT FROM recipe->'normalized'->k)
      OR (recipe->'source_proof'<>'null'::jsonb AND child.request_params#>'{_media_request_v1,origin,plan_source}' IS DISTINCT FROM recipe->'source_proof')
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'artifacts') other
         WHERE other->>'kind'='image' AND other->>'source_task_id'=a->>'source_task_id' AND other->>'id'<>a->>'id'
           AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state->'call_bindings') old_binding
             WHERE old_binding->>'step_id'=other->>'step_id' AND old_binding->>'input_sha256'=binding->>'input_sha256')) THEN
     RAISE EXCEPTION 'TASK_IMAGE_REUSE_UNVERIFIED' USING ERRCODE='42501';
   END IF;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_task_direct_image_reuse() FROM PUBLIC;
CREATE TRIGGER agent_task_direct_image_reuse_guard BEFORE UPDATE OF state ON public.agent_task_states
 FOR EACH ROW WHEN (NEW.state->'model_directed'='true'::jsonb) EXECUTE FUNCTION public.guard_agent_task_direct_image_reuse();

-- Count the main model's actual pre-transfer rounds, preserving the original
-- ownership/input/opaque-context checks and the legacy one-review-round path.
DO $$ BEGIN
 IF (SELECT md5(prosrc) FROM pg_proc WHERE oid='public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure) IS DISTINCT FROM 'cb1453a3335657557631c5610600af99' THEN
  RAISE EXCEPTION 'TASK_FOLLOWUP_INSTALL_BODY_CHANGED';
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.adopt_agent_task_followup(p_task_id uuid,p_execution_token uuid,p_root_task_id uuid,p_expected_revision bigint,
 p_source_sha256 text,p_observation jsonb,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE source jsonb; t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; cp jsonb; fresh jsonb; candidate jsonb:=p_payload->'_task_execution';
 saved jsonb; event uuid; directive uuid; checked jsonb; k text; quote text; before_skills jsonb; after_skills jsonb;
BEGIN
 -- Serialize with pause/cancel, new input and internal continuations.
 PERFORM 1 FROM public.conversations WHERE id=(SELECT conversation_id FROM public.tasks WHERE id=p_task_id) FOR UPDATE;
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 IF t.delivery_context->>'input_kind'='agent_task_followup' AND t.delivery_context->>'logical_task_id'=p_root_task_id::text
     AND t.delivery_context->>'source_revision'=p_expected_revision::text AND t.delivery_context->>'source_sha256'=p_source_sha256 THEN
   PERFORM public.read_agent_task_user_change_pending(t.id,p_execution_token,p_root_task_id);
   SELECT state INTO saved FROM public.conversation_turn_checkpoints WHERE task_id=t.id;
   RETURN jsonb_build_object('outcome','saved','payload',saved,'delivery_context',t.delivery_context);
 END IF;
 checked:=public.read_agent_task_followup(p_task_id,p_execution_token);
 source:=checked->'source';
 IF source IS NULL THEN RAISE EXCEPTION 'TASK_FOLLOWUP_SOURCE_CHANGED'; END IF;
 PERFORM 1 FROM public.tasks WHERE id=(source->>'source_owner_task_id')::uuid FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id FOR UPDATE;
 SELECT state INTO fresh FROM public.conversation_turn_checkpoints WHERE task_id=t.id FOR UPDATE;
 cp:=source->'checkpoint';
 IF s.root_task_id IS NULL OR s.root_task_id::text IS DISTINCT FROM source#>>'{state,root_task_id}' OR s.revision IS DISTINCT FROM p_expected_revision
    OR p_source_sha256 IS DISTINCT FROM source->>'source_sha256' OR s.state IS DISTINCT FROM source->'state'
    OR p_observation->>'relation' IS NULL OR p_observation->>'relation' NOT IN ('continue','amend')
    OR jsonb_typeof(p_observation->'evidence') IS DISTINCT FROM 'array' OR jsonb_array_length(p_observation->'evidence') NOT BETWEEN 1 AND 4 THEN
   RAISE EXCEPTION 'TASK_FOLLOWUP_REVIEW_UNCONFIRMED'; END IF;
 FOR quote IN SELECT jsonb_array_elements_text(p_observation->'evidence') LOOP
   IF length(btrim(quote))=0 OR position(quote in (source->>'message'))=0 THEN RAISE EXCEPTION 'TASK_FOLLOWUP_REVIEW_UNCONFIRMED'; END IF;
 END LOOP;
 IF (candidate-ARRAY['revision','status','model_rounds_used']) IS DISTINCT FROM (s.state-ARRAY['revision','status','model_rounds_used'])
    OR candidate->'revision' IS DISTINCT FROM to_jsonb(s.revision+1) OR candidate->>'status' IS DISTINCT FROM 'active'
    OR candidate->'model_rounds_used' IS DISTINCT FROM to_jsonb((s.state->>'model_rounds_used')::int+(CASE WHEN s.state->'model_directed'='true'::jsonb THEN greatest(1,COALESCE((fresh->>'next_model_round')::integer,1)) ELSE 1 END))
    OR p_payload->'_task_followup_input_checkpoint' IS DISTINCT FROM fresh
    OR p_payload->'_task_followup' IS DISTINCT FROM jsonb_build_object('source_root_task_id',s.root_task_id,'input_message_id',t.input_message_id,'input_sha256',encode(sha256(convert_to(source->>'message','UTF8')),'hex'),'observation',p_observation)
    OR COALESCE(p_payload->'_task_followup_window_history','[]') IS DISTINCT FROM
       (COALESCE(cp->'_task_followup_window_history','[]')||CASE WHEN cp ? '_task_followup_input_checkpoint' THEN
          jsonb_build_array(jsonb_build_object('owner_task_id',s.owner_task_id,'checkpoint',cp->'_task_followup_input_checkpoint')) ELSE '[]'::jsonb END)
    OR jsonb_typeof(p_payload->'messages') IS DISTINCT FROM 'array'
    OR jsonb_array_length(p_payload->'messages')<>jsonb_array_length(cp->'messages')+jsonb_array_length(fresh->'messages')+1
    OR p_payload->'messages'->-1 IS DISTINCT FROM jsonb_build_object('role','system','content',CASE WHEN s.state->'model_directed'='true'::jsonb THEN E'[New authenticated user Turn]\n原目标/成果及未决操作继续保留；根据最新真实用户文字调整工作，旧调用不可沿用新授权，禁止重发未决操作。' ELSE E'[New authenticated user Turn]\n原目标/成果及未决操作继续保留；本条新用户文字须经原要求修订核验，禁止沿旧派发或重发未决操作。' END)
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(cp->'messages') WITH ORDINALITY x(value,n) WHERE p_payload->'messages'->((n-1)::int) IS DISTINCT FROM value)
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(fresh->'messages') WITH ORDINALITY x(value,n)
       WHERE p_payload->'messages'->((jsonb_array_length(cp->'messages')+n-1)::int) IS DISTINCT FROM value) THEN
    RAISE EXCEPTION 'TASK_FOLLOWUP_FACTS_CHANGED'; END IF;
 -- Preserve every opaque original checkpoint extension. Only original window
 -- metadata can be rebound; pins, rendered bodies and ceilings cannot change.
 FOR k IN SELECT jsonb_object_keys(cp-ARRAY['messages','content_blocks','checkpoint_kind','result_content','usage','credits_cost','tool_digest',
    '_task_bootstrap','_task_read_arguments','_task_usage','_continuation_input','_task_execution','_task_execution_expected_revision',
    'next_model_round','turn_index','_task_followup','_task_followup_input_checkpoint','_task_followup_window_history','skill_runtime','_skill_composition','_skill_composition_expected_revision','_ecom_workflow']) LOOP
    IF p_payload->k IS DISTINCT FROM cp->k THEN RAISE EXCEPTION 'TASK_FOLLOWUP_CONTEXT_CHANGED'; END IF;
 END LOOP;
 before_skills:=cp->'skill_runtime';after_skills:=p_payload->'skill_runtime';
 IF (before_skills-ARRAY['turn_id','composition']) IS DISTINCT FROM (after_skills-ARRAY['turn_id','composition'])
    OR (before_skills IS NOT NULL AND after_skills->>'turn_id' IS DISTINCT FROM t.turn_id::text)
    OR ((cp->'_skill_composition')-ARRAY['turn_id','revision','workflow_links']) IS DISTINCT FROM ((p_payload->'_skill_composition')-ARRAY['turn_id','revision','workflow_links'])
    OR ((cp->'_ecom_workflow')-'window_task_id') IS DISTINCT FROM ((p_payload->'_ecom_workflow')-'window_task_id') THEN
   RAISE EXCEPTION 'TASK_FOLLOWUP_METHOD_CHANGED'; END IF;
 INSERT INTO public.agent_task_events(root_task_id,actor_user_id,org_id,dedupe_key,expected_revision,payload,status,continuation_task_id)
 VALUES(s.root_task_id,s.actor_user_id,s.org_id,'user-followup:'||t.input_message_id::text,s.revision,
   jsonb_build_object('source_kind','user_followup','input_message_id',t.input_message_id,'observation',p_observation),'queued',t.id) RETURNING id INTO event;
 UPDATE public.tasks SET delivery_context=COALESCE(delivery_context,'{}')||jsonb_build_object('actor',true,'input_kind','agent_task_followup',
    'logical_task_id',s.root_task_id,'event_id',event,'source_owner_task_id',s.owner_task_id,'source_revision',s.revision,'source_sha256',p_source_sha256) WHERE id=t.id;
 saved:=public.save_agent_task_checkpoint(t.id,p_execution_token,'before_model',p_payload,s.revision);
 IF saved->>'outcome' IS DISTINCT FROM 'saved' THEN RAISE EXCEPTION 'TASK_FOLLOWUP_CHECKPOINT_UNCONFIRMED'; END IF;
 -- Real new user text enters the existing authenticated STEER review pipeline.
 directive:=(public.append_conversation_steer(t.conversation_id,t.id,t.turn_id,'user-followup:'||t.input_message_id::text,
    jsonb_build_object('message',source->>'message'))->>'event_id')::uuid;
 PERFORM public.capture_agent_task_user_change(t.id,p_execution_token,directive);
 UPDATE public.agent_task_events SET status='applied' WHERE id=event;
 SELECT state INTO saved FROM public.conversation_turn_checkpoints WHERE task_id=t.id;
 RETURN jsonb_build_object('outcome','saved','payload',saved,'delivery_context',(SELECT delivery_context FROM public.tasks WHERE id=t.id));
END $$;


CREATE FUNCTION public.agent_task_direct_operation_current(p_state jsonb,p_checkpoint jsonb,p_operation_id text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT CASE WHEN p_state->'model_directed' IS DISTINCT FROM 'true'::jsonb THEN true ELSE EXISTS(
   SELECT 1 FROM jsonb_array_elements(p_state->'operations') op
   JOIN jsonb_array_elements(p_state->'call_bindings') b ON b->>'step_id'=op->>'step_id'
   WHERE op->>'id'=p_operation_id AND b->>'tool_name'=op->>'tool_name'
     AND b->>'input_sha256'=public.agent_task_direct_input_hash(p_checkpoint)
     AND NOT COALESCE(p_state->'retired_call_steps','[]'::jsonb) @> jsonb_build_array(op->>'step_id')) END
$$;
REVOKE ALL ON FUNCTION public.agent_task_direct_operation_current(jsonb,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_direct_operation_current(jsonb,jsonb,text) TO everydayai;

DO $install$ DECLARE target regprocedure:=to_regprocedure('public.agent_child_revision_gate(uuid,uuid)'); definition text; BEGIN
 IF target IS NULL THEN RETURN; END IF;
 IF (SELECT md5(prosrc) FROM pg_proc WHERE oid=target)<>'19272a852db0ed4670ee3176f10f39c5' THEN RAISE EXCEPTION 'TASK_DIRECT_NATIVE_GATE_BODY_CHANGED:agent_child_revision_gate'; END IF;
 SELECT pg_get_functiondef(target) INTO definition;
 ALTER FUNCTION public.agent_child_revision_gate(uuid,uuid) RENAME TO agent_child_revision_gate_before372;
 -- Replace only the first normal-path site; exception restoration is unchanged.
 EXECUTE overlay(definition placing $inject$ IF result='ready' AND NOT public.agent_task_direct_operation_current(s.state,(SELECT state FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id),operation_id) THEN result:='pending'; END IF;
 PERFORM set_config('app.actor_user_id',COALESCE(actor_setting,''),true);$inject$ from position($needle$ PERFORM set_config('app.actor_user_id',COALESCE(actor_setting,''),true);$needle$ in definition) for length($needle$ PERFORM set_config('app.actor_user_id',COALESCE(actor_setting,''),true);$needle$));
 REVOKE ALL ON FUNCTION public.agent_child_revision_gate(uuid,uuid) FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION public.agent_child_revision_gate(uuid,uuid) TO everydayai,everydayai_worker,everydayai_owner;
END $install$;

DO $install$ DECLARE target regprocedure:=to_regprocedure('public.authorize_agent_task_file_delete_item(text,uuid,integer)'); definition text; BEGIN
 IF target IS NULL THEN RETURN; END IF;
 IF (SELECT md5(prosrc) FROM pg_proc WHERE oid=target)<>'c4473f3e1431972d95cd0114ab3427e5' THEN RAISE EXCEPTION 'TASK_DIRECT_NATIVE_GATE_BODY_CHANGED:authorize_agent_task_file_delete_item'; END IF;
 SELECT pg_get_functiondef(target) INTO definition;
 ALTER FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer) RENAME TO authorize_agent_task_file_delete_item_before372;
 -- Replace only the first normal-path site; exception restoration is unchanged.
 EXECUTE overlay(definition placing $inject$     IF NOT public.agent_task_direct_operation_current(s.state,(SELECT state FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id),p_operation_id) THEN RAISE EXCEPTION 'TASK_USER_INPUT_CHANGED' USING ERRCODE='42501'; END IF;
     RETURN checked;$inject$ from position($needle$     RETURN checked;$needle$ in definition) for length($needle$     RETURN checked;$needle$));
 REVOKE ALL ON FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer) FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer) TO everydayai;
END $install$;

DO $install$ DECLARE target regprocedure:=to_regprocedure('public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer)'); definition text; BEGIN
 IF target IS NULL THEN RETURN; END IF;
 IF (SELECT md5(prosrc) FROM pg_proc WHERE oid=target)<>'621f9ca743474dce943bdb910dbb6107' THEN RAISE EXCEPTION 'TASK_DIRECT_NATIVE_GATE_BODY_CHANGED:authorize_agent_task_file_delete_resume'; END IF;
 SELECT pg_get_functiondef(target) INTO definition;
 ALTER FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer) RENAME TO authorize_agent_task_file_delete_resume_before372;
 -- Replace only the first normal-path site; exception restoration is unchanged.
 EXECUTE overlay(definition placing $inject$     IF NOT public.agent_task_direct_operation_current(s.state,(SELECT state FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id),p_operation_id) THEN RAISE EXCEPTION 'TASK_USER_INPUT_CHANGED' USING ERRCODE='42501'; END IF;
     RETURN checked;$inject$ from position($needle$     RETURN checked;$needle$ in definition) for length($needle$     RETURN checked;$needle$));
 REVOKE ALL ON FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer) FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer) TO everydayai;
END $install$;
