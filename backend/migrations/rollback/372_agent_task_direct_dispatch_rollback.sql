SET LOCAL ROLE everydayai;
DO $$ BEGIN
 BEGIN
  ALTER TABLE public.agent_task_states ADD CONSTRAINT agent_task_direct_dispatch_rollback_guard
    CHECK (state->'model_directed' IS DISTINCT FROM 'true'::jsonb);
 EXCEPTION WHEN check_violation THEN
  RAISE EXCEPTION 'TASK_DIRECT_COMPATIBLE_RUNTIME_REQUIRED';
 END;
END $$;

-- Optional notes/bootstrap may exist before the first concrete call/state row.
-- Validate all checkpoint tenants too; retain the original owner arrangement.
DO $$ DECLARE checkpoint_owner text; BEGIN
 SELECT pg_get_userbyid(relowner) INTO checkpoint_owner FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 IF checkpoint_owner NOT IN ('everydayai','everydayai_owner') THEN RAISE EXCEPTION 'TASK_CHECKPOINT_OWNER_UNSUPPORTED'; END IF;
 EXECUTE format('SET LOCAL ROLE %I',checkpoint_owner);
 BEGIN
  ALTER TABLE public.conversation_turn_checkpoints ADD CONSTRAINT agent_task_direct_dispatch_rollback_guard_checkpoint
    CHECK (state#>'{_task_bootstrap,input,model_directed}' IS DISTINCT FROM 'true'::jsonb
      AND state#>'{_task_execution,model_directed}' IS DISTINCT FROM 'true'::jsonb
      AND NOT state ? '_task_direct_plan' AND NOT state ? '_task_direct_seen');
 EXCEPTION WHEN check_violation THEN RAISE EXCEPTION 'TASK_DIRECT_COMPATIBLE_RUNTIME_REQUIRED'; END;
 SET LOCAL ROLE everydayai;
END $$;

-- This intentionally retains 371 and its compatible decoder requirement.
DROP FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text);
ALTER FUNCTION public.begin_agent_task_operation_before372(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) RENAME TO begin_agent_task_operation;
DROP FUNCTION public.agent_task_unreviewed_changes(uuid);
ALTER FUNCTION public.agent_task_unreviewed_changes_before372(uuid) RENAME TO agent_task_unreviewed_changes;

DO $$ BEGIN IF to_regprocedure('public.authorize_agent_task_file_delete_resume_before372(uuid,uuid,uuid,bigint,text,text,integer)') IS NOT NULL THEN DROP FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer); ALTER FUNCTION public.authorize_agent_task_file_delete_resume_before372(uuid,uuid,uuid,bigint,text,text,integer) RENAME TO authorize_agent_task_file_delete_resume; END IF; END $$;
DO $$ BEGIN IF to_regprocedure('public.authorize_agent_task_file_delete_item_before372(text,uuid,integer)') IS NOT NULL THEN DROP FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer); ALTER FUNCTION public.authorize_agent_task_file_delete_item_before372(text,uuid,integer) RENAME TO authorize_agent_task_file_delete_item; END IF; END $$;
DO $$ BEGIN IF to_regprocedure('public.agent_child_revision_gate_before372(uuid,uuid)') IS NOT NULL THEN DROP FUNCTION public.agent_child_revision_gate(uuid,uuid); ALTER FUNCTION public.agent_child_revision_gate_before372(uuid,uuid) RENAME TO agent_child_revision_gate; END IF; END $$;
DROP FUNCTION public.agent_task_direct_operation_current(jsonb,jsonb,text);
DROP FUNCTION public.agent_task_direct_input_hash(jsonb);
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
    IF NOT FOUND OR (jsonb_array_length(s.state->'method_bindings')=0 AND NOT EXISTS(
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
DROP TRIGGER agent_task_direct_image_reuse_guard ON public.agent_task_states;
DROP FUNCTION public.guard_agent_task_direct_image_reuse();
DROP TRIGGER agent_task_image_reuse_guard ON public.agent_task_states;
CREATE TRIGGER agent_task_image_reuse_guard BEFORE UPDATE OF state ON public.agent_task_states
 FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_image_reuse();
ALTER TABLE public.agent_task_states DROP CONSTRAINT agent_task_direct_dispatch_rollback_guard;
DO $$ DECLARE checkpoint_owner text; BEGIN
 SELECT pg_get_userbyid(relowner) INTO checkpoint_owner FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',checkpoint_owner);
 ALTER TABLE public.conversation_turn_checkpoints DROP CONSTRAINT agent_task_direct_dispatch_rollback_guard_checkpoint;
 SET LOCAL ROLE everydayai;
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
    OR candidate->'model_rounds_used' IS DISTINCT FROM to_jsonb((s.state->>'model_rounds_used')::int+1)
    OR p_payload->'_task_followup_input_checkpoint' IS DISTINCT FROM fresh
    OR p_payload->'_task_followup' IS DISTINCT FROM jsonb_build_object('source_root_task_id',s.root_task_id,'input_message_id',t.input_message_id,'input_sha256',encode(sha256(convert_to(source->>'message','UTF8')),'hex'),'observation',p_observation)
    OR COALESCE(p_payload->'_task_followup_window_history','[]') IS DISTINCT FROM
       (COALESCE(cp->'_task_followup_window_history','[]')||CASE WHEN cp ? '_task_followup_input_checkpoint' THEN
          jsonb_build_array(jsonb_build_object('owner_task_id',s.owner_task_id,'checkpoint',cp->'_task_followup_input_checkpoint')) ELSE '[]'::jsonb END)
    OR jsonb_typeof(p_payload->'messages') IS DISTINCT FROM 'array'
    OR jsonb_array_length(p_payload->'messages')<>jsonb_array_length(cp->'messages')+jsonb_array_length(fresh->'messages')+1
    OR p_payload->'messages'->-1 IS DISTINCT FROM jsonb_build_object('role','system','content',E'[New authenticated user Turn]\n原目标/成果及未决操作继续保留；本条新用户文字须经原要求修订核验，禁止沿旧派发或重发未决操作。')
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
