-- Copy-time immutable branch snapshots, private to the actor and organization.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.kb_branch_snapshots (
 id uuid PRIMARY KEY, actor_id uuid NOT NULL REFERENCES public.users(id), org_id uuid REFERENCES public.organizations(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), request_payload jsonb NOT NULL,
 nodes jsonb NOT NULL, relations jsonb NOT NULL, expires_at timestamptz NOT NULL DEFAULT now()+interval '7 days'
);
ALTER TABLE public.kb_branch_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_branch_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY kb_internal ON public.kb_branch_snapshots TO everydayai_owner USING(true) WITH CHECK(true);
REVOKE ALL ON public.kb_branch_snapshots FROM PUBLIC,everydayai,everydayai_runtime;
CREATE FUNCTION public.kb_branch_snapshot(p_id uuid,p_payload jsonb DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 snap public.kb_branch_snapshots%ROWTYPE; cid uuid; rev bigint; ids uuid[]; expanded uuid[]; contents jsonb; edges jsonb;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(org::text,''),0));
 SELECT * INTO snap FROM public.kb_branch_snapshots WHERE id=p_id;
 IF FOUND THEN
  IF snap.actor_id<>actor OR snap.org_id IS DISTINCT FROM org OR NOT public.kb_can(snap.container_id,'write') OR EXISTS(SELECT 1 FROM public.kb_containers WHERE id=snap.container_id AND undo_hidden) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  IF snap.expires_at<=now() THEN RAISE EXCEPTION 'KB_SNAPSHOT_EXPIRED' USING ERRCODE='P0001'; END IF;
  IF p_payload IS NOT NULL AND snap.request_payload<>p_payload THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
 ELSE
  IF p_payload IS NULL THEN RAISE EXCEPTION 'KB_SNAPSHOT_EXPIRED' USING ERRCODE='P0001'; END IF;
  cid:=(p_payload->>'container_id')::uuid;
  IF NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  SELECT revision INTO rev FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
  IF rev IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF jsonb_array_length(p_payload->'objects') NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  SELECT array_agg(DISTINCT (v->>'object_id')::uuid) INTO ids FROM jsonb_array_elements(p_payload->'objects') v;
  IF cardinality(ids)<>jsonb_array_length(p_payload->'objects') OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'objects') v LEFT JOIN public.kb_objects o ON o.id=(v->>'object_id')::uuid
   WHERE o.id IS NULL OR o.container_id<>cid OR o.deleted_at IS NOT NULL OR o.undo_hidden OR o.current_revision<>(v->>'expected_revision')::bigint) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  WITH RECURSIVE tree AS (SELECT id FROM public.kb_objects WHERE id=ANY(ids) UNION SELECT o.id FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id WHERE o.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL)
   SELECT array_agg(id) INTO expanded FROM tree;
  IF cardinality(expanded)<>cardinality(ids) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  SELECT jsonb_agg(jsonb_build_object('id',o.id,'container_id',cid,'parent_id',o.parent_id,'kind',o.kind,'title',o.title,'revision',o.current_revision,'sort_order',o.sort_order,
   'node_id',n.id,'layout_revision',n.revision,'x',n.x,'y',n.y,'body',v.body,'files',COALESCE((SELECT jsonb_agg(jsonb_build_object('title',b.title,'version_id',bv.version_id) ORDER BY b.id)
    FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions bv ON bv.binding_id=b.id AND bv.revision=b.current_revision WHERE b.object_id=o.id AND b.removed_at IS NULL),'[]'::jsonb)) ORDER BY o.id)
   INTO contents FROM public.kb_objects o JOIN public.kb_canvas_nodes n ON n.object_id=o.id JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=ANY(ids);
  IF pg_column_size(contents)>16777216 THEN RAISE EXCEPTION 'KB_SNAPSHOT_CAPACITY' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.id),'[]'::jsonb) INTO edges FROM public.kb_relations r WHERE r.source_object_id=ANY(ids) AND r.target_object_id=ANY(ids);
  DELETE FROM public.kb_branch_snapshots WHERE actor_id=actor AND expires_at<=now();
  INSERT INTO public.kb_branch_snapshots(id,actor_id,org_id,container_id,request_payload,nodes,relations) VALUES(p_id,actor,org,cid,p_payload,contents,edges) RETURNING * INTO snap;
 END IF;
 RETURN jsonb_build_object('snapshot_id',snap.id,'nodes',(SELECT jsonb_agg(v-'body'-'files') FROM jsonb_array_elements(snap.nodes) v));
END $$;
REVOKE ALL ON FUNCTION public.kb_branch_snapshot(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_branch_snapshot(uuid,jsonb) TO everydayai,everydayai_runtime;
CREATE FUNCTION public.kb_paste_snapshot(p_command uuid,p_payload jsonb) RETURNS jsonb
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
 FOR old_id IN SELECT (v->>'id')::uuid FROM jsonb_array_elements(snap.nodes) v LOOP mapping:=mapping||jsonb_build_object(old_id::text,gen_random_uuid()); END LOOP;
 FOR item IN WITH RECURSIVE objects AS (
  SELECT * FROM jsonb_to_recordset(snap.nodes) AS x(id uuid,parent_id uuid,kind text,title text,body text,sort_order bigint,x float8,y float8,files jsonb)
 ), tree AS (
  SELECT o.*,0 depth FROM objects o WHERE parent_id IS NULL OR NOT mapping ? parent_id::text
  UNION ALL SELECT o.*,t.depth+1 FROM objects o JOIN tree t ON o.parent_id=t.id
 ) SELECT * FROM tree ORDER BY depth,sort_order,id LOOP
  new_id:=(mapping->>item.id::text)::uuid;
  new_parent:=CASE WHEN item.parent_id IS NOT NULL AND mapping ? item.parent_id::text THEN (mapping->>item.parent_id::text)::uuid ELSE parent END;
  INSERT INTO public.kb_objects(id,org_id,container_id,parent_id,kind,title,created_by) VALUES(new_id,org,cid,new_parent,item.kind,item.title,actor);
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by) VALUES(new_id,cid,org,1,item.title,item.body,new_parent,actor);
  IF item.parent_id IS NOT NULL AND mapping ? item.parent_id::text THEN UPDATE public.kb_objects SET sort_order=item.sort_order WHERE id=new_id; END IF;
  INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y) VALUES(org,cid,new_id,item.x+(p_payload->'offset'->>'x')::float8,item.y+(p_payload->'offset'->>'y')::float8);
  FOR file IN SELECT * FROM jsonb_array_elements(item.files) LOOP
   bid:=gen_random_uuid();
   INSERT INTO public.kb_file_bindings(id,object_id,container_id,org_id,title,current_revision,created_by) VALUES(bid,new_id,cid,org,file->>'title',1,actor);
   INSERT INTO public.kb_file_binding_versions(binding_id,revision,version_id,container_id,org_id) VALUES(bid,1,(file->>'version_id')::uuid,cid,org);
  END LOOP;
 END LOOP;
 INSERT INTO public.kb_relations(org_id,container_id,source_object_id,target_object_id,relation_type)
  SELECT org,cid,(mapping->>(v->>'source_object_id'))::uuid,(mapping->>(v->>'target_object_id'))::uuid,v->>'relation_type' FROM jsonb_array_elements(snap.relations) v;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command,'copy_branches',rev);
 result:=jsonb_build_object('command_id',p_command,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,p_command,org,cid,jsonb_build_object('operation','copy_branches','payload',p_payload),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_paste_snapshot(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
RESET ROLE;
