-- A user content submission, including child topics, commits or rolls back together.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_apply_content(cmd uuid,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 cid uuid:=(p->>'container_id')::uuid; oid uuid:=(p->>'object_id')::uuid;
 previous public.kb_mutation_receipts%ROWTYPE; target public.kb_objects%ROWTYPE;
 before_state jsonb; after_state jsonb; result jsonb; child jsonb; child_result jsonb;
 child_cmd uuid; rev bigint; request jsonb:=jsonb_build_object('operation','apply_content','payload',p);
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN
  RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(org::text,''),0));
 SELECT * INTO previous FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=cmd;
 IF FOUND THEN
  IF previous.org_id IS DISTINCT FROM org OR previous.request_payload<>request THEN
   RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001';
  END IF;
  RETURN previous.result||jsonb_build_object('status','replayed');
 END IF;
 SELECT revision INTO rev FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
 IF NOT FOUND OR rev IS DISTINCT FROM (p->>'expected_container_revision')::bigint THEN
  RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001';
 END IF;
 IF length(p->>'body') NOT BETWEEN 1 AND 100000 OR jsonb_array_length(p->'children')>12 THEN
  RAISE EXCEPTION 'KB_INVALID_CONTENT' USING ERRCODE='22023';
 END IF;
 before_state:=public.kb_canvas_state(cid);
 IF NOT EXISTS(SELECT 1 FROM public.kb_canvas_history_heads WHERE actor_id=actor AND container_id=cid AND revision=rev) THEN
  UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND container_id=cid;
 END IF;
 IF jsonb_array_length(p->'nodes')>0 THEN PERFORM public.kb_title_layout(cid,p); END IF;
 IF oid IS NULL THEN
  child_cmd:=gen_random_uuid();
  result:=public.kb_apply_command(child_cmd,'create_object',jsonb_build_object(
   'container_id',cid,'parent_id',p->'parent_id','kind','topic','title',p->>'title','body',COALESCE(p->>'input_text',p->>'body'),'position',p->'position'));
  oid:=(result->>'object_id')::uuid;
  IF p->>'input_text' IS NOT NULL AND p->>'input_text' IS DISTINCT FROM p->>'body' THEN
   PERFORM public.kb_apply_command(gen_random_uuid(),'save_document',jsonb_build_object('object_id',oid,'expected_revision',1,'body',p->>'body'));
  END IF;
  IF p->'manual_layout' IS NOT NULL AND p->'manual_layout'<>'null'::jsonb THEN
   PERFORM public.kb_validate_canvas_data(cid,jsonb_build_object('manual_layout',p->'manual_layout'));
   UPDATE public.kb_canvas_nodes SET canvas_data=canvas_data||jsonb_build_object('manual_layout',p->'manual_layout') WHERE object_id=oid;
  END IF;
  IF p->>'structure' IS NOT NULL THEN
   PERFORM public.kb_validate_canvas_data(cid,jsonb_build_object('structure',p->>'structure'));
   UPDATE public.kb_canvas_nodes SET canvas_data=canvas_data||jsonb_build_object('structure',p->>'structure') WHERE object_id=oid;
  END IF;
  IF p->>'after_object_id' IS NOT NULL OR p->>'before_object_id' IS NOT NULL THEN
   PERFORM public.kb_insert_after(oid,p||jsonb_build_object('nodes','[]'::jsonb));
  END IF;
 ELSE
  SELECT * INTO target FROM public.kb_objects WHERE id=oid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden;
  IF NOT FOUND OR target.current_revision IS DISTINCT FROM (p->>'expected_revision')::bigint THEN
   RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001';
  END IF;
  -- A content edit never renames an existing node.
  child_cmd:=gen_random_uuid();
  result:=public.kb_apply_command(child_cmd,'save_document',jsonb_build_object('object_id',oid,
   'expected_revision',target.current_revision,'body',p->>'body'));
 END IF;
 FOR child IN SELECT * FROM jsonb_array_elements(p->'children') LOOP
  child_cmd:=gen_random_uuid();
  child_result:=public.kb_apply_command(child_cmd,'create_object',jsonb_build_object(
   'container_id',cid,'parent_id',oid,'kind','topic','title',child->>'title','body',child->>'body','position',child->'position'));
 END LOOP;
 after_state:=public.kb_canvas_state(cid);
 UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND state='undone';
 SELECT revision INTO rev FROM public.kb_containers WHERE id=cid;
 IF before_state<>after_state THEN
  INSERT INTO public.kb_canvas_history(actor_id,org_id,command_id,container_id,operation,before_state,after_state,state)
   VALUES(actor,org,cmd,cid,'apply_content',public.kb_canvas_patch(before_state,after_state),public.kb_canvas_patch(after_state,before_state),'applied');
 END IF;
 INSERT INTO public.kb_canvas_history_heads(actor_id,container_id,revision) VALUES(actor,cid,rev)
  ON CONFLICT(actor_id,container_id) DO UPDATE SET revision=EXCLUDED.revision;
 DELETE FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND
  (state='discarded' OR sequence NOT IN (SELECT sequence FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org ORDER BY sequence DESC LIMIT 100));
 result:=jsonb_build_object('command_id',cmd,'object_id',oid,'status','applied','revision',rev);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
  VALUES(actor,cmd,org,cid,request,result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_apply_content(uuid,jsonb) FROM PUBLIC,everydayai;
GRANT EXECUTE ON FUNCTION public.kb_apply_content(uuid,jsonb) TO everydayai_runtime;

-- Content versions participate in undo only for this new composite operation.
-- Existing canvas and project restore semantics stay in the previous implementation.
ALTER FUNCTION public.kb_canvas_restore(uuid,text,jsonb) RENAME TO kb_canvas_restore_before_content_input;
REVOKE ALL ON FUNCTION public.kb_canvas_restore_before_content_input(uuid,text,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE FUNCTION public.kb_canvas_restore(cmd uuid,op text,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
<<content_restore>>
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 entry public.kb_canvas_history%ROWTYPE; wanted jsonb; obj jsonb; result jsonb;
 saved_body text; live_body text; current_rev bigint; rev bigint;
BEGIN
 result:=public.kb_canvas_restore_before_content_input(cmd,op,p);
 IF result->>'status'='replayed' THEN RETURN result; END IF;
 SELECT * INTO entry FROM public.kb_canvas_history WHERE actor_id=actor AND command_id=(p->>'history_id')::uuid;
 IF entry.operation='apply_content' THEN
  wanted:=CASE WHEN op='undo_canvas' THEN entry.before_state ELSE entry.after_state END;
  FOR obj IN SELECT * FROM jsonb_array_elements(wanted->'objects') LOOP
   SELECT body INTO saved_body FROM public.kb_object_versions WHERE object_id=(obj->>'id')::uuid AND revision=(obj->>'revision')::bigint;
   IF NOT FOUND THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
   SELECT o.current_revision,v.body INTO current_rev,live_body FROM public.kb_objects o JOIN public.kb_object_versions v
    ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=(obj->>'id')::uuid AND o.container_id=entry.container_id AND NOT o.undo_hidden AND o.deleted_at IS NULL;
   IF NOT FOUND THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
   IF saved_body IS DISTINCT FROM live_body THEN
    PERFORM public.kb_apply_command(gen_random_uuid(),'save_document',jsonb_build_object('object_id',obj->>'id','expected_revision',current_rev,'body',saved_body));
   END IF;
  END LOOP;
  SELECT revision INTO rev FROM public.kb_containers WHERE id=entry.container_id;
  UPDATE public.kb_canvas_history_heads SET revision=rev WHERE actor_id=actor AND container_id=entry.container_id;
  result:=result||jsonb_build_object('revision',rev);
  UPDATE public.kb_mutation_receipts SET result=content_restore.result WHERE actor_id=actor AND command_id=cmd;
 END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_canvas_restore(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_canvas_restore(uuid,text,jsonb) TO everydayai,everydayai_runtime;
RESET ROLE;
