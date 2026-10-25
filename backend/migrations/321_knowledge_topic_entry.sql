-- Parent insertion and outline forests are atomic canvas/history operations.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_create_topics(cmd uuid,op text,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE cid uuid:=(p->>'container_id')::uuid; parent uuid:=(p->>'parent_id')::uuid;
 actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 target public.kb_objects%ROWTYPE; target_data jsonb; target_body text; topic jsonb; ids uuid[]:=ARRAY[]::uuid[]; oid uuid; pid uuid; rev bigint; idx int:=0; pi int;
 item record; data jsonb; key text; members jsonb; eid uuid:=gen_random_uuid(); result jsonb;
BEGIN
 IF op NOT IN ('create_topics','insert_parent') OR NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF jsonb_array_length(p->'topics') NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF parent IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=parent AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF op='insert_parent' THEN
  SELECT * INTO target FROM public.kb_objects WHERE id=(p->>'object_id')::uuid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden;
  IF NOT FOUND OR target.current_revision<>(p->>'expected_revision')::bigint OR target.parent_id IS DISTINCT FROM parent THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF jsonb_array_length(p->'topics')<>1 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  SELECT canvas_data INTO target_data FROM public.kb_canvas_nodes WHERE object_id=target.id;
  SELECT body INTO target_body FROM public.kb_object_versions WHERE object_id=target.id AND revision=target.current_revision;
 END IF;
 IF p ? 'nodes' THEN PERFORM public.kb_title_layout(cid,p); END IF;
 FOR topic IN SELECT * FROM jsonb_array_elements(p->'topics') LOOP
  pi:=(topic->>'parent_index')::int;
  IF pi IS NULL OR pi < -1 OR pi>=idx THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  oid:=gen_random_uuid();pid:=CASE WHEN pi=-1 THEN parent ELSE ids[pi+1] END;
  INSERT INTO public.kb_objects(id,org_id,container_id,parent_id,kind,title,created_by) VALUES(oid,org,cid,pid,'topic',topic->>'title',actor);
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by) VALUES(oid,cid,org,1,topic->>'title','',pid,actor);
  INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y) VALUES(org,cid,oid,(topic->'position'->>'x')::float8,(topic->'position'->>'y')::float8);
  IF topic->>'structure' IS NOT NULL THEN
   PERFORM public.kb_validate_canvas_data(cid,jsonb_build_object('structure',topic->>'structure'));
   UPDATE public.kb_canvas_nodes SET canvas_data=jsonb_build_object('structure',topic->>'structure') WHERE object_id=oid;
  END IF;
  ids:=array_append(ids,oid);idx:=idx+1;
 END LOOP;
 IF op='insert_parent' THEN
  UPDATE public.kb_objects SET sort_order=target.sort_order WHERE id=ids[1];
  UPDATE public.kb_objects SET parent_id=ids[1],current_revision=current_revision+1 WHERE id=target.id;
  INSERT INTO public.kb_object_versions(object_id,org_id,container_id,revision,title,body,parent_id,created_by) VALUES(target.id,org,cid,target.current_revision+1,target.title,target_body,ids[1],actor);
  -- Summary/boundary semantics follow the wrapper, while original node content remains intact.
  UPDATE public.kb_canvas_nodes SET canvas_data=canvas_data||jsonb_strip_nulls(jsonb_build_object('summary',target_data->'summary','boundary',target_data->'boundary')) WHERE object_id=ids[1];
  UPDATE public.kb_canvas_nodes SET canvas_data=canvas_data-'summary'-'boundary',revision=revision+1 WHERE object_id=target.id;
  FOR item IN SELECT n.* FROM public.kb_canvas_nodes n WHERE n.container_id=cid LOOP
   data:=item.canvas_data;
   FOREACH key IN ARRAY ARRAY['summary','boundary'] LOOP
    IF data->key->'members' @> jsonb_build_array(target.id::text) THEN
     SELECT jsonb_agg(CASE WHEN value=target.id::text THEN to_jsonb(ids[1]::text) ELSE to_jsonb(value) END ORDER BY ord) INTO members FROM jsonb_array_elements_text(data->key->'members') WITH ORDINALITY AS m(value,ord);
     data:=jsonb_set(data,ARRAY[key,'members'],members);
    END IF;
   END LOOP;
   IF data IS DISTINCT FROM item.canvas_data THEN UPDATE public.kb_canvas_nodes SET canvas_data=data,revision=revision+1 WHERE id=item.id; END IF;
  END LOOP;
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(eid,org,cid,actor,cmd,op,rev);
 result:=jsonb_build_object('command_id',cmd,'status','applied','object_id',ids[1],'revision',rev,'event_id',eid);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,cmd,org,cid,jsonb_build_object('operation',op,'payload',p),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_create_topics(uuid,text,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_canvas_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid; revision_before bigint;
 before_state jsonb; after_state jsonb; result jsonb; prior public.kb_mutation_receipts%ROWTYPE;
 tracked boolean:=p_operation IN ('create_project','create_object','update_title','connect','move_branch','reparent','move_nodes','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent');
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
  IF p_operation IN ('create_object','set_member','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation','update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent') THEN cid:=(p_payload->>'container_id')::uuid;
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
 IF p_operation IN ('update_canvas_data','create_summaries','update_relation','create_template','create_topics','insert_parent') AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF p_operation='update_title' AND p_payload->'nodes' IS NOT NULL AND p_payload->'nodes'<>'null'::jsonb THEN PERFORM public.kb_title_layout(cid,p_payload); END IF;
 IF p_operation IN ('create_topics','insert_parent') THEN result:=public.kb_create_topics(p_command_id,p_operation,p_payload);
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
