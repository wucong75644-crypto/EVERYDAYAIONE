-- Unify redisplay provenance without changing image bytes or existing receipts.
SET LOCAL ROLE everydayai;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.read_agent_request_context(uuid,uuid)'::regprocedure);
 old_fragment text := $old$ -- Resolve display occurrences to their verified original image in this frozen scope.
 WITH RECURSIVE recent AS (
   SELECT m.id display_mid,(n-1)::int display_idx,m.context_revision display_rev,
          m.id mid,(n-1)::int idx,p,m.content::jsonb body,m.role,m.context_revision rev,
          0 depth,ARRAY[m.id::text||':'||(n-1)::text] visited
   FROM public.messages m CROSS JOIN LATERAL jsonb_array_elements(m.content::jsonb) WITH ORDINALITY a(p,n)
   WHERE m.conversation_id=t.conversation_id AND m.org_id IS NOT DISTINCT FROM t.org_id
     AND m.role='assistant' AND m.status='completed' AND m.context_revision>0
     AND m.context_revision<=t.base_context_revision AND p->>'type'='image'
   ORDER BY m.context_revision DESC,n LIMIT 16
 ), origins AS (
   SELECT * FROM recent
   UNION ALL
   SELECT o.display_mid,o.display_idx,o.display_rev,m.id,i.idx,m.content::jsonb->i.idx,
          m.content::jsonb,m.role,m.context_revision,o.depth+1,o.visited||(m.id::text||':'||i.idx::text)
   FROM origins o
   JOIN public.messages m ON m.id::text=o.p->>'source_message_id'
   CROSS JOIN LATERAL (SELECT CASE WHEN o.p->>'source_content_index' ~ '^[0-9]{1,6}$'
                                  THEN (o.p->>'source_content_index')::int END idx) i
   WHERE m.conversation_id=t.conversation_id AND m.org_id IS NOT DISTINCT FROM t.org_id
     AND m.status='completed' AND m.role IN ('user','assistant') AND m.context_revision>0
     AND m.context_revision<=t.base_context_revision AND o.depth<16
     AND m.content::jsonb->i.idx->>'type'='image'
     AND NOT (m.id::text||':'||i.idx::text)=ANY(o.visited)
 ), actual AS (
   SELECT * FROM origins WHERE p->>'source_message_id' IS NULL
 ), distinct_images AS (
   SELECT DISTINCT ON(mid,idx) * FROM actual ORDER BY mid,idx,display_rev DESC
 )
 SELECT COALESCE(jsonb_agg(entry ORDER BY display_rev DESC),'[]'::jsonb) INTO images FROM (
   SELECT o.display_rev,jsonb_build_object('source_message_id',o.mid,'content_index',o.idx,
     'source_sha256',encode(sha256(convert_to(o.body::text,'UTF8')),'hex'),
     'name',o.p->>'name','task_id',native.id,
     'origin_kind',CASE WHEN o.role='user' THEN 'user_upload'
                        WHEN native.id IS NOT NULL THEN 'generated_result' ELSE 'assistant_image' END,
     'original_turn',CASE WHEN o.role='user' THEN turn_numbers->o.mid::text ELSE turn_numbers->parent.input_message_id::text END,
     'generated_for_message_id',parent.input_message_id,'generated_for_turn',turn_numbers->parent.input_message_id::text,
     'root_task_id',COALESCE(parent.delivery_context->>'logical_task_id',parent.id::text),
     'displayed_in',(SELECT COALESCE(jsonb_agg(jsonb_build_object('message_id',a.display_mid,'content_index',a.display_idx,
                        'display_message_id',a.display_mid,
                        'turn_number',(SELECT turn_numbers->u.id::text FROM public.messages u
                          WHERE u.conversation_id=t.conversation_id AND u.org_id IS NOT DISTINCT FROM t.org_id
                            AND u.role='user' AND u.status='completed' AND u.context_revision>0
                            AND u.context_revision<=a.display_rev ORDER BY u.context_revision DESC LIMIT 1))
                        ORDER BY a.display_rev DESC),'[]'::jsonb)
                     FROM actual a WHERE a.mid=o.mid AND a.idx=o.idx AND (a.display_mid<>o.mid OR a.display_idx<>o.idx))) entry
   FROM distinct_images o
   LEFT JOIN public.tasks native ON native.id::text=o.p->>'task_id' AND native.user_id=t.user_id
     AND native.org_id IS NOT DISTINCT FROM t.org_id AND native.conversation_id=t.conversation_id
   LEFT JOIN public.tasks parent ON parent.id::text=native.request_params#>>'{_media_request_v1,origin,parent_task_id}'
     AND parent.user_id=t.user_id AND parent.org_id IS NOT DISTINCT FROM t.org_id AND parent.conversation_id=t.conversation_id
 ) actual_images;$old$;
 new_fragment text := $new$ SELECT COALESCE(jsonb_agg(entry ORDER BY rev DESC),'[]'::jsonb) INTO images FROM (
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
 ) actual_images;$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected image provenance definition'; END IF;
 definition := replace(definition,old_fragment,new_fragment);
 definition := replace(definition,$field$'requests',rows,'image_results',images,'image_provenance_version',1$field$,$field$'requests',rows,'image_results',images$field$);
 EXECUTE definition;
END $migration$;
