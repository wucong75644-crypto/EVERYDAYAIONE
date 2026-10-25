-- Paste positions and target-tree reflow share one history transaction.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.kb_paste_snapshot(p_command uuid,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid:=(p_payload->>'container_id')::uuid;
 parent uuid:=(p_payload->>'parent_id')::uuid; snap public.kb_branch_snapshots%ROWTYPE; mapping jsonb:='{}'; item record; file jsonb;
 old_id uuid; new_id uuid; new_parent uuid; bid uuid; event_id uuid:=gen_random_uuid(); rev bigint; result jsonb;
BEGIN
 PERFORM public.kb_branch_snapshot((p_payload->>'snapshot_id')::uuid,NULL);
 SELECT * INTO snap FROM public.kb_branch_snapshots WHERE id=(p_payload->>'snapshot_id')::uuid;
 IF jsonb_array_length(p_payload->'objects')<>jsonb_array_length(snap.nodes) OR EXISTS(SELECT 1 FROM jsonb_array_elements(snap.nodes) n WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'objects') o WHERE o->>'object_id'=n->>'id' AND o->>'expected_revision'=n->>'revision')) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF snap.container_id<>cid OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT revision INTO rev FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
 IF rev IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF parent IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=parent AND container_id=cid AND NOT undo_hidden AND deleted_at IS NULL) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF p_payload ? 'snapshot_positions' AND (jsonb_array_length(p_payload->'snapshot_positions')<>jsonb_array_length(snap.nodes) OR (SELECT count(DISTINCT v->>'source_object_id') FROM jsonb_array_elements(p_payload->'snapshot_positions') v)<>jsonb_array_length(snap.nodes) OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'snapshot_positions') v WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(snap.nodes) n WHERE n->>'id'=v->>'source_object_id'))) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF p_payload ? 'snapshot_positions' AND EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'snapshot_positions') v WHERE v#>>'{position,x}' IS NULL OR v#>>'{position,y}' IS NULL OR (v#>>'{position,x}')::float8 NOT BETWEEN -1000000 AND 1000000 OR (v#>>'{position,y}')::float8 NOT BETWEEN -1000000 AND 1000000) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF p_payload ? 'nodes' THEN PERFORM public.kb_title_layout(cid,p_payload); END IF;
 FOR old_id IN SELECT (v->>'id')::uuid FROM jsonb_array_elements(snap.nodes) v LOOP mapping:=mapping||jsonb_build_object(old_id::text,gen_random_uuid()); END LOOP;
 FOR item IN WITH RECURSIVE objects AS (
  SELECT * FROM jsonb_to_recordset(snap.nodes) AS x(id uuid,parent_id uuid,kind text,title text,body text,sort_order bigint,x float8,y float8,files jsonb,canvas_data jsonb)
 ), tree AS (
  SELECT o.*,0 depth FROM objects o WHERE parent_id IS NULL OR NOT mapping ? parent_id::text
  UNION ALL SELECT o.*,t.depth+1 FROM objects o JOIN tree t ON o.parent_id=t.id
 ) SELECT * FROM tree ORDER BY depth,sort_order,id LOOP
  new_id:=(mapping->>item.id::text)::uuid;
  new_parent:=CASE WHEN item.parent_id IS NOT NULL AND mapping ? item.parent_id::text THEN (mapping->>item.parent_id::text)::uuid ELSE parent END;
  INSERT INTO public.kb_objects(id,org_id,container_id,parent_id,kind,title,created_by) VALUES(new_id,org,cid,new_parent,item.kind,item.title,actor);
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by) VALUES(new_id,cid,org,1,item.title,item.body,new_parent,actor);
  IF item.parent_id IS NOT NULL AND mapping ? item.parent_id::text THEN UPDATE public.kb_objects SET sort_order=item.sort_order WHERE id=new_id; END IF;
  INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y) VALUES(org,cid,new_id,COALESCE((SELECT (v->'position'->>'x')::float8 FROM jsonb_array_elements(p_payload->'snapshot_positions') v WHERE v->>'source_object_id'=item.id::text),item.x+(p_payload->'offset'->>'x')::float8),COALESCE((SELECT (v->'position'->>'y')::float8 FROM jsonb_array_elements(p_payload->'snapshot_positions') v WHERE v->>'source_object_id'=item.id::text),item.y+(p_payload->'offset'->>'y')::float8));
  UPDATE public.kb_canvas_nodes SET canvas_data=COALESCE(item.canvas_data,'{}'::jsonb)-'summary'-'boundary' WHERE object_id=new_id;
  FOR file IN SELECT * FROM jsonb_array_elements(item.files) LOOP
   bid:=gen_random_uuid();
   INSERT INTO public.kb_file_bindings(id,object_id,container_id,org_id,title,current_revision,created_by) VALUES(bid,new_id,cid,org,file->>'title',1,actor);
   INSERT INTO public.kb_file_binding_versions(binding_id,revision,version_id,container_id,org_id) VALUES(bid,1,(file->>'version_id')::uuid,cid,org);
   IF item.canvas_data->'image'->>'binding_id'=file->>'binding_id' THEN UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{image,binding_id}',to_jsonb(bid::text)) WHERE object_id=new_id; UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{image,revision}','1'::jsonb) WHERE object_id=new_id; END IF;
   IF item.canvas_data->'video'->>'binding_id'=file->>'binding_id' THEN UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{video,binding_id}',to_jsonb(bid::text)) WHERE object_id=new_id; UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{video,revision}','1'::jsonb) WHERE object_id=new_id; END IF;
  END LOOP;
 END LOOP;
 FOR item IN SELECT * FROM jsonb_to_recordset(snap.nodes) AS x(id uuid,canvas_data jsonb) LOOP
  FOR file IN SELECT jsonb_build_object('key',k,'members',v->'members','title',v->'title') FROM jsonb_each(COALESCE(item.canvas_data,'{}'::jsonb)) e(k,v) WHERE k IN ('summary','boundary') LOOP
   IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(file->'members') AS members(member_id) WHERE NOT mapping ? member_id) THEN
    UPDATE public.kb_canvas_nodes SET canvas_data=canvas_data||jsonb_build_object(file->>'key',jsonb_build_object('members',(SELECT jsonb_agg(mapping->>member_id) FROM jsonb_array_elements_text(file->'members') AS members(member_id)),'title',file->'title')) WHERE object_id=(mapping->>item.id::text)::uuid;
   END IF;
  END LOOP;
 END LOOP;
 INSERT INTO public.kb_relations(org_id,container_id,source_object_id,target_object_id,relation_type,appearance)
  SELECT org,cid,(mapping->>(v->>'source_object_id'))::uuid,(mapping->>(v->>'target_object_id'))::uuid,v->>'relation_type',COALESCE(v->'appearance','{}'::jsonb) FROM jsonb_array_elements(snap.relations) v;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command,'copy_branches',rev);
 result:=jsonb_build_object('command_id',p_command,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,p_command,org,cid,jsonb_build_object('operation','copy_branches','payload',p_payload),result);
 RETURN result;
END $$;
RESET ROLE;
