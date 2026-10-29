-- One private project, one transaction, one reversible history entry.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_import_xmind(cmd uuid,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 prior public.kb_mutation_receipts%ROWTYPE; cid uuid:=gen_random_uuid(); oid uuid; pid uuid;
 ids uuid[]:='{}'; item jsonb; f jsonb; data jsonb; binding jsonb; bindings jsonb:='{}';
 idx integer:=0; parent_idx integer; rev bigint; result jsonb; after_state jsonb;
 before_state jsonb:='{"container_hidden":true,"objects":[],"relations":[]}';
 folded jsonb:='[]'; request jsonb:=jsonb_build_object('operation','import_xmind','payload',p);
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF org IS NULL THEN RAISE EXCEPTION 'KB_ORG_REQUIRED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||org::text,0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=cmd;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>request THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'manage') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed','hidden',(SELECT undo_hidden FROM public.kb_containers WHERE id=prior.container_id));
 END IF;
 IF jsonb_typeof(p->'topics') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'topics') NOT BETWEEN 1 AND 1000
  OR jsonb_typeof(p->'files') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'files')>2001 OR pg_column_size(p)>16000000
 THEN RAISE EXCEPTION 'KB_INVALID_FILE' USING ERRCODE='22023'; END IF;
 INSERT INTO public.kb_containers(id,org_id,kind,owner_id,title) VALUES(cid,org,'project',actor,p->>'title');
 INSERT INTO public.kb_projects(container_id,org_id,owner_id) VALUES(cid,org,actor);
 INSERT INTO public.kb_container_members(container_id,org_id,user_id,role) VALUES(cid,org,actor,'manager');
 FOR item IN SELECT value FROM jsonb_array_elements(p->'topics') LOOP
  parent_idx:=(item->>'parent_index')::integer;
  IF parent_idx IS NULL OR parent_idx < -1 OR parent_idx >= idx THEN RAISE EXCEPTION 'KB_INVALID_FILE' USING ERRCODE='22023'; END IF;
  pid:=CASE WHEN parent_idx=-1 THEN NULL ELSE ids[parent_idx+1] END; oid:=gen_random_uuid();
  INSERT INTO public.kb_objects(id,org_id,container_id,parent_id,kind,title,created_by,sort_order)
   VALUES(oid,org,cid,pid,'topic',item->>'title',actor,(item->>'sort_order')::bigint);
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
   VALUES(oid,cid,org,1,item->>'title',item->>'body',pid,actor);
  INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y)
   VALUES(org,cid,oid,(item->>'x')::float8,(item->>'y')::float8);
  ids:=array_append(ids,oid); idx:=idx+1;
  IF (item->>'folded')::boolean THEN folded:=folded||to_jsonb(oid); END IF;
 END LOOP;
 FOR f IN SELECT value FROM jsonb_array_elements(p->'files') LOOP
  idx:=(f->>'topic_index')::integer;
  IF idx IS NULL OR idx<0 OR idx>=cardinality(ids) THEN RAISE EXCEPTION 'KB_INVALID_FILE' USING ERRCODE='22023'; END IF;
  binding:=public.kb_apply_file_command((f->>'command_id')::uuid,'file_upload',(f->'metadata')||jsonb_build_object('object_id',ids[idx+1],'version_id',f->>'version_id'));
  bindings:=bindings||jsonb_build_object(f->>'key',binding->'binding_id');
 END LOOP;
 idx:=0;
 FOR item IN SELECT value FROM jsonb_array_elements(p->'topics') LOOP
  data:='{}';
  IF item ? 'link' THEN data:=data||jsonb_build_object('link',item->'link'); END IF;
  IF item ? 'labels' THEN data:=data||jsonb_build_object('labels',item->'labels'); END IF;
  IF item ? 'image' THEN
   IF NOT bindings ? (idx::text||':image') THEN RAISE EXCEPTION 'KB_INVALID_FILE' USING ERRCODE='22023'; END IF;
   data:=data||jsonb_build_object('image',jsonb_build_object('binding_id',bindings->(idx::text||':image'),'revision',1,'width',220,'placement','top','alt','','aspect',COALESCE((item->>'image_aspect')::numeric,1)));
  END IF;
  IF item ? 'summary' THEN
   IF jsonb_typeof(item->'summary') IS DISTINCT FROM 'array' OR jsonb_array_length(item->'summary')=0 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(item->'summary') m WHERE m::integer<0 OR m::integer>=cardinality(ids) OR m::integer=idx) THEN RAISE EXCEPTION 'KB_INVALID_FILE' USING ERRCODE='22023'; END IF;
   data:=data||jsonb_build_object('summary',jsonb_build_object('members',(SELECT jsonb_agg(ids[m::integer+1]) FROM jsonb_array_elements_text(item->'summary') m),'title',item->>'title'));
  END IF;
  PERFORM public.kb_validate_canvas_data(cid,data);
  UPDATE public.kb_canvas_nodes SET canvas_data=data WHERE object_id=ids[idx+1];
  idx:=idx+1;
 END LOOP;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 after_state:=public.kb_canvas_state(cid);
 UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND org_id=org AND state='undone';
 INSERT INTO public.kb_canvas_history(actor_id,org_id,command_id,container_id,operation,before_state,after_state,state)
  VALUES(actor,org,cmd,cid,'import_xmind',public.kb_canvas_patch(before_state,after_state),public.kb_canvas_patch(after_state,before_state),'applied');
 INSERT INTO public.kb_canvas_history_heads(actor_id,container_id,revision) VALUES(actor,cid,rev);
 DELETE FROM public.kb_canvas_history WHERE actor_id=actor AND org_id=org AND (state='discarded' OR sequence NOT IN (SELECT sequence FROM public.kb_canvas_history WHERE actor_id=actor AND org_id=org ORDER BY sequence DESC LIMIT 100));
 result:=jsonb_build_object('status','applied','container_id',cid,'object_id',ids[1],'revision',rev,'folded_ids',folded,'report',p->'report');
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,cmd,org,cid,request,result);
 INSERT INTO public.kb_events(org_id,container_id,actor_id,command_id,operation,revision) VALUES(org,cid,actor,cmd,'import_xmind',rev);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_import_xmind(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_import_xmind(uuid,jsonb) TO everydayai,everydayai_runtime;
RESET ROLE;
