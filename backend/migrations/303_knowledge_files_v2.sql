-- V2 private immutable files. Additive; never changes an existing Workspace file.
SET LOCAL ROLE everydayai;
GRANT REFERENCES(id) ON public.user_assets TO everydayai_owner;
GRANT EXECUTE ON FUNCTION public.register_user_asset(uuid,text,text,text,text,text,text,text,text,text,text,text,bigint,text,jsonb,text,uuid,text,text,text,uuid,uuid,uuid,uuid,uuid,integer,text,text,jsonb,timestamptz) TO everydayai_owner;
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.kb_file_versions (
 id uuid PRIMARY KEY, asset_id uuid NOT NULL REFERENCES public.user_assets(id),
 org_id uuid REFERENCES public.organizations(id), storage_owner uuid NOT NULL REFERENCES public.users(id),
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 180), mime_type text NOT NULL,
 size bigint NOT NULL CHECK(size BETWEEN 1 AND 20971520),
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 extracted_text text NOT NULL DEFAULT '' CHECK(length(extracted_text)<=200000),
 text_status text NOT NULL CHECK(text_status IN ('ready','unsupported','failed','truncated')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.kb_file_bindings (
 id uuid PRIMARY KEY, object_id uuid NOT NULL REFERENCES public.kb_objects(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), org_id uuid REFERENCES public.organizations(id),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 180), current_revision bigint NOT NULL CHECK(current_revision>0),
 created_by uuid NOT NULL REFERENCES public.users(id), created_at timestamptz NOT NULL DEFAULT now(), removed_at timestamptz
);
CREATE INDEX kb_files_object ON public.kb_file_bindings(object_id,id);
CREATE TABLE public.kb_file_binding_versions (
 binding_id uuid NOT NULL REFERENCES public.kb_file_bindings(id), revision bigint NOT NULL CHECK(revision>0),
 version_id uuid NOT NULL REFERENCES public.kb_file_versions(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), org_id uuid REFERENCES public.organizations(id),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(binding_id,revision)
);
CREATE INDEX kb_file_version_refs ON public.kb_file_binding_versions(version_id);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['kb_file_versions','kb_file_bindings','kb_file_binding_versions'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY kb_internal ON public.%I TO everydayai_owner USING(true) WITH CHECK(true)',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,everydayai,everydayai_runtime',t);
 END LOOP;
END $$;
GRANT SELECT ON public.kb_file_bindings,public.kb_file_binding_versions TO everydayai,everydayai_runtime;
CREATE POLICY kb_file_read ON public.kb_file_bindings FOR SELECT TO everydayai,everydayai_runtime
 USING(removed_at IS NULL AND public.kb_can(container_id,'read') AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=object_id AND o.deleted_at IS NULL));
CREATE POLICY kb_file_read ON public.kb_file_binding_versions FOR SELECT TO everydayai,everydayai_runtime
 USING(public.kb_can(container_id,'read') AND EXISTS(SELECT 1 FROM public.kb_file_bindings b WHERE b.id=binding_id));

CREATE FUNCTION public.kb_file_version(p_object uuid,p_binding uuid,p_revision bigint DEFAULT NULL) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('binding_id',b.id,'revision',bv.revision,'version_id',v.id,'asset_id',v.asset_id,
  'name',v.name,'mime_type',v.mime_type,'size',v.size,'sha256',v.sha256,'text_status',v.text_status,
  'created_at',bv.created_at,'storage_owner',v.storage_owner,'storage_org',v.org_id)
 FROM public.kb_file_bindings b JOIN public.kb_objects o ON o.id=b.object_id
 JOIN public.kb_file_binding_versions bv ON bv.binding_id=b.id AND bv.revision=COALESCE(p_revision,b.current_revision)
 JOIN public.kb_file_versions v ON v.id=bv.version_id
 WHERE b.id=p_binding AND b.object_id=p_object AND b.removed_at IS NULL AND o.deleted_at IS NULL
 AND public.kb_can(b.container_id,'read');
$$;

CREATE FUNCTION public.kb_apply_file_command(p_command uuid,p_operation text,p_payload jsonb) RETURNS jsonb
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
CREATE FUNCTION public.kb_protect_file_asset() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.kb_file_versions WHERE asset_id=OLD.id) THEN
  IF TG_OP='DELETE' OR NEW.status IS DISTINCT FROM OLD.status OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
    OR NEW.storage_provider IS DISTINCT FROM OLD.storage_provider OR NEW.storage_owner_key IS DISTINCT FROM OLD.storage_owner_key
    OR NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 THEN
   RAISE EXCEPTION 'KB_ASSET_REFERENCED' USING ERRCODE='23503';
  END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
REVOKE ALL ON FUNCTION public.kb_file_version(uuid,uuid,bigint),public.kb_apply_file_command(uuid,text,jsonb),public.kb_protect_file_asset() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_file_version(uuid,uuid,bigint),public.kb_apply_file_command(uuid,text,jsonb) TO everydayai,everydayai_runtime;
GRANT EXECUTE ON FUNCTION public.kb_protect_file_asset() TO everydayai;
RESET ROLE;
SET LOCAL ROLE everydayai;
CREATE TRIGGER kb_keep_referenced_asset BEFORE DELETE OR UPDATE ON public.user_assets
 FOR EACH ROW EXECUTE FUNCTION public.kb_protect_file_asset();
REVOKE EXECUTE ON FUNCTION public.kb_protect_file_asset() FROM everydayai;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_search_file(p_object uuid,p_query text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT v.name||' · '||substring(v.extracted_text FROM greatest(1,strpos(lower(v.extracted_text),lower(p_query))-60) FOR 220)
 FROM public.kb_file_bindings b JOIN public.kb_objects o ON o.id=b.object_id
 JOIN public.kb_file_binding_versions bv ON bv.binding_id=b.id AND bv.revision=b.current_revision
 JOIN public.kb_file_versions v ON v.id=bv.version_id
 WHERE b.object_id=p_object AND b.removed_at IS NULL AND o.deleted_at IS NULL AND public.kb_can(b.container_id,'read')
 AND (strpos(lower(b.title),lower(p_query))>0 OR strpos(lower(v.name),lower(p_query))>0 OR strpos(lower(v.extracted_text),lower(p_query))>0)
 ORDER BY b.id LIMIT 1;
$$;
REVOKE ALL ON FUNCTION public.kb_search_file(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_search_file(uuid,text) TO everydayai,everydayai_runtime;
RESET ROLE;
