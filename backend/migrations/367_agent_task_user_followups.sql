-- New authenticated user Turns can acquire a closed logical task through the
-- original owner/event/checkpoint protocol. No parallel actor or new queue.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_task_followup(p_task_id uuid,p_execution_token uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; cp jsonb; text_value text;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 IF SESSION_USER<>'everydayai' OR t.id IS NULL OR t.type IS DISTINCT FROM 'chat' OR t.status IS DISTINCT FROM 'running'
    OR (t.delivery_context @> '{"actor":true}') IS NOT TRUE
    OR t.execution_token IS DISTINCT FROM p_execution_token OR t.lease_expires_at IS NULL OR t.lease_expires_at<=now()
    OR t.user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
    OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
    OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
    OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
    OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
        WHERE m.user_id=t.user_id AND m.org_id=t.org_id AND m.status='active' AND o.status='active')) THEN
   RAISE EXCEPTION 'TASK_FOLLOWUP_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM public.conversation_turn_checkpoints WHERE task_id=t.id AND (state ? '_task_execution' OR state ? '_task_followup'))
    OR t.execution_mode IS DISTINCT FROM 'serial' OR NOT EXISTS(SELECT 1 FROM public.conversations WHERE id=t.conversation_id AND scope_type='user' AND user_id=t.user_id) THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT string_agg(part->>'text',E'\n' ORDER BY n) INTO text_value
 FROM public.messages m,jsonb_array_elements(m.content::jsonb) WITH ORDINALITY x(part,n)
 WHERE m.id=t.input_message_id AND m.role='user' AND m.conversation_id=t.conversation_id AND m.turn_id IS NOT DISTINCT FROM t.turn_id AND part->>'type'='text';
 text_value:=btrim(text_value);
 IF text_value IS NULL OR length(text_value) NOT BETWEEN 1 AND 20000 THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT logical.* INTO s FROM public.agent_task_states logical JOIN public.tasks owner ON owner.id=logical.owner_task_id
 JOIN public.messages m ON m.id=owner.assistant_message_id
 WHERE logical.actor_user_id=t.user_id AND logical.org_id IS NOT DISTINCT FROM t.org_id AND logical.conversation_id=t.conversation_id
   AND logical.owner_task_id<>t.id AND owner.status='completed' AND owner.queue_sequence<t.queue_sequence
   AND owner.type='chat' AND owner.execution_mode='serial' AND owner.user_id=t.user_id
   AND owner.org_id IS NOT DISTINCT FROM t.org_id AND owner.conversation_id=t.conversation_id
   AND owner.input_message_id<>t.input_message_id AND logical.state->>'image_binding_version'='3'
   AND logical.state#>>'{execution_identity,context_scope}'='user' AND logical.state#>>'{execution_identity,workspace_owner_id}'=t.user_id::text
   AND logical.state->'branch_id'='null'::jsonb AND logical.state->>'status' NOT IN ('cancelled','failed','paused')
   AND (logical.state->>'model_rounds_used')::int<(logical.state->>'model_round_limit')::int
   AND m.role='assistant' AND m.conversation_id=t.conversation_id AND m.status='completed' AND m.context_revision>0 AND m.context_revision<=t.base_context_revision
   AND NOT EXISTS(SELECT 1 FROM public.agent_task_events e WHERE e.root_task_id=logical.root_task_id AND e.status='queued')
 ORDER BY owner.queue_sequence DESC LIMIT 1;
 IF s.root_task_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id;
 IF cp->'_task_execution' IS DISTINCT FROM s.state THEN RAISE EXCEPTION 'TASK_FOLLOWUP_SOURCE_MISSING'; END IF;
 IF t.request_params ? '_selected_skill' AND (
     t.request_params#>>'{_selected_skill,skill_id}' IS DISTINCT FROM cp#>>'{skill_runtime,manual_skill_id}'
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cp#>'{skill_runtime,directory}') d
         WHERE d->>'skill_key'=t.request_params#>>'{_selected_skill,skill_id}' AND d->>'revision'=t.request_params#>>'{_selected_skill,revision}')) THEN
   RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','source',jsonb_build_object('state',s.state,'checkpoint',cp,
   'source_sha256',encode(sha256(convert_to(cp::text,'UTF8')),'hex'),'source_owner_task_id',s.owner_task_id,
   'message',text_value,'input_message_id',t.input_message_id));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_followup(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_followup(uuid,uuid) TO everydayai;
CREATE FUNCTION public.adopt_agent_task_followup(p_task_id uuid,p_execution_token uuid,p_root_task_id uuid,p_expected_revision bigint,
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
REVOKE ALL ON FUNCTION public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb) TO everydayai;
RESET ROLE;
