-- Derived knowledge never writes manual body, canvas labels, or canvas history.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.kb_derivation_jobs (
 object_id uuid PRIMARY KEY REFERENCES public.kb_objects(id), container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 org_id uuid, actor_id uuid NOT NULL REFERENCES public.users(id), dirty bigint NOT NULL DEFAULT 1,
 state text NOT NULL DEFAULT 'queued', token uuid, lease_until timestamptz, attempts integer NOT NULL DEFAULT 0,
 retry_at timestamptz NOT NULL DEFAULT now(), error_code text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.kb_derived_generations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), object_id uuid NOT NULL REFERENCES public.kb_objects(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), org_id uuid, fingerprint text NOT NULL,
 sections jsonb NOT NULL, tags jsonb NOT NULL, chunks jsonb NOT NULL, dependencies jsonb NOT NULL,
 coverage text NOT NULL CHECK(coverage IN ('complete','partial')), config_version text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.kb_derived_heads (
 object_id uuid PRIMARY KEY REFERENCES public.kb_objects(id), container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 org_id uuid, generation_id uuid NOT NULL REFERENCES public.kb_derived_generations(id), revision bigint NOT NULL DEFAULT 1
);
CREATE TABLE public.kb_knowledge_overrides (
 object_id uuid NOT NULL REFERENCES public.kb_objects(id), container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 org_id uuid, key text NOT NULL, value jsonb NOT NULL, revision bigint NOT NULL DEFAULT 1, source_generation uuid REFERENCES public.kb_derived_generations(id),
 actor_id uuid NOT NULL REFERENCES public.users(id), PRIMARY KEY(object_id,key)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['kb_derivation_jobs','kb_derived_generations','kb_derived_heads','kb_knowledge_overrides'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY kb_owner ON public.%I TO everydayai_owner USING(true) WITH CHECK(true)',t);
  EXECUTE format('CREATE POLICY kb_derived_read ON public.%I FOR SELECT TO everydayai_runtime,everydayai USING(public.kb_can(container_id,''read''))',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,everydayai_runtime,everydayai',t);
 END LOOP;
END $$;
-- All derived payloads are returned through validated functions, not direct SELECT.
CREATE INDEX kb_derivation_pending ON public.kb_derivation_jobs(retry_at) WHERE state IN ('queued','processing','failed');
CREATE INDEX kb_derived_object ON public.kb_derived_generations(object_id,created_at);

CREATE FUNCTION public.kb_derivation_fingerprint(p_object uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 WITH RECURSIVE branch AS (
  SELECT o.*,ARRAY[o.id] AS visited FROM public.kb_objects o WHERE o.id=p_object AND o.deleted_at IS NULL AND NOT o.undo_hidden
  UNION ALL SELECT o.*,b.visited||o.id FROM public.kb_objects o JOIN branch b ON o.parent_id=b.id
   WHERE o.container_id=b.container_id AND o.deleted_at IS NULL AND NOT o.undo_hidden AND NOT o.id=ANY(b.visited)
 ), inputs AS (
 SELECT 'object:'||b.id AS k,jsonb_build_array(b.parent_id,b.current_revision,b.title,n.canvas_data->'labels',n.canvas_data->'note')::text AS v FROM branch b
 JOIN public.kb_canvas_nodes n ON n.object_id=b.id
 UNION ALL SELECT 'binding:'||f.id,jsonb_build_array(f.object_id,f.current_revision,v.version_id)::text FROM public.kb_file_bindings f JOIN branch b ON b.id=f.object_id
 JOIN public.kb_file_binding_versions v ON v.binding_id=f.id AND v.revision=f.current_revision WHERE f.removed_at IS NULL
 UNION ALL SELECT 'override:'||x.object_id||':'||x.key,jsonb_build_array(x.revision,x.value)::text FROM public.kb_knowledge_overrides x JOIN branch b ON b.id=x.object_id
 ) SELECT md5(COALESCE(string_agg(k||':'||v,E'\n' ORDER BY k),'')) FROM inputs
 WHERE public.kb_can((SELECT container_id FROM public.kb_objects WHERE id=p_object),'read');
$$;

CREATE FUNCTION public.kb_enqueue_derivation(p_object uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE row record; actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF actor IS NULL THEN RETURN; END IF;
 FOR row IN WITH RECURSIVE ancestors AS (
  SELECT id,parent_id,container_id,ARRAY[id] AS visited FROM public.kb_objects WHERE id=p_object
  UNION ALL SELECT o.id,o.parent_id,o.container_id,a.visited||o.id FROM public.kb_objects o JOIN ancestors a ON o.id=a.parent_id
   WHERE o.container_id=a.container_id AND NOT o.id=ANY(a.visited)
 ) SELECT a.id,a.container_id,c.org_id FROM ancestors a JOIN public.kb_containers c ON c.id=a.container_id
 WHERE public.kb_can(c.id,'write') AND c.deleted_at IS NULL
 LOOP
  INSERT INTO public.kb_derivation_jobs(object_id,container_id,org_id,actor_id) VALUES(row.id,row.container_id,row.org_id,actor)
  ON CONFLICT(object_id) DO UPDATE SET dirty=kb_derivation_jobs.dirty+1,actor_id=excluded.actor_id,state='queued',token=NULL,lease_until=NULL,attempts=0,retry_at=now(),error_code=NULL,updated_at=now();
 END LOOP;
END $$;
CREATE INDEX kb_derived_dependencies ON public.kb_derived_generations USING gin(dependencies jsonb_path_ops);
CREATE FUNCTION public.kb_enqueue_dependants(p_source uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM public.kb_enqueue_derivation(h.object_id) FROM public.kb_derived_heads h
 JOIN public.kb_derived_generations g ON g.id=h.generation_id
 WHERE h.object_id<>p_source AND public.kb_can(h.container_id,'write')
 AND g.dependencies @> jsonb_build_array(jsonb_build_object('object_id',p_source::text));
END $$;
REVOKE ALL ON FUNCTION public.kb_enqueue_dependants(uuid) FROM PUBLIC;

CREATE FUNCTION public.kb_derivation_source_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_TABLE_NAME='kb_objects' THEN
  IF TG_OP='UPDATE' AND OLD.parent_id IS DISTINCT FROM NEW.parent_id AND OLD.parent_id IS NOT NULL THEN PERFORM public.kb_enqueue_derivation(OLD.parent_id); END IF;
  PERFORM public.kb_enqueue_derivation(NEW.id);
  PERFORM public.kb_enqueue_dependants(NEW.id);
 ELSIF TG_TABLE_NAME='kb_canvas_nodes' THEN
  IF TG_OP='INSERT' OR (OLD.canvas_data->'labels') IS DISTINCT FROM (NEW.canvas_data->'labels') OR (OLD.canvas_data->'note') IS DISTINCT FROM (NEW.canvas_data->'note') THEN PERFORM public.kb_enqueue_derivation(NEW.object_id); PERFORM public.kb_enqueue_dependants(NEW.object_id); END IF;
 ELSIF TG_TABLE_NAME='kb_file_bindings' OR TG_TABLE_NAME='kb_knowledge_overrides' THEN PERFORM public.kb_enqueue_derivation(NEW.object_id); PERFORM public.kb_enqueue_dependants(NEW.object_id);

 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER kb_derive_object AFTER INSERT OR UPDATE OF current_revision,parent_id,deleted_at,undo_hidden ON public.kb_objects FOR EACH ROW EXECUTE FUNCTION public.kb_derivation_source_changed();
CREATE TRIGGER kb_derive_labels AFTER INSERT OR UPDATE OF canvas_data ON public.kb_canvas_nodes FOR EACH ROW EXECUTE FUNCTION public.kb_derivation_source_changed();
CREATE TRIGGER kb_derive_binding AFTER INSERT OR UPDATE ON public.kb_file_bindings FOR EACH ROW EXECUTE FUNCTION public.kb_derivation_source_changed();
CREATE TRIGGER kb_derive_override AFTER INSERT OR UPDATE ON public.kb_knowledge_overrides FOR EACH ROW EXECUTE FUNCTION public.kb_derivation_source_changed();

CREATE FUNCTION public.kb_derivation_candidates() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_setting('app.access_kind',true)<>'worker' OR NULLIF(current_setting('app.actor_user_id',true),'') IS NOT NULL THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 RETURN COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT * FROM (
 SELECT object_id,actor_id,org_id,false AS needs_enqueue FROM public.kb_derivation_jobs
 WHERE retry_at<=now() AND (state IN ('queued','failed') OR (state='processing' AND lease_until<now())) AND attempts<5
 UNION ALL
 SELECT o.id,c.owner_id,c.org_id,true FROM public.kb_objects o JOIN public.kb_containers c ON c.id=o.container_id
 WHERE o.deleted_at IS NULL AND NOT o.undo_hidden AND c.deleted_at IS NULL AND NOT c.undo_hidden
 AND NOT EXISTS(SELECT 1 FROM public.kb_derivation_jobs j WHERE j.object_id=o.id)
 ) y WHERE (COALESCE(current_setting('app.knowledge_users',true),'')='' AND COALESCE(current_setting('app.knowledge_orgs',true),'')='')
 OR y.actor_id::text=ANY(string_to_array(current_setting('app.knowledge_users',true),','))
 OR y.org_id::text=ANY(string_to_array(current_setting('app.knowledge_orgs',true),','))
 LIMIT 32) x),'[]'::jsonb);
END $$;

CREATE FUNCTION public.kb_claim_derivation(p_object uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE job public.kb_derivation_jobs%ROWTYPE; claim uuid:=gen_random_uuid();
BEGIN
 SELECT * INTO job FROM public.kb_derivation_jobs WHERE object_id=p_object FOR UPDATE;
 IF NOT FOUND OR job.actor_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid OR job.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN RETURN NULL; END IF;
 IF NOT public.kb_can(job.container_id,'write') THEN UPDATE public.kb_derivation_jobs SET state='blocked_permission',token=NULL WHERE object_id=p_object; RETURN NULL; END IF;
 IF job.retry_at>now() OR job.attempts>=5 OR (job.state NOT IN ('queued','failed') AND NOT(job.state='processing' AND job.lease_until<now())) THEN RETURN NULL; END IF;
 UPDATE public.kb_derivation_jobs SET state='processing',token=claim,lease_until=now()+interval '5 minutes',attempts=attempts+1 WHERE object_id=p_object;
 RETURN jsonb_build_object('token',claim,'dirty',job.dirty,'fingerprint',public.kb_derivation_fingerprint(p_object));
END $$;

CREATE FUNCTION public.kb_derived_authorized(p_generation uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g public.kb_derived_generations%ROWTYPE; d jsonb; value jsonb;
BEGIN
 SELECT * INTO g FROM public.kb_derived_generations WHERE id=p_generation;
 IF NOT FOUND OR NOT public.kb_can(g.container_id,'read') OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=g.object_id AND deleted_at IS NULL AND NOT undo_hidden) THEN RETURN false; END IF;
 FOR d IN SELECT * FROM jsonb_array_elements(g.dependencies) LOOP
  IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=(d->>'object_id')::uuid AND container_id=g.container_id AND deleted_at IS NULL AND NOT undo_hidden) THEN RETURN false; END IF;
  IF d ? 'binding_id' THEN
   value:=public.kb_file_version((d->>'object_id')::uuid,(d->>'binding_id')::uuid,(d->>'revision')::bigint);
   IF value IS NULL OR value->>'version_id' IS DISTINCT FROM d->>'version_id' THEN RETURN false; END IF;
  END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION public.kb_derived_document(p_object uuid,p_generation uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g public.kb_derived_generations%ROWTYPE; j public.kb_derivation_jobs%ROWTYPE; cid uuid;
BEGIN
 SELECT container_id INTO cid FROM public.kb_objects WHERE id=p_object AND deleted_at IS NULL AND NOT undo_hidden;
 IF cid IS NULL OR NOT public.kb_can(cid,'read') THEN RETURN NULL; END IF;
 SELECT * INTO j FROM public.kb_derivation_jobs WHERE object_id=p_object;
 SELECT v.* INTO g FROM public.kb_derived_generations v WHERE v.object_id=p_object AND v.id=COALESCE(p_generation,(SELECT generation_id FROM public.kb_derived_heads WHERE object_id=p_object));
 IF g.id IS NULL OR NOT public.kb_derived_authorized(g.id) OR (p_generation IS NULL AND g.fingerprint IS DISTINCT FROM public.kb_derivation_fingerprint(p_object)) THEN
  RETURN jsonb_build_object('generation_id',NULL,'sections','[]'::jsonb,'tags','[]'::jsonb,'chunks','[]'::jsonb,'status',COALESCE(j.state,'idle'),'override_sources',COALESCE((SELECT jsonb_object_agg(key,source_generation) FROM public.kb_knowledge_overrides WHERE object_id=p_object AND (source_generation IS NULL OR public.kb_derived_authorized(source_generation))),'{}'::jsonb),
 'overrides',COALESCE((SELECT jsonb_object_agg(key,value) FROM public.kb_knowledge_overrides WHERE object_id=p_object AND (source_generation IS NULL OR public.kb_derived_authorized(source_generation))),'{}'::jsonb),'override_revisions',COALESCE((SELECT jsonb_object_agg(key,revision) FROM public.kb_knowledge_overrides WHERE object_id=p_object AND (source_generation IS NULL OR public.kb_derived_authorized(source_generation))),'{}'::jsonb));
 END IF;
 RETURN jsonb_build_object('generation_id',g.id,'sections',g.sections,'tags',g.tags,'chunks',g.chunks,'dependencies',g.dependencies,'coverage',g.coverage,'status',CASE WHEN g.coverage='partial' THEN 'partial' ELSE 'ready' END,'created_at',g.created_at,'override_revisions',COALESCE((SELECT jsonb_object_agg(key,revision) FROM public.kb_knowledge_overrides WHERE object_id=p_object AND (source_generation IS NULL OR public.kb_derived_authorized(source_generation))),'{}'::jsonb),
 'override_sources',COALESCE((SELECT jsonb_object_agg(key,source_generation) FROM public.kb_knowledge_overrides WHERE object_id=p_object AND (source_generation IS NULL OR public.kb_derived_authorized(source_generation))),'{}'::jsonb),
 'overrides',COALESCE((SELECT jsonb_object_agg(key,value) FROM public.kb_knowledge_overrides WHERE object_id=p_object AND (source_generation IS NULL OR public.kb_derived_authorized(source_generation))),'{}'::jsonb));
END $$;

CREATE FUNCTION public.kb_publish_derivation(p_object uuid,p_token uuid,p_dirty bigint,p_fingerprint text,p_data jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE job public.kb_derivation_jobs%ROWTYPE; gid uuid; dep jsonb;
BEGIN
 SELECT * INTO job FROM public.kb_derivation_jobs WHERE object_id=p_object FOR UPDATE;
 IF NOT FOUND OR job.actor_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid OR job.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid OR NOT public.kb_can(job.container_id,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF job.token IS DISTINCT FROM p_token OR job.dirty<>p_dirty OR job.state<>'processing' OR job.lease_until<now() OR p_fingerprint IS DISTINCT FROM public.kb_derivation_fingerprint(p_object) THEN RETURN NULL; END IF;
 IF pg_column_size(p_data)>1500000 OR jsonb_typeof(p_data->'sections')<>'array' OR jsonb_typeof(p_data->'tags')<>'array' OR jsonb_typeof(p_data->'chunks')<>'array' OR jsonb_typeof(p_data->'dependencies')<>'array' THEN RAISE EXCEPTION 'KB_INVALID_DERIVATION' USING ERRCODE='22023'; END IF;
 FOR dep IN SELECT * FROM jsonb_array_elements(p_data->'dependencies') LOOP
  IF NOT EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=(dep->>'object_id')::uuid AND o.container_id=job.container_id AND o.deleted_at IS NULL AND NOT o.undo_hidden) THEN RAISE EXCEPTION 'KB_INVALID_DERIVATION' USING ERRCODE='22023'; END IF;
 END LOOP;
 INSERT INTO public.kb_derived_generations(object_id,container_id,org_id,fingerprint,sections,tags,chunks,dependencies,coverage,config_version)
 VALUES(p_object,job.container_id,job.org_id,p_fingerprint,p_data->'sections',p_data->'tags',p_data->'chunks',p_data->'dependencies',p_data->>'coverage',p_data->>'config_version') RETURNING id INTO gid;
 IF NOT public.kb_derived_authorized(gid) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 INSERT INTO public.kb_derived_heads(object_id,container_id,org_id,generation_id) VALUES(p_object,job.container_id,job.org_id,gid)
 ON CONFLICT(object_id) DO UPDATE SET generation_id=excluded.generation_id,revision=kb_derived_heads.revision+1;
 UPDATE public.kb_derivation_jobs SET state='ready',token=NULL,lease_until=NULL,error_code=NULL WHERE object_id=p_object;
 -- Propagate the latest child manifest, even when its displayed summary is unchanged.
 PERFORM public.kb_enqueue_derivation(o.parent_id) FROM public.kb_objects o WHERE o.id=p_object AND o.parent_id IS NOT NULL;
 RETURN gid;
END $$;
CREATE FUNCTION public.kb_fail_derivation(p_object uuid,p_token uuid,p_code text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 UPDATE public.kb_derivation_jobs SET state=CASE WHEN attempts>=5 THEN 'blocked' ELSE 'failed' END,error_code=left(p_code,80),token=NULL,lease_until=NULL,retry_at=now()+interval '30 seconds'
 WHERE object_id=p_object AND token=p_token AND actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid;
END $$;
CREATE FUNCTION public.kb_set_knowledge_override(p_object uuid,p_key text,p_value jsonb,p_revision bigint,p_command uuid DEFAULT gen_random_uuid()) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE cid uuid; rev bigint; source_gen uuid; prior public.kb_mutation_receipts%ROWTYPE; request jsonb:=jsonb_build_object('operation','knowledge_override','object_id',p_object,'key',p_key,'value',p_value,'revision',p_revision);
BEGIN
 SELECT container_id INTO cid FROM public.kb_objects WHERE id=p_object AND deleted_at IS NULL AND NOT undo_hidden;
 IF cid IS NULL OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(current_setting('app.actor_user_id',true)||p_command::text,0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND command_id=p_command;
 IF FOUND THEN IF prior.request_payload<>request OR prior.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF; RETURN (prior.result->>'revision')::bigint; END IF;
 IF jsonb_typeof(p_value)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_value) k WHERE k NOT IN ('text','hidden')) OR (p_value ? 'text' AND (jsonb_typeof(p_value->'text')<>'string' OR length(p_value->>'text')>12000)) OR (p_value ? 'hidden' AND jsonb_typeof(p_value->'hidden')<>'boolean') THEN RAISE EXCEPTION 'KB_INVALID_DERIVATION' USING ERRCODE='22023'; END IF;
 IF length(p_key)>160 OR p_key!~'^(section:|tag:)' OR pg_column_size(p_value)>200000 THEN RAISE EXCEPTION 'KB_INVALID_DERIVATION' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-override:'||p_object||p_key,0));
 SELECT revision INTO rev FROM public.kb_knowledge_overrides WHERE object_id=p_object AND key=p_key;
 IF p_key LIKE 'section:%' THEN
  SELECT g.id INTO source_gen FROM public.kb_derived_generations g WHERE g.object_id=p_object AND public.kb_derived_authorized(g.id) AND EXISTS(SELECT 1 FROM jsonb_array_elements(g.sections) s WHERE s->>'id'=p_key) ORDER BY g.created_at DESC LIMIT 1;
  IF source_gen IS NULL THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 END IF;
 IF COALESCE(rev,0)<>p_revision THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 INSERT INTO public.kb_knowledge_overrides(object_id,container_id,org_id,key,value,actor_id,source_generation) VALUES(p_object,cid,NULLIF(current_setting('app.org_id',true),'')::uuid,p_key,p_value,NULLIF(current_setting('app.actor_user_id',true),'')::uuid,source_gen)
 ON CONFLICT(object_id,key) DO UPDATE SET value=excluded.value,revision=kb_knowledge_overrides.revision+1,actor_id=excluded.actor_id,source_generation=excluded.source_generation RETURNING revision INTO rev;
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(NULLIF(current_setting('app.actor_user_id',true),'')::uuid,p_command,NULLIF(current_setting('app.org_id',true),'')::uuid,cid,request,jsonb_build_object('revision',rev));
 RETURN rev;
END $$;
DO $$ DECLARE fn record; BEGIN
 FOR fn IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('kb_derivation_fingerprint','kb_enqueue_derivation','kb_derivation_source_changed','kb_derivation_candidates','kb_claim_derivation','kb_derived_authorized','kb_derived_document','kb_publish_derivation','kb_fail_derivation','kb_set_knowledge_override') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',fn.signature);
  IF fn.signature::text NOT LIKE 'kb_derivation_source_changed%' THEN EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO everydayai_runtime,everydayai',fn.signature); END IF;
 END LOOP;
END $$;

CREATE TABLE public.kb_retrieval_states (
 snapshot_id uuid PRIMARY KEY REFERENCES public.kb_read_snapshots(id), state jsonb NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE public.kb_retrieval_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_retrieval_states FORCE ROW LEVEL SECURITY;
CREATE POLICY kb_query_owner ON public.kb_retrieval_states TO everydayai_owner USING(true) WITH CHECK(true);
CREATE POLICY kb_query_actor ON public.kb_retrieval_states TO everydayai_runtime,everydayai
 USING(EXISTS(SELECT 1 FROM public.kb_read_snapshots s WHERE s.id=snapshot_id AND s.actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND s.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid()))
 WITH CHECK(EXISTS(SELECT 1 FROM public.kb_read_snapshots s WHERE s.id=snapshot_id AND s.actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND s.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid()));
REVOKE ALL ON public.kb_retrieval_states FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON public.kb_retrieval_states TO everydayai_runtime,everydayai;

-- Cache immutable media extraction behind the live binding permission check.
CREATE TABLE public.kb_media_extractions (
 version_id uuid NOT NULL REFERENCES public.kb_file_versions(id) ON DELETE CASCADE, config text NOT NULL,
 result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(version_id,config)
);
ALTER TABLE public.kb_media_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_media_extractions FORCE ROW LEVEL SECURITY;
CREATE POLICY kb_media_owner ON public.kb_media_extractions TO everydayai_owner USING(true) WITH CHECK(true);
REVOKE ALL ON public.kb_media_extractions FROM PUBLIC,everydayai_runtime,everydayai;
CREATE FUNCTION public.kb_media_result(p_object uuid,p_binding uuid,p_revision bigint,p_config text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v jsonb;
BEGIN
 v:=public.kb_file_version(p_object,p_binding,p_revision);
 IF v IS NULL THEN RETURN NULL; END IF;
 RETURN (SELECT result FROM public.kb_media_extractions WHERE version_id=(v->>'version_id')::uuid AND config=p_config);
END $$;
CREATE FUNCTION public.kb_save_media_result(p_object uuid,p_binding uuid,p_revision bigint,p_config text,p_result jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v jsonb;
BEGIN
 IF NOT public.kb_can((SELECT container_id FROM public.kb_objects WHERE id=p_object),'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 v:=public.kb_file_version(p_object,p_binding,p_revision);
 IF v IS NULL THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF length(p_config)>100 OR jsonb_typeof(p_result)<>'object' OR jsonb_typeof(p_result->'text')<>'string' OR length(p_result->>'text')>120000 OR COALESCE(p_result->>'coverage','') NOT IN ('complete','partial') THEN RAISE EXCEPTION 'KB_INVALID_DERIVATION' USING ERRCODE='22023'; END IF;
 INSERT INTO public.kb_media_extractions(version_id,config,result) VALUES((v->>'version_id')::uuid,p_config,p_result) ON CONFLICT DO NOTHING;
END $$;
REVOKE ALL ON FUNCTION public.kb_media_result(uuid,uuid,bigint,text),public.kb_save_media_result(uuid,uuid,bigint,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_media_result(uuid,uuid,bigint,text),public.kb_save_media_result(uuid,uuid,bigint,text,jsonb) TO everydayai_runtime,everydayai;

CREATE FUNCTION public.kb_reuse_derivation(p_object uuid,p_token uuid,p_config text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE job public.kb_derivation_jobs%ROWTYPE; g public.kb_derived_generations%ROWTYPE;
BEGIN
 SELECT * INTO job FROM public.kb_derivation_jobs WHERE object_id=p_object FOR UPDATE;
 IF NOT FOUND OR job.actor_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid OR job.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid OR NOT public.kb_can(job.container_id,'write') OR job.token IS DISTINCT FROM p_token OR job.lease_until<now() THEN RETURN NULL; END IF;
 SELECT v.* INTO g FROM public.kb_derived_generations v JOIN public.kb_derived_heads h ON h.generation_id=v.id WHERE h.object_id=p_object;
 IF g.id IS NULL OR g.coverage<>'complete' OR g.config_version<>p_config OR g.fingerprint IS DISTINCT FROM public.kb_derivation_fingerprint(p_object) OR NOT public.kb_derived_authorized(g.id) THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(g.dependencies) d WHERE d ? 'generation_id' AND d->>'generation_id' IS DISTINCT FROM (SELECT generation_id::text FROM public.kb_derived_heads WHERE object_id=(d->>'object_id')::uuid)) THEN RETURN NULL; END IF;
 UPDATE public.kb_derivation_jobs SET state='ready',token=NULL,lease_until=NULL,error_code=NULL WHERE object_id=p_object;
 RETURN g.id;
END $$;
REVOKE ALL ON FUNCTION public.kb_reuse_derivation(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_reuse_derivation(uuid,uuid,text) TO everydayai_runtime,everydayai;
