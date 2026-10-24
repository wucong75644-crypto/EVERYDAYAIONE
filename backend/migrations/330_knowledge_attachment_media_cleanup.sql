-- Removing an attachment also removes only its own live canvas media in the same guarded transaction.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.kb_apply_file_command(p_command uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 oid uuid:=(p_payload->>'object_id')::uuid; bid uuid; vid uuid; cid uuid; next_rev bigint;
 prior public.kb_mutation_receipts%ROWTYPE; binding public.kb_file_bindings%ROWTYPE;
 source public.kb_file_bindings%ROWTYPE; version public.kb_file_versions%ROWTYPE;
 result jsonb; registration jsonb; request jsonb:=jsonb_build_object('operation',p_operation,'payload',p_payload-'extracted_text'-'text_status');
 asset_url text; container_rev bigint;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(actor::text||p_command::text,0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command;
 IF FOUND THEN
  IF prior.request_payload<>request OR prior.org_id IS DISTINCT FROM org THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='22023'; END IF;
  IF NOT public.kb_can(prior.container_id,'read') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 SELECT container_id INTO cid FROM public.kb_objects WHERE id=oid AND deleted_at IS NULL;
 IF NOT FOUND OR NOT public.kb_can(cid,'read') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.kb_containers WHERE id=cid FOR UPDATE;
 IF NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_payload->>'binding_id' IS NOT NULL THEN
  bid:=(p_payload->>'binding_id')::uuid;
  SELECT * INTO binding FROM public.kb_file_bindings WHERE id=bid AND object_id=oid AND removed_at IS NULL FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='42501'; END IF;
  IF binding.current_revision IS DISTINCT FROM (p_payload->>'expected_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
  next_rev:=binding.current_revision+1;
 ELSE
  bid:=gen_random_uuid(); next_rev:=1;
  IF (SELECT count(*) FROM public.kb_file_bindings WHERE object_id=oid AND removed_at IS NULL)>=100 THEN
   RAISE EXCEPTION 'KB_FILE_CAPACITY' USING ERRCODE='22023';
  END IF;
 END IF;
 IF p_operation='file_upload' THEN
  vid:=(p_payload->>'version_id')::uuid;
  IF COALESCE(p_payload->>'name','')='' OR length(p_payload->>'name')>180
    OR p_payload->>'name' ~ '[[:cntrl:]/\\]' OR (p_payload->>'size')::bigint NOT BETWEEN 1 AND 20971520
    OR COALESCE(p_payload->>'sha256','') !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'KB_INVALID_FILE' USING ERRCODE='22023'; END IF;
  asset_url:='/api/knowledge/objects/'||oid||'/files/'||bid||'/versions/'||next_rev||'/content';
  registration:=public.register_user_asset(org,'user',actor::text,'workspace','knowledge-private/'||vid,
   'file',asset_url,NULL,asset_url,NULL,p_payload->>'name',p_payload->>'mime_type',(p_payload->>'size')::bigint,
   p_payload->>'sha256',jsonb_build_object('storage_class','knowledge_private','version_id',vid),
   'knowledge:'||vid,actor,'upload','web_upload','attachment',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'{}'::jsonb,NULL);
  INSERT INTO public.kb_file_versions(id,asset_id,org_id,storage_owner,name,mime_type,size,sha256,extracted_text,text_status)
   VALUES(vid,(registration->'asset'->>'id')::uuid,org,actor,p_payload->>'name',p_payload->>'mime_type',
    (p_payload->>'size')::bigint,p_payload->>'sha256',COALESCE(p_payload->>'extracted_text',''),p_payload->>'text_status');
 ELSIF p_operation='file_reference' THEN
  SELECT * INTO source FROM public.kb_file_bindings WHERE id=(p_payload->>'source_binding_id')::uuid
   AND object_id=(p_payload->>'source_object_id')::uuid AND removed_at IS NULL;
  IF NOT FOUND OR NOT public.kb_can(source.container_id,'read') OR source.org_id IS DISTINCT FROM org
    OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=source.object_id AND deleted_at IS NULL) THEN
   RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='42501'; END IF;
  SELECT version_id INTO vid FROM public.kb_file_binding_versions WHERE binding_id=source.id AND revision=(p_payload->>'source_revision')::bigint;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='42501'; END IF;
 ELSIF p_operation='file_remove' AND binding.id IS NOT NULL THEN
  UPDATE public.kb_file_bindings SET removed_at=now() WHERE id=bid;
  UPDATE public.kb_canvas_nodes SET
   canvas_data=canvas_data
    -(CASE WHEN canvas_data->'image'->>'binding_id'=bid::text THEN ARRAY['image'] ELSE ARRAY[]::text[] END)
    -(CASE WHEN canvas_data->'video'->>'binding_id'=bid::text THEN ARRAY['video'] ELSE ARRAY[]::text[] END),
   revision=revision+1
   WHERE object_id=oid AND (canvas_data->'image'->>'binding_id'=bid::text OR canvas_data->'video'->>'binding_id'=bid::text);

 ELSE RAISE EXCEPTION 'KB_INVALID_FILE' USING ERRCODE='22023';
 END IF;
 IF p_operation<>'file_remove' THEN
  SELECT * INTO version FROM public.kb_file_versions WHERE id=vid;
  IF binding.id IS NULL THEN
   INSERT INTO public.kb_file_bindings(id,object_id,container_id,org_id,title,current_revision,created_by)
    VALUES(bid,oid,cid,org,version.name,next_rev,actor);
  ELSE UPDATE public.kb_file_bindings SET current_revision=next_rev WHERE id=bid; END IF;
  INSERT INTO public.kb_file_binding_versions(binding_id,revision,version_id,container_id,org_id) VALUES(bid,next_rev,vid,cid,org);
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO container_rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision)
  VALUES(gen_random_uuid(),org,cid,actor,p_command,p_operation,container_rev);
 result:=jsonb_build_object('binding_id',bid,'revision',next_rev,'status','applied');
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
  VALUES(actor,p_command,org,cid,request,result);
 RETURN result;
END $$;

-- Retain bytes and canonical assets for every binding version, even after removal.
