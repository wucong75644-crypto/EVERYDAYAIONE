-- Recent real requests remain visible even when no logical execution was created.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_request_context(p_task_id uuid,p_execution_token uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; rows jsonb; images jsonb; turn_numbers jsonb;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 IF SESSION_USER<>'everydayai' OR t.id IS NULL OR t.type IS DISTINCT FROM 'chat' OR t.status IS DISTINCT FROM 'running'
    OR (t.delivery_context @> '{"actor":true}') IS NOT TRUE OR t.execution_mode IS DISTINCT FROM 'serial'
    OR t.execution_token IS DISTINCT FROM p_execution_token OR t.lease_expires_at IS NULL OR t.lease_expires_at<=now()
    OR t.user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
    OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
    OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
    OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
    OR NOT EXISTS(SELECT 1 FROM public.conversations WHERE id=t.conversation_id AND scope_type='user' AND user_id=t.user_id)
    OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
         WHERE m.user_id=t.user_id AND m.org_id=t.org_id AND m.status='active' AND o.status='active')) THEN
   RAISE EXCEPTION 'REQUEST_CONTEXT_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 -- Number visible user messages, not assistant events, worker continuations or
 -- positions inside the limited recent-request window. The pending input is last.
 SELECT COALESCE(jsonb_object_agg(id::text,ordinal),'{}'::jsonb) INTO turn_numbers FROM (
   SELECT id,row_number() OVER (ORDER BY COALESCE(context_revision,t.base_context_revision+1),created_at,id) ordinal
   FROM public.messages WHERE conversation_id=t.conversation_id AND role='user'
     AND (id=t.input_message_id OR status='completed' AND context_revision>0 AND context_revision<=t.base_context_revision)
 ) numbered;
 SELECT COALESCE(jsonb_agg(entry ORDER BY seq DESC),'[]'::jsonb) INTO rows FROM (
   SELECT owner.queue_sequence seq, jsonb_build_object(
      'message_id',m.id,'task_id',owner.id,'root_task_id',COALESCE(owner.delivery_context->>'logical_task_id',owner.id::text),
      'context_revision',m.context_revision,'turn_number',turn_numbers->m.id::text,
      'source_sha256',encode(sha256(convert_to(m.content::jsonb::text,'UTF8')),'hex'),
      'text_excerpt',left((SELECT string_agg(p->>'text',E'\n' ORDER BY n)
         FROM jsonb_array_elements(m.content::jsonb) WITH ORDINALITY a(p,n) WHERE p->>'type'='text'),4000),
      'text_requires_read',length((SELECT string_agg(p->>'text',E'\n' ORDER BY n)
         FROM jsonb_array_elements(m.content::jsonb) WITH ORDINALITY a(p,n) WHERE p->>'type'='text'))>4000,
      'text_part_indices',(SELECT COALESCE(jsonb_agg(n-1),'[]'::jsonb)
         FROM jsonb_array_elements(m.content::jsonb) WITH ORDINALITY a(p,n) WHERE p->>'type'='text'),
      'images',(SELECT COALESCE(jsonb_agg(jsonb_build_object('name',p->>'name','reference',
         jsonb_build_object('message_id',m.id,'content_index',n-1),
         'client_reference',CASE WHEN p->>'source_message_id' IS NOT NULL THEN jsonb_build_object(
            'message_id',p->>'source_message_id','content_index',p->'source_content_index') END) ORDER BY n),'[]'::jsonb)
         FROM jsonb_array_elements(m.content::jsonb) WITH ORDINALITY a(p,n)
         WHERE p->>'type' IN ('image','image_url','input_image') OR p->>'type'='file' AND p->>'mime_type' LIKE 'image/%'),
      'intent',COALESCE(cp.state#>>'{_ecom_workflow,intent}',owner.request_params#>>'{_ecom_workflow,intent}'),
      'focus',cp.state#>>'{_request_resolution_v1,focus}',
      'selected_images',COALESCE(cp.state#>'{_ecom_workflow,request_context,materials}','[]'::jsonb),
      'resolution',cp.state->'_request_resolution_v1',
      'has_execution_state',cp.state ? '_task_execution',
      'task_status',owner.status) entry
   FROM public.tasks owner JOIN public.messages m ON m.id=owner.input_message_id
   LEFT JOIN public.conversation_turn_checkpoints cp ON cp.task_id=owner.id
   WHERE owner.conversation_id=t.conversation_id AND owner.user_id=t.user_id AND owner.org_id IS NOT DISTINCT FROM t.org_id
     AND owner.type='chat' AND owner.execution_mode='serial' AND owner.queue_sequence<=t.queue_sequence
     AND COALESCE(owner.delivery_context->>'input_kind','') NOT IN ('agent_task_continuation')
     AND m.role='user' AND m.conversation_id=t.conversation_id
     AND (m.id=t.input_message_id OR m.status='completed' AND m.context_revision>0 AND m.context_revision<=t.base_context_revision)
   ORDER BY owner.queue_sequence DESC LIMIT 12
 ) recent;
 SELECT COALESCE(jsonb_agg(entry ORDER BY rev DESC),'[]'::jsonb) INTO images FROM (
   SELECT m.context_revision rev, jsonb_build_object('source_message_id',m.id,'content_index',n-1,
     'source_sha256',encode(sha256(convert_to(m.content::jsonb::text,'UTF8')),'hex'),
     'name',p->>'name','task_id',p->>'task_id',
     'generated_for_message_id',parent.input_message_id,'generated_for_turn',turn_numbers->parent.input_message_id::text,
     'root_task_id',COALESCE(parent.delivery_context->>'logical_task_id',parent.id::text)) entry
   FROM public.messages m CROSS JOIN LATERAL jsonb_array_elements(m.content::jsonb) WITH ORDINALITY a(p,n)
   LEFT JOIN public.tasks native ON native.id::text=p->>'task_id' AND native.user_id=t.user_id
      AND native.org_id IS NOT DISTINCT FROM t.org_id AND native.conversation_id=t.conversation_id
   LEFT JOIN public.tasks parent ON parent.id::text=native.request_params#>>'{_media_request_v1,origin,parent_task_id}'
      AND parent.user_id=t.user_id AND parent.org_id IS NOT DISTINCT FROM t.org_id AND parent.conversation_id=t.conversation_id
   WHERE m.conversation_id=t.conversation_id AND m.role='assistant' AND m.status='completed'
      AND m.context_revision>0 AND m.context_revision<=t.base_context_revision
      AND p->>'type'='image'
   ORDER BY m.context_revision DESC,n LIMIT 16
 ) actual_images;
 RETURN jsonb_build_object('version',1,'catalog_version',2,'input_message_id',t.input_message_id,'context_revision',t.base_context_revision,
                           'requests',rows,'image_results',images);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_request_context(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_request_context(uuid,uuid) TO everydayai;

-- Historical group provenance comes from the saved artifact, not the latest
-- owner of its logical task. No content or authority is rewritten.
DO $migration$
DECLARE definition text := pg_get_functiondef('public.read_agent_content_followup(uuid,uuid,uuid)'::regprocedure);
 original text := '''root_task_id'',s.root_task_id,''artifact_id'',a->>''id'',';
BEGIN
 IF strpos(definition,original)=0 THEN RAISE EXCEPTION 'Unexpected content group definition'; END IF;
 definition:=replace(definition,original,'''root_task_id'',s.root_task_id,''creation_task_id'',a->>''source_task_id'',''artifact_id'',a->>''id'',');
 EXECUTE definition;
END $migration$;

-- Preserve this window's target, never the historical owner's target. Recheck
-- the selected root while holding the adoption function's conversation lock.
DO $migration$
DECLARE definition text := pg_get_functiondef('public.adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure);
 old_exclusions text := '''_skill_composition_expected_revision'',''_ecom_workflow''])';
 old_check text := 'IF s.root_task_id IS NULL OR s.root_task_id::text IS DISTINCT FROM source#>>''{state,root_task_id}''';
BEGIN
 IF strpos(definition,old_exclusions)=0 OR strpos(definition,old_check)=0 THEN
   RAISE EXCEPTION 'Unexpected followup adoption definition'; END IF;
 definition:=replace(definition,old_exclusions,'''_skill_composition_expected_revision'',''_ecom_workflow'',''_request_context_v1'',''_request_resolution_v1'',''_inline_media_action'',''_request_response_v1''])');
 definition:=replace(definition,old_check,$check$
IF fresh ? '_request_context_v1' AND (
    p_payload->'_request_context_v1' IS DISTINCT FROM fresh->'_request_context_v1'
    OR p_payload->'_request_resolution_v1' IS DISTINCT FROM fresh->'_request_resolution_v1'
    OR p_payload->'_inline_media_action' IS DISTINCT FROM fresh->'_inline_media_action'
    OR p_payload->'_request_response_v1' IS DISTINCT FROM fresh->'_request_response_v1'
    OR fresh#>>'{_request_resolution_v1,status}' IS DISTINCT FROM 'resolved'
    OR fresh#>>'{_request_resolution_v1,input_message_id}' IS DISTINCT FROM t.input_message_id::text
    OR fresh#>>'{_request_resolution_v1,target,kind}' IS DISTINCT FROM 'content'
    OR fresh#>>'{_request_resolution_v1,target,root_task_id}' IS DISTINCT FROM p_root_task_id::text
    OR jsonb_typeof(fresh#>'{_request_resolution_v1,target,item_indices}') IS DISTINCT FROM 'array'
    OR jsonb_array_length(fresh#>'{_request_resolution_v1,target,item_indices}') NOT BETWEEN 1 AND 100
    OR NOT EXISTS(SELECT FROM jsonb_array_elements(s.state->'artifacts') a
        WHERE a->>'id'=fresh#>>'{_request_resolution_v1,target,artifact_id}'
          AND a->'validations' @> '["content.object.v2"]'::jsonb
          AND (a->>'inline_text')::jsonb->'revision'=fresh#>'{_request_resolution_v1,target,revision}'
          AND EXISTS(SELECT FROM jsonb_array_elements(s.state->'results') r WHERE r->'artifact_ids' ? (a->>'id'))
          AND NOT EXISTS(SELECT FROM jsonb_array_elements(fresh#>'{_request_resolution_v1,target,item_indices}') i
              WHERE jsonb_typeof(i)<>'number' OR i::text !~ '^(100|[1-9][0-9]?)$'
                 OR i>to_jsonb(jsonb_array_length((a->>'inline_text')::jsonb#>'{document,items}')))
          AND NOT EXISTS(SELECT FROM jsonb_array_elements(s.state->'artifacts') newer
              WHERE newer->'validations' @> '["content.object.v2"]'::jsonb
                AND (newer->>'inline_text')::jsonb->>'object_key'=(a->>'inline_text')::jsonb->>'object_key'
                AND ((newer->>'inline_text')::jsonb->>'revision')::integer>((a->>'inline_text')::jsonb->>'revision')::integer))
 ) THEN RAISE EXCEPTION 'REQUEST_TARGET_ADOPTION_MISMATCH'; END IF;
 IF s.root_task_id IS NULL OR s.root_task_id::text IS DISTINCT FROM source#>>'{state,root_task_id}'$check$);
 EXECUTE definition;
END $migration$;
RESET ROLE;
