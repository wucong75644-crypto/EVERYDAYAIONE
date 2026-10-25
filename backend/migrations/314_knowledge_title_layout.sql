-- Title and its collision-avoidance layout share one receipt and undo entry.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_title_layout(cid uuid,payload jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE node jsonb;
BEGIN
 IF NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF (SELECT revision FROM public.kb_containers WHERE id=cid AND NOT undo_hidden) IS DISTINCT FROM (payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF jsonb_typeof(payload->'nodes')<>'array' OR jsonb_array_length(payload->'nodes')>10000 OR (SELECT count(DISTINCT v->>'node_id') FROM jsonb_array_elements(payload->'nodes') v)<>jsonb_array_length(payload->'nodes') THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 FOR node IN SELECT * FROM jsonb_array_elements(payload->'nodes') LOOP
  IF node->'position'->>'x' IS NULL OR node->'position'->>'y' IS NULL OR (node->'position'->>'x')::float8 NOT BETWEEN -1000000 AND 1000000 OR (node->'position'->>'y')::float8 NOT BETWEEN -1000000 AND 1000000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  UPDATE public.kb_canvas_nodes n SET x=(node->'position'->>'x')::float8,y=(node->'position'->>'y')::float8,revision=revision+1
   WHERE n.id=(node->>'node_id')::uuid AND n.container_id=cid AND n.revision=(node->>'expected_revision')::bigint AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=n.object_id AND o.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL);
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.kb_title_layout(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_canvas_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid; revision_before bigint;
 before_state jsonb; after_state jsonb; result jsonb; prior public.kb_mutation_receipts%ROWTYPE;
 tracked boolean:=p_operation IN ('create_project','create_object','update_title','connect','move_branch','reparent','move_nodes','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation');
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
  IF p_operation IN ('create_object','set_member','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation') THEN cid:=(p_payload->>'container_id')::uuid;
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
  IF p_operation='create_object' AND p_payload->>'after_object_id' IS NOT NULL AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  before_state:=public.kb_canvas_state(cid);
 ELSE before_state:='{"container_hidden":true,"objects":[],"relations":[]}'::jsonb;
 END IF;
 IF p_operation='update_title' AND p_payload->'nodes' IS NOT NULL AND p_payload->'nodes'<>'null'::jsonb THEN PERFORM public.kb_title_layout(cid,p_payload); END IF;
 IF p_operation IN ('delete_relation','reconnect_relation') THEN result:=public.kb_apply_relation_command(p_command_id,p_operation,p_payload);
 ELSIF p_operation='move_branches' THEN result:=public.kb_apply_group_move(p_command_id,p_payload);
 ELSIF p_operation='copy_branches' AND p_payload->>'snapshot_id' IS NOT NULL THEN result:=public.kb_paste_snapshot(p_command_id,p_payload);
 ELSIF p_operation IN ('copy_branches','delete_branches') THEN result:=public.kb_apply_batch_command(p_command_id,p_operation,p_payload);
 ELSIF p_operation='move_branch' THEN result:=public.kb_apply_branch_command(p_command_id,p_payload);
 ELSE result:=public.kb_apply_command(p_command_id,p_operation,p_payload); END IF;
 IF p_operation='create_object' AND p_payload->>'after_object_id' IS NOT NULL THEN PERFORM public.kb_insert_after((result->>'object_id')::uuid,p_payload); END IF;
 IF cid IS NULL THEN SELECT container_id INTO cid FROM public.kb_objects WHERE id=(result->>'object_id')::uuid; END IF;
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
