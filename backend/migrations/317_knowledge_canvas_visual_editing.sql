-- Visual editing shares guarded canvas commands and history.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.kb_relations ADD COLUMN appearance jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE OR REPLACE FUNCTION public.kb_validate_canvas_data(cid uuid,data jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g jsonb; member jsonb;
BEGIN
 IF jsonb_typeof(data)<>'object' OR pg_column_size(data)>65536 OR EXISTS(SELECT 1 FROM jsonb_object_keys(data) k WHERE k NOT IN ('note','labels','task','summary','boundary','style','image')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'image' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions v ON v.binding_id=b.id JOIN public.kb_file_versions f ON f.id=v.version_id WHERE f.mime_type IN ('image/png','image/jpeg','image/webp','image/gif') AND b.container_id=cid AND b.id=(data->'image'->>'binding_id')::uuid AND v.revision=(data->'image'->>'revision')::bigint) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 FOR g IN SELECT value FROM jsonb_each(data) WHERE key IN ('summary','boundary') LOOP
  IF jsonb_typeof(g->'members')<>'array' OR jsonb_array_length(g->'members') NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  IF (SELECT count(DISTINCT parent_id) FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')))<>1 OR EXISTS(SELECT 1 FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')) AND parent_id IS NULL) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  FOR member IN SELECT * FROM jsonb_array_elements(g->'members') LOOP
   IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=(member#>>'{}')::uuid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  END LOOP;
 END LOOP;
END $$;
CREATE FUNCTION public.kb_update_relation(cmd uuid,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE cid uuid:=(p->>'container_id')::uuid; actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; rev bigint; eid uuid:=gen_random_uuid(); result jsonb;
BEGIN
 UPDATE public.kb_relations r SET appearance=p->'appearance' WHERE id=(p->>'relation_id')::uuid AND container_id=cid AND NOT EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id IN(r.source_object_id,r.target_object_id) AND (o.deleted_at IS NOT NULL OR o.undo_hidden));
 IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(eid,org,cid,actor,cmd,'update_relation',rev);
 result:=jsonb_build_object('command_id',cmd,'status','applied','object_id',NULL,'revision',rev,'event_id',eid);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,cmd,org,cid,jsonb_build_object('operation','update_relation','payload',p),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_update_relation(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;

CREATE OR REPLACE FUNCTION public.kb_canvas_restore(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; entry public.kb_canvas_history%ROWTYPE;
 prior public.kb_mutation_receipts%ROWTYPE; wanted jsonb; other jsonb; obj jsonb; rel jsonb;
 current public.kb_objects%ROWTYPE; oid uuid; cid uuid; rev bigint; body text; event_id uuid:=gen_random_uuid(); result jsonb;
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_operation NOT IN ('undo_canvas','redo_canvas') THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(org::text,''),0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command_id;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>jsonb_build_object('operation',p_operation,'payload',p_payload) THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 SELECT * INTO entry FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org
  AND state=CASE WHEN p_operation='undo_canvas' THEN 'applied' ELSE 'undone' END
  ORDER BY CASE WHEN p_operation='undo_canvas' THEN -sequence ELSE sequence END LIMIT 1;
 IF NOT FOUND OR entry.command_id IS DISTINCT FROM (p_payload->>'history_id')::uuid THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
 cid:=entry.container_id;
 PERFORM 1 FROM public.kb_containers WHERE id=cid FOR UPDATE;
 IF NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.kb_canvas_history_heads h JOIN public.kb_containers c ON c.id=h.container_id WHERE h.actor_id=actor AND h.container_id=cid AND h.revision=c.revision) THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
 wanted:=CASE WHEN p_operation='undo_canvas' THEN entry.before_state ELSE entry.after_state END;
 other:=CASE WHEN p_operation='undo_canvas' THEN entry.after_state ELSE entry.before_state END;
 -- Hide objects introduced by the forward step instead of deleting versions or bytes.
 FOR obj IN SELECT v FROM jsonb_array_elements(other->'objects') v WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(wanted->'objects') w WHERE w->>'id'=v->>'id') LOOP
  UPDATE public.kb_objects SET undo_hidden=true WHERE id=(obj->>'id')::uuid AND container_id=cid;
 END LOOP;
 FOR obj IN SELECT * FROM jsonb_array_elements(wanted->'objects') LOOP
  oid:=(obj->>'id')::uuid;
  SELECT * INTO current FROM public.kb_objects WHERE id=oid AND container_id=cid AND deleted_at IS NULL;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF current.title IS DISTINCT FROM obj->>'title' OR current.parent_id IS DISTINCT FROM (obj->>'parent_id')::uuid THEN
   SELECT v.body INTO body FROM public.kb_object_versions v WHERE v.object_id=oid AND v.revision=(obj->>'revision')::bigint;
   IF NOT FOUND THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
   UPDATE public.kb_objects SET title=obj->>'title',parent_id=(obj->>'parent_id')::uuid,current_revision=current_revision+1 WHERE id=oid;
   INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
    VALUES(oid,cid,org,current.current_revision+1,obj->>'title',body,(obj->>'parent_id')::uuid,actor);
  END IF;
  UPDATE public.kb_canvas_nodes SET canvas_data=COALESCE(obj->'canvas_data','{}'::jsonb),revision=revision+1 WHERE object_id=oid AND canvas_data IS DISTINCT FROM COALESCE(obj->'canvas_data','{}'::jsonb);
  -- Apply order after parent triggers have run; never decrement revision counters.
  UPDATE public.kb_objects SET undo_hidden=false,sort_order=(obj->>'sort_order')::bigint WHERE id=oid;
  UPDATE public.kb_canvas_nodes SET x=(obj->>'x')::float8,y=(obj->>'y')::float8,revision=revision+1
   WHERE id=(obj->>'node_id')::uuid AND object_id=oid AND (x IS DISTINCT FROM (obj->>'x')::float8 OR y IS DISTINCT FROM (obj->>'y')::float8);
 END LOOP;
 DELETE FROM public.kb_relations WHERE container_id=cid AND id IN (SELECT (v->>'id')::uuid FROM jsonb_array_elements(other->'relations') v)
  AND id NOT IN (SELECT (v->>'id')::uuid FROM jsonb_array_elements(wanted->'relations') v);
 FOR rel IN SELECT * FROM jsonb_array_elements(wanted->'relations') LOOP
  INSERT INTO public.kb_relations(id,org_id,container_id,source_object_id,target_object_id,relation_type,appearance)
   VALUES((rel->>'id')::uuid,org,cid,(rel->>'source_object_id')::uuid,(rel->>'target_object_id')::uuid,rel->>'relation_type',COALESCE(rel->'appearance','{}'::jsonb)) ON CONFLICT(id) DO UPDATE SET source_object_id=EXCLUDED.source_object_id,target_object_id=EXCLUDED.target_object_id,relation_type=EXCLUDED.relation_type,appearance=EXCLUDED.appearance
    WHERE kb_relations.container_id=EXCLUDED.container_id;
 END LOOP;
 UPDATE public.kb_containers SET undo_hidden=(wanted->>'container_hidden')::boolean,revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 UPDATE public.kb_canvas_history SET state=CASE WHEN p_operation='undo_canvas' THEN 'undone' ELSE 'applied' END WHERE sequence=entry.sequence;
 UPDATE public.kb_canvas_history_heads SET revision=rev WHERE actor_id=actor AND container_id=cid;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command_id,p_operation,rev);
 result:=jsonb_build_object('command_id',p_command_id,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,p_command_id,org,cid,jsonb_build_object('operation',p_operation,'payload',p_payload),result);
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION public.kb_canvas_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid; revision_before bigint;
 before_state jsonb; after_state jsonb; result jsonb; prior public.kb_mutation_receipts%ROWTYPE;
 tracked boolean:=p_operation IN ('create_project','create_object','update_title','connect','move_branch','reparent','move_nodes','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation');
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
  IF p_operation IN ('create_object','set_member','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation') THEN cid:=(p_payload->>'container_id')::uuid;
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
 IF p_operation IN ('update_canvas_data','create_summaries','update_relation') AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF p_operation='update_title' AND p_payload->'nodes' IS NOT NULL AND p_payload->'nodes'<>'null'::jsonb THEN PERFORM public.kb_title_layout(cid,p_payload); END IF;
 IF p_operation='update_relation' THEN result:=public.kb_update_relation(p_command_id,p_payload);
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
 IF p_operation='delete_branches' THEN PERFORM public.kb_prune_canvas_groups(cid); END IF;
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
  WITH RECURSIVE tree AS (SELECT id FROM public.kb_objects WHERE id=ANY(ids) UNION SELECT o.id FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id WHERE o.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL)
   SELECT array_agg(id) INTO expanded FROM tree;
  IF cardinality(expanded)<>cardinality(ids) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  SELECT jsonb_agg(jsonb_build_object('id',o.id,'container_id',cid,'parent_id',o.parent_id,'kind',o.kind,'title',o.title,'revision',o.current_revision,'sort_order',o.sort_order,
   'node_id',n.id,'layout_revision',n.revision,'x',n.x,'y',n.y,'canvas_data',n.canvas_data,'body',v.body,'files',COALESCE((SELECT jsonb_agg(jsonb_build_object('binding_id',b.id,'title',b.title,'version_id',bv.version_id) ORDER BY b.id)
    FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions bv ON bv.binding_id=b.id AND bv.revision=CASE WHEN n.canvas_data->'image'->>'binding_id'=b.id::text THEN (n.canvas_data->'image'->>'revision')::bigint ELSE b.current_revision END WHERE b.object_id=o.id AND b.removed_at IS NULL),'[]'::jsonb)) ORDER BY o.id)
   INTO contents FROM public.kb_objects o JOIN public.kb_canvas_nodes n ON n.object_id=o.id JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=ANY(ids);
  IF pg_column_size(contents)>16777216 THEN RAISE EXCEPTION 'KB_SNAPSHOT_CAPACITY' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.id),'[]'::jsonb) INTO edges FROM public.kb_relations r WHERE r.source_object_id=ANY(ids) AND r.target_object_id=ANY(ids);
  DELETE FROM public.kb_branch_snapshots WHERE actor_id=actor AND expires_at<=now();
  INSERT INTO public.kb_branch_snapshots(id,actor_id,org_id,container_id,request_payload,nodes,relations) VALUES(p_id,actor,org,cid,p_payload,contents,edges) RETURNING * INTO snap;
 END IF;
 RETURN jsonb_build_object('snapshot_id',snap.id,'nodes',(SELECT jsonb_agg(v-'body'-'files') FROM jsonb_array_elements(snap.nodes) v));
