-- Relation anchors/control vectors are presentation only. Floating topic+relation is atomic.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.kb_validate_canvas_data(cid uuid,data jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g jsonb; member jsonb;
BEGIN
 IF data ? 'title_format' THEN PERFORM public.kb_validate_title_format(data->'title_format',NULL); END IF;
 IF jsonb_typeof(data)<>'object' OR pg_column_size(data)>65536 OR EXISTS(SELECT 1 FROM jsonb_object_keys(data) k WHERE k NOT IN ('note','labels','task','summary','boundary','style','image','video','link','width','structure','title_format','boundaries')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'width' AND (jsonb_typeof(data->'width')<>'number' OR (data->>'width')::numeric NOT BETWEEN 80 AND 800 OR (data->>'width')::numeric<>trunc((data->>'width')::numeric)) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'image' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions v ON v.binding_id=b.id JOIN public.kb_file_versions f ON f.id=v.version_id WHERE f.mime_type IN ('image/png','image/jpeg','image/webp','image/gif') AND b.container_id=cid AND b.id=(data->'image'->>'binding_id')::uuid AND v.revision=(data->'image'->>'revision')::bigint) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF data ? 'video' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions v ON v.binding_id=b.id JOIN public.kb_file_versions f ON f.id=v.version_id WHERE f.mime_type='video/mp4' AND b.container_id=cid AND b.id=(data->'video'->>'binding_id')::uuid AND v.revision=(data->'video'->>'revision')::bigint) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF data ? 'boundaries' THEN
  IF jsonb_typeof(data->'boundaries') IS DISTINCT FROM 'array' OR jsonb_array_length(data->'boundaries')>100 OR (SELECT count(DISTINCT v->>'id') FROM jsonb_array_elements(data->'boundaries') v)<>jsonb_array_length(data->'boundaries') THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  FOR g IN SELECT value FROM jsonb_array_elements(data->'boundaries') LOOP
   PERFORM (g->>'id')::uuid;
   IF g->>'id' IS NULL OR COALESCE(length(g->>'title'),0)>200 OR EXISTS(SELECT 1 FROM jsonb_object_keys(g) k WHERE k NOT IN ('id','members','title')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  END LOOP;
 END IF;
 FOR g IN SELECT value FROM jsonb_each(data) WHERE key IN ('summary','boundary') UNION ALL SELECT value FROM jsonb_array_elements(COALESCE(data->'boundaries','[]'::jsonb)) LOOP
  IF jsonb_typeof(g->'members')<>'array' OR jsonb_array_length(g->'members') NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  IF (SELECT count(DISTINCT parent_id) FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')))<>1 OR EXISTS(SELECT 1 FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')) AND parent_id IS NULL) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  FOR member IN SELECT * FROM jsonb_array_elements(g->'members') LOOP
   IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=(member#>>'{}')::uuid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  END LOOP;
 END LOOP;
END $$;
CREATE FUNCTION public.kb_validate_relation_appearance(a jsonb) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE k text; p jsonb; x float8; y float8;
BEGIN
 IF jsonb_typeof(a)<>'object' OR pg_column_size(a)>4096 OR EXISTS(SELECT 1 FROM jsonb_object_keys(a) key WHERE key NOT IN ('text','bend','source_anchor','target_anchor','source_control','target_control')) OR length(COALESCE(a->>'text',''))>200 OR abs(COALESCE((a->>'bend')::float8,0))>2000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 FOREACH k IN ARRAY ARRAY['source_anchor','target_anchor','source_control','target_control'] LOOP
  p:=a->k;
  IF p IS NULL OR p='null'::jsonb THEN CONTINUE; END IF;
  IF jsonb_typeof(p)<>'object' OR NOT(p ? 'x' AND p ? 'y') OR EXISTS(SELECT 1 FROM jsonb_object_keys(p) key WHERE key NOT IN('x','y')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  x:=(p->>'x')::float8;y:=(p->>'y')::float8;
  IF x IS NULL OR y IS NULL OR NOT(x BETWEEN -4000 AND 4000 AND y BETWEEN -4000 AND 4000) OR (k LIKE '%anchor' AND (NOT(x BETWEEN 0 AND 1 AND y BETWEEN 0 AND 1) OR NOT(x IN(0,1) OR y IN(0,1)))) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.kb_validate_relation_appearance(jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_update_relation(cmd uuid,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE cid uuid:=(p->>'container_id')::uuid; actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; rev bigint; eid uuid:=gen_random_uuid(); result jsonb;
BEGIN
 PERFORM public.kb_validate_relation_appearance(p->'appearance');
 UPDATE public.kb_relations r SET appearance=jsonb_strip_nulls(p->'appearance') WHERE id=(p->>'relation_id')::uuid AND container_id=cid AND NOT EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id IN(r.source_object_id,r.target_object_id) AND (o.deleted_at IS NOT NULL OR o.undo_hidden));
 IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(eid,org,cid,actor,cmd,'update_relation',rev);
 result:=jsonb_build_object('command_id',cmd,'status','applied','object_id',NULL,'revision',rev,'event_id',eid);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,cmd,org,cid,jsonb_build_object('operation','update_relation','payload',p),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_update_relation(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;

CREATE FUNCTION public.kb_create_relation_topic(cmd uuid,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE cid uuid:=(p->>'container_id')::uuid; actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 oid uuid:=gen_random_uuid(); sid uuid:=(p->>'source_object_id')::uuid; rid uuid:=(p->>'relation_id')::uuid; old public.kb_relations%ROWTYPE; rev bigint; eid uuid:=gen_random_uuid(); result jsonb;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=sid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF rid IS NOT NULL THEN
  SELECT * INTO old FROM public.kb_relations WHERE id=rid AND container_id=cid;
  IF NOT FOUND OR (CASE WHEN p->>'endpoint'='source' THEN old.target_object_id ELSE old.source_object_id END)<>sid OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=old.source_object_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=old.target_object_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 END IF;
 IF NOT((p->'position'->>'x')::float8 BETWEEN -1000000 AND 1000000 AND (p->'position'->>'y')::float8 BETWEEN -1000000 AND 1000000) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 INSERT INTO public.kb_objects(id,org_id,container_id,parent_id,kind,title,created_by) VALUES(oid,org,cid,NULL,'topic','自由主题',actor);
 INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by) VALUES(oid,cid,org,1,'自由主题','',NULL,actor);
 INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y) VALUES(org,cid,oid,(p->'position'->>'x')::float8,(p->'position'->>'y')::float8);
 IF rid IS NULL THEN
  INSERT INTO public.kb_relations(org_id,container_id,source_object_id,target_object_id,relation_type) VALUES(org,cid,sid,oid,p->>'relation_type');
 ELSE
  UPDATE public.kb_relations SET source_object_id=CASE WHEN p->>'endpoint'='source' THEN oid ELSE sid END,target_object_id=CASE WHEN p->>'endpoint'='source' THEN sid ELSE oid END,appearance=appearance-CASE WHEN p->>'endpoint'='source' THEN 'source_anchor' ELSE 'target_anchor' END-CASE WHEN p->>'endpoint'='source' THEN 'source_control' ELSE 'target_control' END WHERE id=rid;
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(eid,org,cid,actor,cmd,'create_relation_topic',rev);
 result:=jsonb_build_object('command_id',cmd,'status','applied','object_id',oid,'revision',rev,'event_id',eid);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,cmd,org,cid,jsonb_build_object('operation','create_relation_topic','payload',p),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_create_relation_topic(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_apply_relation_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 cid uuid:=(p_payload->>'container_id')::uuid; rid uuid:=(p_payload->>'relation_id')::uuid;
 source_id uuid; target_id uuid; rev bigint; rel public.kb_relations%ROWTYPE;
 event_id uuid:=gen_random_uuid(); result jsonb;
BEGIN
 IF p_operation NOT IN ('delete_relation','reconnect_relation') THEN RAISE EXCEPTION 'KB_INVALID_RELATION' USING ERRCODE='22023'; END IF;
 IF NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT revision INTO rev FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF rev IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 SELECT * INTO rel FROM public.kb_relations WHERE id=rid AND container_id=cid;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=rel.source_object_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
  OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=rel.target_object_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
  THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF p_operation='reconnect_relation' THEN
  source_id:=(p_payload->>'source_object_id')::uuid; target_id:=(p_payload->>'target_object_id')::uuid;
  IF source_id IS NULL OR target_id IS NULL OR source_id=target_id THEN RAISE EXCEPTION 'KB_INVALID_RELATION' USING ERRCODE='22023'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=source_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
   OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=target_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
   THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF EXISTS(SELECT 1 FROM public.kb_relations WHERE container_id=cid AND source_object_id=source_id AND target_object_id=target_id AND relation_type=rel.relation_type AND id<>rid)
   THEN RAISE EXCEPTION 'KB_RELATION_DUPLICATE' USING ERRCODE='22023'; END IF;
  IF p_payload->'appearance' IS NOT NULL AND p_payload->'appearance'<>'null'::jsonb THEN PERFORM public.kb_validate_relation_appearance(p_payload->'appearance'); END IF;
  IF source_id IS DISTINCT FROM rel.source_object_id OR target_id IS DISTINCT FROM rel.target_object_id OR (p_payload->'appearance' IS NOT NULL AND p_payload->'appearance'<>'null'::jsonb AND rel.appearance IS DISTINCT FROM jsonb_strip_nulls(p_payload->'appearance')) THEN
   UPDATE public.kb_relations SET source_object_id=source_id,target_object_id=target_id,appearance=CASE WHEN p_payload->'appearance' IS NOT NULL AND p_payload->'appearance'<>'null'::jsonb THEN jsonb_strip_nulls(p_payload->'appearance') ELSE appearance END WHERE id=rid AND container_id=cid;
   UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
  END IF;
 ELSE
  DELETE FROM public.kb_relations WHERE id=rid AND container_id=cid;
  UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 END IF;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command_id,p_operation,rev);
 result:=jsonb_build_object('command_id',p_command_id,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
  VALUES(actor,p_command_id,org,cid,jsonb_build_object('operation',p_operation,'payload',p_payload),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_apply_relation_command(uuid,text,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
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
