-- Node width and deletion that promotes children, with atomic history.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.kb_validate_canvas_data(cid uuid,data jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g jsonb; member jsonb;
BEGIN
 IF jsonb_typeof(data)<>'object' OR pg_column_size(data)>65536 OR EXISTS(SELECT 1 FROM jsonb_object_keys(data) k WHERE k NOT IN ('note','labels','task','summary','boundary','style','image','video','link','width')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'width' AND (jsonb_typeof(data->'width')<>'number' OR (data->>'width')::numeric NOT BETWEEN 160 AND 800 OR (data->>'width')::numeric<>trunc((data->>'width')::numeric)) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'image' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions v ON v.binding_id=b.id JOIN public.kb_file_versions f ON f.id=v.version_id WHERE f.mime_type IN ('image/png','image/jpeg','image/webp','image/gif') AND b.container_id=cid AND b.id=(data->'image'->>'binding_id')::uuid AND v.revision=(data->'image'->>'revision')::bigint) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF data ? 'video' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions v ON v.binding_id=b.id JOIN public.kb_file_versions f ON f.id=v.version_id WHERE f.mime_type='video/mp4' AND b.container_id=cid AND b.id=(data->'video'->>'binding_id')::uuid AND v.revision=(data->'video'->>'revision')::bigint) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 FOR g IN SELECT value FROM jsonb_each(data) WHERE key IN ('summary','boundary') LOOP
  IF jsonb_typeof(g->'members')<>'array' OR jsonb_array_length(g->'members') NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  IF (SELECT count(DISTINCT parent_id) FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')))<>1 OR EXISTS(SELECT 1 FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')) AND parent_id IS NULL) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  FOR member IN SELECT * FROM jsonb_array_elements(g->'members') LOOP
   IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=(member#>>'{}')::uuid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  END LOOP;
 END LOOP;
END $$;
CREATE FUNCTION public.kb_delete_topic(cmd uuid,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE cid uuid:=(p->>'container_id')::uuid; actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 target public.kb_objects%ROWTYPE; child public.kb_objects%ROWTYPE; peer record; ids uuid[]:=ARRAY[]::uuid[]; kids uuid[]; rev bigint; result jsonb; eid uuid:=gen_random_uuid();
 row record; data jsonb; key text; members jsonb; receiver uuid;
BEGIN
 IF NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO target FROM public.kb_objects WHERE id=(p->>'object_id')::uuid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden FOR UPDATE;
 IF NOT FOUND OR target.current_revision<>(p->>'expected_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 PERFORM public.kb_title_layout(cid,p);
 SELECT COALESCE(array_agg(id ORDER BY sort_order,id),'{}'::uuid[]) INTO kids FROM public.kb_objects WHERE parent_id=target.id AND deleted_at IS NULL AND NOT undo_hidden;
 FOR peer IN SELECT id FROM public.kb_objects WHERE container_id=cid AND parent_id IS NOT DISTINCT FROM target.parent_id AND deleted_at IS NULL AND NOT undo_hidden ORDER BY sort_order,id LOOP
  IF peer.id=target.id THEN ids:=ids||kids; ELSE ids:=array_append(ids,peer.id); END IF;
 END LOOP;
 FOR child IN SELECT * FROM public.kb_objects WHERE id=ANY(kids) LOOP
  UPDATE public.kb_objects SET parent_id=target.parent_id,current_revision=current_revision+1 WHERE id=child.id;
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
   SELECT child.id,cid,org,child.current_revision+1,v.title,v.body,target.parent_id,actor FROM public.kb_object_versions v WHERE v.object_id=child.id AND v.revision=child.current_revision;
 END LOOP;
 UPDATE public.kb_objects o SET sort_order=t.ordinal*1024 FROM unnest(ids) WITH ORDINALITY t(id,ordinal) WHERE o.id=t.id;
 -- A boundary belongs to its range, not the storage node chosen to carry it.
 SELECT canvas_data->'boundary' INTO data FROM public.kb_canvas_nodes WHERE object_id=target.id;
 IF data IS NOT NULL THEN
  SELECT n.id INTO receiver FROM public.kb_canvas_nodes n JOIN public.kb_objects o ON o.id=n.object_id
   WHERE o.container_id=cid AND o.id<>target.id AND NOT o.undo_hidden AND o.deleted_at IS NULL
   AND NOT(n.canvas_data ? 'boundary') AND (o.id=ANY(kids) OR o.id IN(SELECT value::uuid FROM jsonb_array_elements_text(data->'members')))
   ORDER BY o.sort_order,o.id LIMIT 1;
  IF receiver IS NOT NULL THEN UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_set(canvas_data,ARRAY['boundary'],data),revision=revision+1 WHERE id=receiver; END IF;
 END IF;
 UPDATE public.kb_objects SET undo_hidden=true WHERE id=target.id;
 -- Expand affected ranges to promoted children; remove ranges that no longer share a non-root parent.
 FOR row IN SELECT n.id,n.canvas_data FROM public.kb_canvas_nodes n JOIN public.kb_objects o ON o.id=n.object_id WHERE n.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL AND (n.canvas_data ? 'summary' OR n.canvas_data ? 'boundary') LOOP
  data:=row.canvas_data;
  FOREACH key IN ARRAY ARRAY['summary','boundary'] LOOP
   IF data ? key THEN
    SELECT COALESCE(jsonb_agg(value ORDER BY ordinal,sub),'[]'::jsonb) INTO members FROM (
     SELECT to_jsonb(v.id::text) value,m.ordinal,v.sub FROM jsonb_array_elements_text(data->key->'members') WITH ORDINALITY m(id,ordinal)
     CROSS JOIN LATERAL (SELECT k id,s sub FROM unnest(CASE WHEN m.id::uuid=target.id THEN kids ELSE ARRAY[m.id::uuid] END) WITH ORDINALITY k(k,s)) v
    ) expanded;
    IF jsonb_array_length(members)=0 OR EXISTS(SELECT 1 FROM public.kb_objects WHERE id IN(SELECT value::uuid FROM jsonb_array_elements_text(members)) AND parent_id IS NULL) OR (SELECT count(DISTINCT parent_id) FROM public.kb_objects WHERE id IN(SELECT value::uuid FROM jsonb_array_elements_text(members)))<>1 THEN data:=data-key;
    ELSE data:=jsonb_set(data,ARRAY[key,'members'],members); END IF;
   END IF;
  END LOOP;
  UPDATE public.kb_canvas_nodes SET canvas_data=data,revision=revision+1 WHERE id=row.id AND canvas_data IS DISTINCT FROM data;
 END LOOP;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(eid,org,cid,actor,cmd,'delete_topic',rev);
 result:=jsonb_build_object('command_id',cmd,'status','applied','object_id',NULL,'revision',rev,'event_id',eid);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,cmd,org,cid,jsonb_build_object('operation','delete_topic','payload',p),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_delete_topic(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_canvas_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid; revision_before bigint;
 before_state jsonb; after_state jsonb; result jsonb; prior public.kb_mutation_receipts%ROWTYPE;
 tracked boolean:=p_operation IN ('create_project','create_object','update_title','connect','move_branch','reparent','move_nodes','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent','delete_topic');
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
  IF p_operation IN ('create_object','set_member','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent','delete_topic') THEN cid:=(p_payload->>'container_id')::uuid;
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
 IF p_operation IN ('update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent','delete_topic') AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF p_operation='update_title' AND p_payload->'nodes' IS NOT NULL AND p_payload->'nodes'<>'null'::jsonb THEN PERFORM public.kb_title_layout(cid,p_payload); END IF;
 IF p_operation='delete_topic' THEN result:=public.kb_delete_topic(p_command_id,p_payload);
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