END $$;
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
  INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y) VALUES(org,cid,new_id,item.x+(p_payload->'offset'->>'x')::float8,item.y+(p_payload->'offset'->>'y')::float8);
  UPDATE public.kb_canvas_nodes SET canvas_data=COALESCE(item.canvas_data,'{}'::jsonb)-'summary'-'boundary' WHERE object_id=new_id;
  FOR file IN SELECT * FROM jsonb_array_elements(item.files) LOOP
   bid:=gen_random_uuid();
   INSERT INTO public.kb_file_bindings(id,object_id,container_id,org_id,title,current_revision,created_by) VALUES(bid,new_id,cid,org,file->>'title',1,actor);
   INSERT INTO public.kb_file_binding_versions(binding_id,revision,version_id,container_id,org_id) VALUES(bid,1,(file->>'version_id')::uuid,cid,org);
   IF item.canvas_data->'image'->>'binding_id'=file->>'binding_id' THEN UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{image,binding_id}',to_jsonb(bid::text)) WHERE object_id=new_id; UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,'{image,revision}','1'::jsonb) WHERE object_id=new_id; END IF;
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
CREATE OR REPLACE FUNCTION public.kb_update_canvas_data(cmd uuid,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid:=(p->>'container_id')::uuid; rev bigint; item jsonb; eid uuid:=gen_random_uuid(); result jsonb;
BEGIN
 FOR item IN SELECT * FROM jsonb_array_elements(p->'updates') LOOP
  IF item->'canvas_data' ? 'image' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings WHERE id=(item->'canvas_data'->'image'->>'binding_id')::uuid AND object_id=(item->>'object_id')::uuid AND removed_at IS NULL) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  PERFORM public.kb_validate_canvas_data(cid,item->'canvas_data');
  UPDATE public.kb_canvas_nodes n SET canvas_data=item->'canvas_data',revision=revision+1 WHERE object_id=(item->>'object_id')::uuid AND container_id=cid AND revision=(item->>'expected_layout_revision')::bigint AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=n.object_id AND o.deleted_at IS NULL AND NOT o.undo_hidden);
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 END LOOP;
 IF p ? 'nodes' THEN PERFORM public.kb_title_layout(cid,p); END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(eid,org,cid,actor,cmd,'update_canvas_data',rev);
 result:=jsonb_build_object('command_id',cmd,'status','applied','object_id',NULL,'revision',rev,'event_id',eid);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,cmd,org,cid,jsonb_build_object('operation','update_canvas_data','payload',p),result);
 RETURN result;
END $$;
RESET ROLE;
