SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_content_followup(p_task_id uuid,p_execution_token uuid,p_root_task_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; cp jsonb; text_value text; chosen jsonb; groups jsonb := '[]'::jsonb; extra jsonb;
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
 FOR s IN SELECT logical.* FROM public.agent_task_states logical JOIN public.tasks owner ON owner.id=logical.owner_task_id
 JOIN public.messages m ON m.id=owner.assistant_message_id
 WHERE (p_root_task_id IS NULL OR logical.root_task_id=p_root_task_id) AND logical.actor_user_id=t.user_id AND logical.org_id IS NOT DISTINCT FROM t.org_id AND logical.conversation_id=t.conversation_id
   AND logical.owner_task_id<>t.id AND owner.status='completed' AND owner.queue_sequence<t.queue_sequence
   AND owner.type='chat' AND owner.execution_mode='serial' AND owner.user_id=t.user_id
   AND owner.org_id IS NOT DISTINCT FROM t.org_id AND owner.conversation_id=t.conversation_id
   AND owner.input_message_id<>t.input_message_id AND logical.state->>'image_binding_version'='3'
   AND logical.state#>>'{execution_identity,context_scope}'='user' AND logical.state#>>'{execution_identity,workspace_owner_id}'=t.user_id::text
   AND logical.state->'branch_id'='null'::jsonb AND logical.state->>'status' NOT IN ('cancelled','failed','paused')
   AND (logical.state->>'model_rounds_used')::int<(logical.state->>'model_round_limit')::int
   AND m.role='assistant' AND m.conversation_id=t.conversation_id AND m.status='completed' AND m.context_revision>0 AND m.context_revision<=t.base_context_revision
   AND NOT EXISTS(SELECT 1 FROM public.agent_task_events e WHERE e.root_task_id=logical.root_task_id AND e.status='queued')
 ORDER BY owner.queue_sequence DESC LIMIT 32 LOOP
 SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=s.owner_task_id;
 IF cp->'_task_execution' IS DISTINCT FROM s.state THEN
   IF chosen IS NULL THEN RAISE EXCEPTION 'TASK_FOLLOWUP_SOURCE_MISSING'; END IF;
   CONTINUE;
 END IF;
 IF t.request_params ? '_selected_skill' AND (
     t.request_params#>>'{_selected_skill,skill_id}' IS DISTINCT FROM cp#>>'{skill_runtime,manual_skill_id}'
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cp#>'{skill_runtime,directory}') d
         WHERE d->>'skill_key'=t.request_params#>>'{_selected_skill,skill_id}' AND d->>'revision'=t.request_params#>>'{_selected_skill,revision}')) THEN
   CONTINUE; END IF;
 IF chosen IS NULL THEN
 chosen:=jsonb_build_object('state',s.state,'checkpoint',cp,
   'source_sha256',encode(sha256(convert_to(cp::text,'UTF8')),'hex'),'source_owner_task_id',s.owner_task_id,
   'message',text_value,'input_message_id',t.input_message_id);
 ELSIF s.state->>'model_directed'='true' THEN
   SELECT jsonb_agg(jsonb_build_object('root_task_id',s.root_task_id,'artifact_id',a->>'id',
           'revision',rec->'revision','title',rec#>>'{document,title}',
           'uploads',(SELECT COALESCE(jsonb_agg(jsonb_build_object('name',img->'name','reference',
               jsonb_build_object('message_id',img#>'{reference,message_id}','content_index',img#>'{reference,content_index}'))
               ORDER BY n),'[]'::jsonb) FROM jsonb_array_elements(rec->'source_images') WITH ORDINALITY v(img,n)
               WHERE img#>>'{reference,message_id}' IS NOT NULL),'items',
           (SELECT jsonb_agg(jsonb_build_object('item_index',idx,'title',item->>'title') ORDER BY idx)
            FROM jsonb_array_elements(rec#>'{document,items}') WITH ORDINALITY v(item,idx))))
     INTO extra FROM (
       SELECT DISTINCT ON (r->>'object_key') artifact a,r rec FROM jsonb_array_elements(s.state->'artifacts') artifact
       CROSS JOIN LATERAL (SELECT (artifact->>'inline_text')::jsonb) x(r)
       WHERE artifact->'validations' @> '["content.object.v2"]'::jsonb
         AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'results') result
                    WHERE result->'artifact_ids' ? (artifact->>'id'))
       ORDER BY r->>'object_key',(r->>'revision')::integer DESC
     ) latest;
   groups:=groups||COALESCE(extra,'[]'::jsonb);
 END IF;
 END LOOP;
 IF chosen IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','source',chosen,'other_groups',groups);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_content_followup(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_content_followup(uuid,uuid,uuid) TO everydayai;
ALTER FUNCTION public.read_agent_task_followup(uuid,uuid) RENAME TO read_agent_task_followup_before378;
CREATE FUNCTION public.read_agent_task_followup(p_task_id uuid,p_execution_token uuid) RETURNS jsonb
LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT public.read_agent_content_followup(p_task_id,p_execution_token,NULL)
$$;
REVOKE ALL ON FUNCTION public.read_agent_task_followup(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_followup(uuid,uuid) TO everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
BEGIN
 IF strpos(definition,'checked:=public.read_agent_task_followup(p_task_id,p_execution_token);')=0 THEN RAISE EXCEPTION 'Unexpected followup adoption definition'; END IF;
 EXECUTE replace(definition,'checked:=public.read_agent_task_followup(p_task_id,p_execution_token);','checked:=public.read_agent_content_followup(p_task_id,p_execution_token,p_root_task_id);');
END $migration$;
RESET ROLE;
