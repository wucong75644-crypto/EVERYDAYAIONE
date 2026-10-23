-- Atomic whole-branch duplicate / recoverable remove. Never delete immutable sources.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_apply_batch_command(p_command uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 cid uuid:=(p_payload->>'container_id')::uuid; parent uuid:=(p_payload->>'parent_id')::uuid;
 ids uuid[]; roots uuid[]; mapping jsonb:='{}'; item record; old_id uuid; new_id uuid; new_parent uuid; bid uuid;
 event_id uuid:=gen_random_uuid(); rev bigint; result jsonb;
BEGIN
 IF NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT revision INTO rev FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
 IF rev IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF p_operation NOT IN ('copy_branches','delete_branches') OR jsonb_array_length(p_payload->'objects') NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 SELECT array_agg(DISTINCT (v->>'object_id')::uuid) INTO roots FROM jsonb_array_elements(p_payload->'objects') v;
 IF cardinality(roots)<>jsonb_array_length(p_payload->'objects') OR EXISTS(
 SELECT 1 FROM jsonb_array_elements(p_payload->'objects') v LEFT JOIN public.kb_objects o ON o.id=(v->>'object_id')::uuid
 WHERE o.id IS NULL OR o.container_id<>cid OR o.deleted_at IS NOT NULL OR o.undo_hidden OR o.current_revision<>(v->>'expected_revision')::bigint)
 THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 WITH RECURSIVE tree AS (SELECT id FROM public.kb_objects WHERE id=ANY(roots)
 UNION SELECT o.id FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id WHERE o.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL)
 SELECT array_agg(id) INTO ids FROM tree;
 IF p_operation='copy_branches' AND cardinality(ids)<>cardinality((SELECT array_agg((v->>'object_id')::uuid) FROM jsonb_array_elements(p_payload->'objects') v)) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF cardinality(ids)>10000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 -- Eliminate selected descendants: every source is copied exactly once.
 SELECT array_agg(o.id) INTO roots FROM public.kb_objects o WHERE o.id=ANY(ids) AND (o.parent_id IS NULL OR NOT o.parent_id=ANY(ids));
 IF p_operation='delete_branches' THEN
  UPDATE public.kb_objects SET undo_hidden=true WHERE id=ANY(ids);
 ELSE
  IF parent IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=parent AND container_id=cid AND NOT undo_hidden AND deleted_at IS NULL) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  FOREACH old_id IN ARRAY ids LOOP mapping:=mapping||jsonb_build_object(old_id::text,gen_random_uuid()); END LOOP;
  -- Parents must exist before children, including arbitrary selected source order.
  FOR item IN WITH RECURSIVE tree AS (
   SELECT o.*,0 depth FROM public.kb_objects o WHERE o.id=ANY(roots)
   UNION ALL SELECT o.*,t.depth+1 FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id WHERE o.id=ANY(ids))
   SELECT t.*,v.body,n.x,n.y FROM tree t JOIN public.kb_object_versions v ON v.object_id=t.id AND v.revision=t.current_revision
    JOIN public.kb_canvas_nodes n ON n.object_id=t.id ORDER BY depth,sort_order,id LOOP
   new_id:=(mapping->>item.id::text)::uuid;
   new_parent:=CASE WHEN item.id=ANY(roots) THEN parent ELSE (mapping->>item.parent_id::text)::uuid END;
   INSERT INTO public.kb_objects(id,org_id,container_id,parent_id,kind,title,created_by)
    VALUES(new_id,org,cid,new_parent,item.kind,item.title,actor);
   INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
    VALUES(new_id,cid,org,1,item.title,item.body,new_parent,actor);
   INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y)
    VALUES(org,cid,new_id,item.x+(p_payload->'offset'->>'x')::float8,item.y+(p_payload->'offset'->>'y')::float8);
   FOR old_id IN SELECT id FROM public.kb_file_bindings WHERE object_id=item.id AND removed_at IS NULL LOOP
    bid:=gen_random_uuid();
    INSERT INTO public.kb_file_bindings(id,object_id,container_id,org_id,title,current_revision,created_by)
     SELECT bid,new_id,cid,org,title,1,actor FROM public.kb_file_bindings WHERE id=old_id;
    INSERT INTO public.kb_file_binding_versions(binding_id,revision,version_id,container_id,org_id)
     SELECT bid,1,v.version_id,cid,org FROM public.kb_file_binding_versions v JOIN public.kb_file_bindings b ON b.id=v.binding_id
      WHERE b.id=old_id AND v.revision=b.current_revision;
   END LOOP;
  END LOOP;
  INSERT INTO public.kb_relations(org_id,container_id,source_object_id,target_object_id,relation_type)
   SELECT org,cid,(mapping->>source_object_id::text)::uuid,(mapping->>target_object_id::text)::uuid,relation_type
    FROM public.kb_relations WHERE source_object_id=ANY(ids) AND target_object_id=ANY(ids);
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command,p_operation,rev);
 result:=jsonb_build_object('command_id',p_command,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
  VALUES(actor,p_command,org,cid,jsonb_build_object('operation',p_operation,'payload',p_payload),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_apply_batch_command(uuid,text,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
RESET ROLE;
