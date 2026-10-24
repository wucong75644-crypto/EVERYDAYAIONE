-- Scope-based summaries follow covered branches without rewriting original topic parents.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_summary_scope(cid uuid,roots uuid[]) RETURNS uuid[] LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ids uuid[]:=COALESCE(roots,'{}');next_ids uuid[];
BEGIN
 LOOP
  WITH RECURSIVE tree AS (SELECT id FROM public.kb_objects WHERE id=ANY(ids) AND container_id=cid AND NOT undo_hidden AND deleted_at IS NULL
   UNION SELECT o.id FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id WHERE o.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL)
  SELECT COALESCE(array_agg(DISTINCT id),'{}') INTO next_ids FROM tree;
  SELECT COALESCE(array_agg(DISTINCT id),'{}') INTO next_ids FROM (SELECT unnest(next_ids) id UNION SELECT n.object_id FROM public.kb_canvas_nodes n JOIN public.kb_objects o ON o.id=n.object_id WHERE n.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL AND n.canvas_data ? 'summary' AND jsonb_array_length(n.canvas_data->'summary'->'members')>0 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(n.canvas_data->'summary'->'members') m WHERE m::uuid=n.object_id OR NOT m::uuid=ANY(next_ids))) all_ids;
  IF next_ids<@ids AND ids<@next_ids THEN RETURN next_ids; END IF;
  ids:=next_ids;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.kb_summary_scope(uuid,uuid[]) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_apply_branch_command(p_command_id uuid,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 oid uuid:=(p_payload->>'object_id')::uuid; target uuid:=(p_payload->>'target_id')::uuid;
 placement text:=p_payload->>'placement'; cid uuid; pid uuid; old public.kb_objects%ROWTYPE;
 prior public.kb_mutation_receipts%ROWTYPE; full_request jsonb:=jsonb_build_object('operation','move_branch','payload',p_payload);
 node jsonb; subtree uuid[]; siblings uuid[]; final_order uuid[]:='{}'; sibling uuid; inserted boolean:=false;
 rev bigint; response jsonb; event_id uuid:=gen_random_uuid();
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(actor::text||':'||p_command_id::text,0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command_id;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>full_request THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 SELECT container_id INTO cid FROM public.kb_objects WHERE id=oid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
 IF cid IS NULL OR NOT public.kb_can(cid,'read') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.kb_containers WHERE id=cid FOR UPDATE;
 IF NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF (SELECT revision FROM public.kb_containers WHERE id=cid)<>(p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 SELECT * INTO old FROM public.kb_objects WHERE id=oid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
 IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF COALESCE((p_payload->>'expected_revision')::bigint,0)<1 OR COALESCE((p_payload->>'expected_container_revision')::bigint,0)<1 OR jsonb_typeof(p_payload->'nodes') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'KB_INVALID_LAYOUT' USING ERRCODE='22023'; END IF;
 IF old.current_revision<>(p_payload->>'expected_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF placement IS NULL OR placement NOT IN ('free','child','before','after') OR (placement='free')<>(target IS NULL) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 SELECT public.kb_summary_scope(cid,ARRAY[oid]) INTO subtree;
 IF target=ANY(subtree) THEN RAISE EXCEPTION 'KB_TREE_CYCLE' USING ERRCODE='22023'; END IF;
 pid:=old.parent_id;
 IF target IS NOT NULL THEN
  SELECT CASE WHEN placement='child' THEN id ELSE parent_id END INTO pid FROM public.kb_objects WHERE id=target AND container_id=cid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 END IF;
 IF jsonb_array_length(p_payload->'nodes') NOT BETWEEN 1 AND 10000 OR
  (SELECT count(*)<>count(DISTINCT v->>'node_id') FROM jsonb_array_elements(p_payload->'nodes') v) THEN RAISE EXCEPTION 'KB_INVALID_LAYOUT' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM unnest(subtree) s WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'nodes') v JOIN public.kb_canvas_nodes n ON n.id=(v->>'node_id')::uuid WHERE n.object_id=s)) THEN RAISE EXCEPTION 'KB_INCOMPLETE_BRANCH' USING ERRCODE='22023'; END IF;
 FOR node IN SELECT * FROM jsonb_array_elements(p_payload->'nodes') LOOP
  UPDATE public.kb_canvas_nodes SET x=(node->'position'->>'x')::float8,y=(node->'position'->>'y')::float8,revision=revision+1
   WHERE id=(node->>'node_id')::uuid AND container_id=cid AND revision=(node->>'expected_revision')::bigint;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 END LOOP;
 IF pid IS DISTINCT FROM old.parent_id THEN
  UPDATE public.kb_objects SET parent_id=pid,current_revision=current_revision+1 WHERE id=oid;
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
   SELECT oid,cid,org,old.current_revision+1,v.title,v.body,pid,actor FROM public.kb_object_versions v WHERE v.object_id=oid AND v.revision=old.current_revision;
 END IF;
 IF placement<>'free' THEN
  SELECT array_agg(id ORDER BY sort_order,id) INTO siblings FROM public.kb_objects WHERE container_id=cid AND parent_id IS NOT DISTINCT FROM pid AND id<>oid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
  FOREACH sibling IN ARRAY COALESCE(siblings,'{}'::uuid[]) LOOP
   IF sibling=target AND placement='before' THEN final_order:=array_append(final_order,oid); inserted:=true; END IF;
   final_order:=array_append(final_order,sibling);
   IF sibling=target AND placement='after' THEN final_order:=array_append(final_order,oid); inserted:=true; END IF;
  END LOOP;
  IF NOT inserted THEN final_order:=array_append(final_order,oid); END IF;
  UPDATE public.kb_objects o SET sort_order=t.ordinal*1024 FROM unnest(final_order) WITH ORDINALITY AS t(id,ordinal) WHERE o.id=t.id;
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command_id,'move_branch',rev);
 response:=jsonb_build_object('command_id',p_command_id,'status','applied','object_id',oid,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,p_command_id,org,cid,full_request,response);
 RETURN response;
