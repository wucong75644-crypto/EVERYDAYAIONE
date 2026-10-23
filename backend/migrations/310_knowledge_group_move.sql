-- Whole selection moves atomically; cut-paste uses the same identity-preserving command.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_apply_group_move(p_command uuid,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 cid uuid:=(p_payload->>'container_id')::uuid; target uuid:=(p_payload->>'target_id')::uuid;
 placement text:=p_payload->>'placement'; ids uuid[]; roots uuid[]; ordered uuid[]; siblings uuid[]; pid uuid;
 item record; node jsonb; oid uuid; rev bigint; ordinal bigint:=0; event_id uuid:=gen_random_uuid(); result jsonb;
BEGIN
 IF NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT revision INTO rev FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
 IF rev IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF placement NOT IN ('free','child','before','after','root') OR jsonb_array_length(p_payload->'objects') NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 SELECT array_agg(DISTINCT (v->>'object_id')::uuid) INTO roots FROM jsonb_array_elements(p_payload->'objects') v;
 IF cardinality(roots)<>jsonb_array_length(p_payload->'objects') OR EXISTS(
 SELECT 1 FROM jsonb_array_elements(p_payload->'objects') v LEFT JOIN public.kb_objects o ON o.id=(v->>'object_id')::uuid
 WHERE o.id IS NULL OR o.container_id<>cid OR o.deleted_at IS NOT NULL OR o.undo_hidden OR o.current_revision<>(v->>'expected_revision')::bigint)
 THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 WITH RECURSIVE tree AS (SELECT id FROM public.kb_objects WHERE id=ANY(roots)
 UNION SELECT o.id FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id WHERE o.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL)
 SELECT array_agg(id) INTO ids FROM tree;
 SELECT array_agg(o.id ORDER BY o.sort_order,o.id) INTO roots FROM public.kb_objects o WHERE o.id=ANY(ids) AND (o.parent_id IS NULL OR NOT o.parent_id=ANY(ids));
 IF cardinality(ids)>10000 OR jsonb_array_length(p_payload->'nodes') NOT BETWEEN 1 AND 10000 OR
 (SELECT count(DISTINCT v->>'node_id') FROM jsonb_array_elements(p_payload->'nodes') v)<>jsonb_array_length(p_payload->'nodes') OR EXISTS(
 SELECT 1 FROM public.kb_canvas_nodes n WHERE n.object_id=ANY(ids) AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'nodes') v WHERE (v->>'node_id')::uuid=n.id))
 THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF placement IN ('child','before','after') THEN
  SELECT CASE WHEN placement='child' THEN id ELSE parent_id END INTO pid FROM public.kb_objects
   WHERE id=target AND container_id=cid AND NOT undo_hidden AND deleted_at IS NULL AND NOT id=ANY(ids);
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 ELSIF target IS NOT NULL THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 FOR node IN SELECT * FROM jsonb_array_elements(p_payload->'nodes') LOOP
  UPDATE public.kb_canvas_nodes n SET x=(node->'position'->>'x')::float8,y=(node->'position'->>'y')::float8,revision=revision+1
   WHERE n.id=(node->>'node_id')::uuid AND n.container_id=cid AND n.revision=(node->>'expected_revision')::bigint
   AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=n.object_id AND NOT o.undo_hidden AND o.deleted_at IS NULL
    AND (placement<>'free' OR o.id=ANY(ids)));
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 END LOOP;
 IF placement<>'free' THEN
  SELECT array_agg(id ORDER BY sort_order,id) INTO siblings FROM public.kb_objects
   WHERE container_id=cid AND parent_id IS NOT DISTINCT FROM pid AND NOT id=ANY(roots) AND NOT undo_hidden AND deleted_at IS NULL;
  ordered:='{}'::uuid[];
  FOREACH oid IN ARRAY COALESCE(siblings,'{}'::uuid[]) LOOP
   IF oid=target AND placement='before' THEN ordered:=ordered||roots; END IF;
   ordered:=array_append(ordered,oid);
   IF oid=target AND placement='after' THEN ordered:=ordered||roots; END IF;
  END LOOP;
  IF placement IN ('child','root') THEN ordered:=ordered||roots; END IF;
  FOR item IN SELECT o.*,v.body FROM public.kb_objects o JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=ANY(roots) LOOP
   IF item.parent_id IS DISTINCT FROM pid THEN
    UPDATE public.kb_objects SET parent_id=pid,current_revision=current_revision+1 WHERE id=item.id;
    INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
     VALUES(item.id,cid,org,item.current_revision+1,item.title,item.body,pid,actor);
   END IF;
  END LOOP;
  FOREACH oid IN ARRAY ordered LOOP ordinal:=ordinal+1024; UPDATE public.kb_objects SET sort_order=ordinal WHERE id=oid; END LOOP;
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command,'move_branches',rev);
 result:=jsonb_build_object('command_id',p_command,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
  VALUES(actor,p_command,org,cid,jsonb_build_object('operation','move_branches','payload',p_payload),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_apply_group_move(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
RESET ROLE;