END $$;
CREATE OR REPLACE FUNCTION public.kb_apply_batch_command(p_command uuid,p_operation text,p_payload jsonb) RETURNS jsonb
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
 SELECT public.kb_summary_scope(cid,roots) INTO ids;
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
 IF p_operation='delete_branches' AND p_payload->'nodes' IS NOT NULL AND p_payload->'nodes'<>'null'::jsonb THEN
  IF (SELECT count(DISTINCT v->>'node_id') FROM jsonb_array_elements(p_payload->'nodes') v)<>jsonb_array_length(p_payload->'nodes') THEN RAISE EXCEPTION 'KB_INVALID_LAYOUT' USING ERRCODE='22023';END IF;
  FOR item IN SELECT value node FROM jsonb_array_elements(p_payload->'nodes') LOOP
   UPDATE public.kb_canvas_nodes n SET x=(item.node->'position'->>'x')::float8,y=(item.node->'position'->>'y')::float8,revision=revision+1 WHERE n.id=(item.node->>'node_id')::uuid AND n.container_id=cid AND n.revision=(item.node->>'expected_revision')::bigint AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=n.object_id AND NOT o.undo_hidden AND o.deleted_at IS NULL);
   IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001';END IF;
  END LOOP;
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command,p_operation,rev);
 result:=jsonb_build_object('command_id',p_command,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
  VALUES(actor,p_command,org,cid,jsonb_build_object('operation',p_operation,'payload',p_payload),result);
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION public.kb_apply_group_move(p_command uuid,p_payload jsonb) RETURNS jsonb
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
 SELECT public.kb_summary_scope(cid,roots) INTO ids;
 SELECT array_agg(o.id ORDER BY o.sort_order,o.id) INTO roots FROM public.kb_objects o WHERE o.id=ANY(ids) AND (o.parent_id IS NULL OR NOT o.parent_id=ANY(ids)) AND NOT EXISTS(SELECT 1 FROM public.kb_canvas_nodes n WHERE n.object_id=o.id AND n.canvas_data ? 'summary' AND jsonb_array_length(n.canvas_data->'summary'->'members')>0 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(n.canvas_data->'summary'->'members') m WHERE NOT m::uuid=ANY(ids)));
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
CREATE OR REPLACE FUNCTION public.kb_branch_snapshot(p_id uuid,p_payload jsonb DEFAULT NULL) RETURNS jsonb
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
  SELECT public.kb_summary_scope(cid,ids) INTO expanded;
  IF cardinality(expanded)<>cardinality(ids) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  SELECT jsonb_agg(jsonb_build_object('id',o.id,'container_id',cid,'parent_id',o.parent_id,'kind',o.kind,'title',o.title,'revision',o.current_revision,'sort_order',o.sort_order,
   'node_id',n.id,'layout_revision',n.revision,'x',n.x,'y',n.y,'canvas_data',n.canvas_data,'body',v.body,'files',COALESCE((SELECT jsonb_agg(jsonb_build_object('binding_id',b.id,'title',b.title,'version_id',bv.version_id) ORDER BY b.id)
    FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions bv ON bv.binding_id=b.id AND bv.revision=CASE WHEN n.canvas_data->'image'->>'binding_id'=b.id::text THEN (n.canvas_data->'image'->>'revision')::bigint WHEN n.canvas_data->'video'->>'binding_id'=b.id::text THEN (n.canvas_data->'video'->>'revision')::bigint ELSE b.current_revision END WHERE b.object_id=o.id AND b.removed_at IS NULL),'[]'::jsonb)) ORDER BY o.id)
   INTO contents FROM public.kb_objects o JOIN public.kb_canvas_nodes n ON n.object_id=o.id JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=ANY(ids);
  IF pg_column_size(contents)>16777216 THEN RAISE EXCEPTION 'KB_SNAPSHOT_CAPACITY' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.id),'[]'::jsonb) INTO edges FROM public.kb_relations r WHERE r.source_object_id=ANY(ids) AND r.target_object_id=ANY(ids);
  DELETE FROM public.kb_branch_snapshots WHERE actor_id=actor AND expires_at<=now();
  INSERT INTO public.kb_branch_snapshots(id,actor_id,org_id,container_id,request_payload,nodes,relations) VALUES(p_id,actor,org,cid,p_payload,contents,edges) RETURNING * INTO snap;
 END IF;
 RETURN jsonb_build_object('snapshot_id',snap.id,'nodes',(SELECT jsonb_agg(v-'body'-'files') FROM jsonb_array_elements(snap.nodes) v));
END $$;
CREATE FUNCTION public.kb_reconcile_summaries(cid uuid,op text,p jsonb,result jsonb,before_state jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE row record; g jsonb; members jsonb; first_parent text; target uuid:=(p->>'object_id')::uuid;replacement uuid:=(result->>'object_id')::uuid;kids uuid[];
BEGIN
 IF op='delete_topic' THEN SELECT ARRAY(SELECT (v->>'id')::uuid FROM jsonb_array_elements(before_state->'objects') v WHERE v->>'parent_id'=target::text AND NOT(v->'canvas_data' ? 'summary')) INTO kids;END IF;
 FOR row IN SELECT n.object_id,n.id,n.canvas_data FROM public.kb_canvas_nodes n JOIN public.kb_objects o ON o.id=n.object_id WHERE n.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL AND n.canvas_data ? 'summary' LOOP
  g:=row.canvas_data->'summary';
  SELECT COALESCE(jsonb_agg(to_jsonb(v.id::text) ORDER BY m.ordinal,v.sub),'[]'::jsonb) INTO members FROM jsonb_array_elements_text(g->'members') WITH ORDINALITY m(id,ordinal)
   CROSS JOIN LATERAL (SELECT k id,s sub FROM unnest(CASE WHEN op='insert_parent' AND m.id::uuid=target THEN ARRAY[replacement] WHEN op='delete_topic' AND m.id::uuid=target THEN kids ELSE ARRAY[m.id::uuid] END) WITH ORDINALITY k(k,s)) v
   WHERE EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=v.id AND o.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL);
  IF (SELECT count(DISTINCT COALESCE(parent_id::text,'root')) FROM public.kb_objects WHERE id IN(SELECT m::uuid FROM jsonb_array_elements_text(members) m))>1 THEN
   SELECT v->>'parent_id' INTO first_parent FROM jsonb_array_elements(before_state->'objects') v WHERE v->>'id'=g->'members'->>0;
   SELECT COALESCE(jsonb_agg(m),'[]'::jsonb) INTO members FROM jsonb_array_elements(members) m WHERE EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=(m#>>'{}')::uuid AND o.parent_id::text IS NOT DISTINCT FROM first_parent);
  END IF;
  IF jsonb_array_length(members)=0 THEN
   UPDATE public.kb_objects SET undo_hidden=true WHERE id=ANY(public.kb_summary_scope(cid,ARRAY[row.object_id]));
  ELSIF members IS DISTINCT FROM g->'members' THEN
   UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{summary,members}',members),revision=revision+1 WHERE id=row.id;
  END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.kb_reconcile_summaries(uuid,text,jsonb,jsonb,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_canvas_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid; revision_before bigint;
 before_state jsonb; after_state jsonb; result jsonb; prior public.kb_mutation_receipts%ROWTYPE;
 obj jsonb;
 tracked boolean:=p_operation IN ('create_project','create_object','update_title','connect','move_branch','reparent','move_nodes','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent','delete_topic','update_map_theme','create_relation_topic');
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(org::text,''),0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command_id;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>jsonb_build_object('operation',p_operation,'payload',p_payload) THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 IF p_operation<>'create_project' THEN
  IF p_operation IN ('create_object','set_member','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent','delete_topic','update_map_theme','create_relation_topic') THEN cid:=(p_payload->>'container_id')::uuid;
  ELSIF p_operation='move_nodes' THEN SELECT container_id INTO cid FROM public.kb_canvas_nodes WHERE id=(p_payload->'nodes'->0->>'node_id')::uuid;
  ELSE SELECT container_id INTO cid FROM public.kb_objects WHERE id=COALESCE(p_payload->>'object_id',p_payload->>'source_object_id')::uuid AND deleted_at IS NULL AND NOT undo_hidden; END IF;
  IF cid IS NULL OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  SELECT revision INTO revision_before FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.kb_canvas_history_heads WHERE actor_id=actor AND container_id=cid AND revision=revision_before) THEN
   UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND container_id=cid;
  END IF;
  IF EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id IN (
    (p_payload->>'parent_id')::uuid,(p_payload->>'target_id')::uuid,(p_payload->>'target_object_id')::uuid)
    AND (o.undo_hidden OR o.deleted_at IS NOT NULL OR EXISTS(SELECT 1 FROM public.kb_containers c WHERE c.id=o.container_id AND c.undo_hidden))) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF p_operation='move_nodes' AND EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'nodes') v JOIN public.kb_canvas_nodes n ON n.id=(v->>'node_id')::uuid JOIN public.kb_objects o ON o.id=n.object_id WHERE o.undo_hidden) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF p_operation='create_object' AND (p_payload->>'after_object_id' IS NOT NULL OR p_payload->>'before_object_id' IS NOT NULL) AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  before_state:=public.kb_canvas_state(cid);
 ELSE before_state:='{"container_hidden":true,"objects":[],"relations":[]}'::jsonb;
 END IF;
 IF p_operation IN ('update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent','delete_topic','update_map_theme','create_relation_topic') AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF p_operation='update_title' AND p_payload->'title_format' IS NOT NULL AND p_payload->'title_format'<>'null'::jsonb AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF p_operation='update_title' AND p_payload->'nodes' IS NOT NULL AND p_payload->'nodes'<>'null'::jsonb THEN PERFORM public.kb_title_layout(cid,p_payload); END IF;
 IF p_operation='create_relation_topic' THEN result:=public.kb_create_relation_topic(p_command_id,p_payload);
 ELSIF p_operation='update_map_theme' THEN result:=public.kb_update_map_theme(p_command_id,p_payload);
 ELSIF p_operation='delete_topic' THEN result:=public.kb_delete_topic(p_command_id,p_payload);
 ELSIF p_operation IN ('create_topics','insert_parent') THEN result:=public.kb_create_topics(p_command_id,p_operation,p_payload);
 ELSIF p_operation='create_template' THEN result:=public.kb_create_template(p_command_id,p_payload);
 ELSIF p_operation='update_relation' THEN result:=public.kb_update_relation(p_command_id,p_payload);
 ELSIF p_operation='create_summaries' THEN result:=public.kb_create_summaries(p_command_id,p_payload);
 ELSIF p_operation='update_canvas_data' THEN result:=public.kb_update_canvas_data(p_command_id,p_payload);
 ELSIF p_operation IN ('delete_relation','reconnect_relation') THEN result:=public.kb_apply_relation_command(p_command_id,p_operation,p_payload);
 ELSIF p_operation='move_branches' THEN result:=public.kb_apply_group_move(p_command_id,p_payload);
 ELSIF p_operation='copy_branches' AND p_payload->>'snapshot_id' IS NOT NULL THEN result:=public.kb_paste_snapshot(p_command_id,p_payload);
 ELSIF p_operation IN ('copy_branches','delete_branches') THEN result:=public.kb_apply_batch_command(p_command_id,p_operation,p_payload);
 ELSIF p_operation='move_branch' THEN result:=public.kb_apply_branch_command(p_command_id,p_payload);
 ELSE result:=public.kb_apply_command(p_command_id,p_operation,p_payload); END IF;
 IF p_operation='create_object' AND (p_payload->>'after_object_id' IS NOT NULL OR p_payload->>'before_object_id' IS NOT NULL) THEN PERFORM public.kb_insert_after((result->>'object_id')::uuid,p_payload); END IF;
 IF cid IS NULL THEN SELECT container_id INTO cid FROM public.kb_objects WHERE id=(result->>'object_id')::uuid; END IF;
 IF p_operation IN ('insert_parent','delete_topic','delete_branches','move_branch','move_branches','reparent') THEN PERFORM public.kb_reconcile_summaries(cid,p_operation,p_payload,result,before_state); END IF;
 IF p_operation='delete_branches' THEN PERFORM public.kb_prune_canvas_groups(cid); END IF;
 -- Apply presentation inside the same transaction and history entry as the title.
 IF p_operation IN ('update_title','create_object','create_template','create_topics','insert_parent') THEN
  IF p_operation IN ('create_template','create_topics','insert_parent') THEN
   obj:=p_payload->'topics'->0->'title_format';
  ELSE obj:=p_payload->'title_format'; END IF;
  IF obj IS NOT NULL AND obj<>'null'::jsonb THEN
   PERFORM public.kb_validate_title_format(obj,(SELECT title FROM public.kb_objects WHERE id=(result->>'object_id')::uuid));
   UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{title_format}',obj),revision=revision+1 WHERE object_id=(result->>'object_id')::uuid;
  ELSIF p_operation='update_title' THEN
   UPDATE public.kb_canvas_nodes SET canvas_data=canvas_data-'title_format',revision=revision+1 WHERE object_id=(p_payload->>'object_id')::uuid AND canvas_data ? 'title_format';
  END IF;
 END IF;
 IF p_operation='update_canvas_data' THEN
  FOR obj IN SELECT jsonb_build_object('format',n.canvas_data->'title_format','title',o.title) FROM public.kb_canvas_nodes n JOIN public.kb_objects o ON o.id=n.object_id WHERE n.object_id IN (SELECT (v->>'object_id')::uuid FROM jsonb_array_elements(p_payload->'updates') v) AND n.canvas_data ? 'title_format' LOOP
   PERFORM public.kb_validate_title_format(obj->'format',obj->>'title');
  END LOOP;
 END IF;
 IF p_operation IN ('insert_parent','delete_topic','delete_branches','move_branch','move_branches','reparent') THEN PERFORM public.kb_reconcile_boundaries(cid,p_operation,p_payload,result,before_state); END IF;
 after_state:=public.kb_canvas_state(cid);
 IF before_state<>after_state THEN
  UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND state='undone';
 END IF;
 IF tracked AND before_state<>after_state THEN
  INSERT INTO public.kb_canvas_history(actor_id,org_id,command_id,container_id,operation,before_state,after_state,state)
   VALUES(actor,org,p_command_id,cid,p_operation,public.kb_canvas_patch(before_state,after_state),public.kb_canvas_patch(after_state,before_state),'applied');
 ELSIF NOT tracked THEN
  UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND container_id=cid;
 END IF;
 INSERT INTO public.kb_canvas_history_heads(actor_id,container_id,revision) SELECT actor,cid,revision FROM public.kb_containers WHERE id=cid
  ON CONFLICT(actor_id,container_id) DO UPDATE SET revision=EXCLUDED.revision;
 DELETE FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND
  (state='discarded' OR sequence NOT IN (SELECT sequence FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org ORDER BY sequence DESC LIMIT 100));
 RETURN result;
END $$;
RESET ROLE;
