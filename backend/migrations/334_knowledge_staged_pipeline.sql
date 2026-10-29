-- Local source projection and independent, version-bound attachment tasks.
SET LOCAL ROLE everydayai_owner;
-- Switch with workers stopped; old leases must not publish into the new pipeline.
UPDATE public.kb_derivation_jobs SET dirty=dirty+1,state='queued',token=NULL,lease_until=NULL,attempts=0,retry_at=now(),error_code=NULL;

CREATE FUNCTION public.kb_local_pipeline() RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT true $$;
REVOKE ALL ON FUNCTION public.kb_local_pipeline() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_local_pipeline() TO everydayai,everydayai_runtime;
CREATE OR REPLACE FUNCTION public.kb_derivation_fingerprint(p_object uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 WITH inputs AS (
 SELECT 'object:'||o.id AS k,jsonb_build_array(o.parent_id,o.current_revision,o.title,n.canvas_data->'labels',n.canvas_data->'note')::text AS v
 FROM public.kb_objects o JOIN public.kb_canvas_nodes n ON n.object_id=o.id WHERE o.id=p_object AND o.deleted_at IS NULL AND NOT o.undo_hidden
 UNION ALL SELECT 'binding:'||b.id,jsonb_build_array(b.current_revision,bv.version_id)::text FROM public.kb_file_bindings b
 JOIN public.kb_file_binding_versions bv ON bv.binding_id=b.id AND bv.revision=b.current_revision WHERE b.object_id=p_object AND b.removed_at IS NULL
 UNION ALL SELECT 'override:'||x.key,jsonb_build_array(x.revision,x.value)::text FROM public.kb_knowledge_overrides x WHERE x.object_id=p_object
 ) SELECT md5(COALESCE(string_agg(k||':'||v,E'\n' ORDER BY k),'')) FROM inputs
 WHERE public.kb_can((SELECT container_id FROM public.kb_objects WHERE id=p_object),'read');
$$;
CREATE OR REPLACE FUNCTION public.kb_enqueue_derivation(p_object uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF actor IS NULL THEN RETURN; END IF;
 INSERT INTO public.kb_derivation_jobs(object_id,container_id,org_id,actor_id)
 SELECT o.id,o.container_id,c.org_id,actor FROM public.kb_objects o JOIN public.kb_containers c ON c.id=o.container_id
 WHERE o.id=p_object AND NOT o.undo_hidden AND o.deleted_at IS NULL AND NOT c.undo_hidden AND c.deleted_at IS NULL AND public.kb_can(c.id,'write')
 ON CONFLICT(object_id) DO UPDATE SET dirty=kb_derivation_jobs.dirty+1,actor_id=excluded.actor_id,state='queued',token=NULL,lease_until=NULL,attempts=0,retry_at=now(),error_code=NULL,updated_at=now();
END $$;
CREATE OR REPLACE FUNCTION public.kb_publish_derivation(p_object uuid,p_token uuid,p_dirty bigint,p_fingerprint text,p_data jsonb) RETURNS uuid
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
 -- Local projection does not propagate child payloads into parent nodes.

 RETURN gid;
END $$;
CREATE OR REPLACE FUNCTION public.kb_derivation_candidates() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_setting('app.access_kind',true)<>'worker' OR NULLIF(current_setting('app.actor_user_id',true),'') IS NOT NULL THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 RETURN COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT * FROM (
 SELECT object_id,actor_id,org_id,false AS needs_enqueue FROM public.kb_derivation_jobs
 WHERE retry_at<=now() AND (state IN ('queued','failed') OR (state='processing' AND lease_until<now())) AND attempts<5
 UNION ALL
 SELECT o.id,c.owner_id,c.org_id,true FROM public.kb_objects o JOIN public.kb_containers c ON c.id=o.container_id
 WHERE o.deleted_at IS NULL AND NOT o.undo_hidden AND c.deleted_at IS NULL AND NOT c.undo_hidden
 AND (NOT EXISTS(SELECT 1 FROM public.kb_derivation_jobs j WHERE j.object_id=o.id) OR EXISTS(SELECT 1 FROM public.kb_derivation_jobs j JOIN public.kb_derived_heads h ON h.object_id=j.object_id JOIN public.kb_derived_generations g ON g.id=h.generation_id WHERE j.object_id=o.id AND j.state='ready' AND g.config_version NOT LIKE 'local-v1:%'))
 ) y WHERE (COALESCE(current_setting('app.knowledge_users',true),'')='' AND COALESCE(current_setting('app.knowledge_orgs',true),'')='')
 OR y.actor_id::text=ANY(string_to_array(current_setting('app.knowledge_users',true),','))
 OR y.org_id::text=ANY(string_to_array(current_setting('app.knowledge_orgs',true),','))
 LIMIT 32) x),'[]'::jsonb);
END $$;
CREATE TABLE public.kb_file_parse_jobs (
 object_id uuid NOT NULL REFERENCES public.kb_objects(id), binding_id uuid NOT NULL REFERENCES public.kb_file_bindings(id),
 revision bigint NOT NULL, config text NOT NULL CHECK(length(config)<=100), container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 actor_id uuid NOT NULL REFERENCES public.users(id),org_id uuid,state text NOT NULL DEFAULT 'queued', attempts integer NOT NULL DEFAULT 0,
 token uuid,lease_until timestamptz,retry_at timestamptz NOT NULL DEFAULT now(),error_code text, PRIMARY KEY(binding_id,revision,config)
);
ALTER TABLE public.kb_file_parse_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_file_parse_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY kb_owner ON public.kb_file_parse_jobs TO everydayai_owner USING(true) WITH CHECK(true);
REVOKE ALL ON public.kb_file_parse_jobs FROM PUBLIC,everydayai_runtime,everydayai;
CREATE FUNCTION public.kb_file_enqueue(p_object uuid,p_binding uuid,p_revision bigint,p_config text,p_retry boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v jsonb;cid uuid;
BEGIN
 v:=public.kb_file_version(p_object,p_binding,p_revision);
 SELECT container_id INTO cid FROM public.kb_objects WHERE id=p_object AND deleted_at IS NULL AND NOT undo_hidden;
 IF v IS NULL OR cid IS NULL OR NOT public.kb_can(cid,'write') OR length(p_config)>100
 OR NOT EXISTS(SELECT 1 FROM public.kb_file_bindings WHERE id=p_binding AND current_revision=p_revision AND removed_at IS NULL) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 INSERT INTO public.kb_file_parse_jobs(object_id,binding_id,revision,config,container_id,actor_id,org_id)
 VALUES(p_object,p_binding,p_revision,p_config,cid,NULLIF(current_setting('app.actor_user_id',true),'')::uuid,NULLIF(current_setting('app.org_id',true),'')::uuid)
 ON CONFLICT(binding_id,revision,config) DO UPDATE SET state='queued',attempts=0,retry_at=now(),token=NULL,lease_until=NULL,error_code=NULL,actor_id=excluded.actor_id
 WHERE p_retry AND kb_file_parse_jobs.state IN ('failed','blocked','blocked_permission');
END $$;
CREATE FUNCTION public.kb_file_state(p_object uuid,p_binding uuid,p_revision bigint,p_config text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('state',j.state,'error_code',j.error_code) FROM public.kb_file_parse_jobs j
 WHERE j.object_id=p_object AND j.binding_id=p_binding AND j.revision=p_revision AND j.config=p_config
 AND public.kb_file_version(p_object,p_binding,p_revision) IS NOT NULL;
$$;
CREATE FUNCTION public.kb_file_claim(p_object uuid,p_binding uuid,p_revision bigint,p_config text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE j public.kb_file_parse_jobs%ROWTYPE;t uuid:=gen_random_uuid();
BEGIN
 SELECT * INTO j FROM public.kb_file_parse_jobs WHERE binding_id=p_binding AND revision=p_revision AND config=p_config FOR UPDATE;
 IF NOT FOUND OR j.object_id<>p_object OR j.actor_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR j.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN RETURN NULL; END IF;
 IF NOT public.kb_can(j.container_id,'write') OR public.kb_file_version(p_object,p_binding,p_revision) IS NULL
 OR NOT EXISTS(SELECT 1 FROM public.kb_file_bindings WHERE id=p_binding AND current_revision=p_revision AND removed_at IS NULL) THEN
 UPDATE public.kb_file_parse_jobs SET state='blocked_permission',token=NULL WHERE binding_id=p_binding AND revision=p_revision AND config=p_config; RETURN NULL; END IF;
 IF j.attempts>=5 OR j.retry_at>now() OR (j.state NOT IN ('queued','failed') AND NOT(j.state='processing' AND j.lease_until<now())) THEN RETURN NULL; END IF;
 UPDATE public.kb_file_parse_jobs SET state='processing',token=t,lease_until=now()+interval '5 minutes',attempts=attempts+1 WHERE binding_id=p_binding AND revision=p_revision AND config=p_config;
 RETURN t;
END $$;
CREATE FUNCTION public.kb_file_finish(p_object uuid,p_binding uuid,p_revision bigint,p_config text,p_token uuid,p_error text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE j public.kb_file_parse_jobs%ROWTYPE;
BEGIN
 SELECT * INTO j FROM public.kb_file_parse_jobs WHERE binding_id=p_binding AND revision=p_revision AND config=p_config FOR UPDATE;
 IF NOT FOUND OR j.object_id<>p_object OR j.actor_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR j.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid OR NOT public.kb_can(j.container_id,'write') THEN RETURN false; END IF;
 IF j.state<>'processing' OR j.token IS DISTINCT FROM p_token OR j.lease_until<now() OR public.kb_file_version(p_object,p_binding,p_revision) IS NULL
 OR NOT EXISTS(SELECT 1 FROM public.kb_file_bindings WHERE id=p_binding AND current_revision=p_revision AND removed_at IS NULL) THEN RETURN false; END IF;
 IF p_error IS NULL AND public.kb_media_result(p_object,p_binding,p_revision,p_config) IS NULL THEN RETURN false; END IF;
 UPDATE public.kb_file_parse_jobs SET state=CASE WHEN p_error IS NULL THEN 'ready' WHEN p_error='MODEL_NOT_CONFIGURED' OR attempts>=5 THEN 'blocked' ELSE 'failed' END,
 token=NULL,lease_until=NULL,error_code=left(p_error,80),retry_at=now()+interval '30 seconds' WHERE binding_id=p_binding AND revision=p_revision AND config=p_config;
 IF p_error IS NULL THEN PERFORM public.kb_enqueue_derivation(p_object); END IF;
 RETURN true;
END $$;
CREATE FUNCTION public.kb_file_candidates(p_config text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_setting('app.access_kind',true)<>'worker' OR NULLIF(current_setting('app.actor_user_id',true),'') IS NOT NULL THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 RETURN COALESCE((SELECT jsonb_agg(to_jsonb(v)) FROM (
 SELECT b.object_id,b.id AS binding_id,b.current_revision AS revision,COALESCE(j.actor_id,c.owner_id) AS actor_id,c.org_id,'file'::text AS phase,
 CASE WHEN f.mime_type LIKE 'image/%' OR f.mime_type IN ('video/mp4','application/pdf') THEN p_config ELSE 'native-v1' END AS config,j.binding_id IS NULL AS needs_enqueue
 FROM public.kb_file_bindings b JOIN public.kb_objects o ON o.id=b.object_id JOIN public.kb_containers c ON c.id=o.container_id
 JOIN public.kb_file_binding_versions bv ON bv.binding_id=b.id AND bv.revision=b.current_revision JOIN public.kb_file_versions f ON f.id=bv.version_id
 LEFT JOIN public.kb_file_parse_jobs j ON j.binding_id=b.id AND j.revision=b.current_revision AND j.config=CASE WHEN f.mime_type LIKE 'image/%' OR f.mime_type IN ('video/mp4','application/pdf') THEN p_config ELSE 'native-v1' END
 WHERE b.removed_at IS NULL AND o.deleted_at IS NULL AND NOT o.undo_hidden AND c.deleted_at IS NULL AND NOT c.undo_hidden
 AND f.name!~* '\.(xmind|zip|pptx)$' AND f.text_status<>'ready'
 AND (j.binding_id IS NULL OR j.retry_at<=now() AND j.attempts<5 AND (j.state IN ('queued','failed') OR j.state='processing' AND j.lease_until<now()))
 AND ((COALESCE(current_setting('app.knowledge_users',true),'')='' AND COALESCE(current_setting('app.knowledge_orgs',true),'')='')
 OR c.owner_id::text=ANY(string_to_array(current_setting('app.knowledge_users',true),',')) OR c.org_id::text=ANY(string_to_array(current_setting('app.knowledge_orgs',true),',')))
 ORDER BY b.id LIMIT 32) v),'[]'::jsonb);
END $$;
DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.kb_file_enqueue(uuid,uuid,bigint,text,boolean)'::regprocedure,'public.kb_file_state(uuid,uuid,bigint,text)'::regprocedure,'public.kb_file_claim(uuid,uuid,bigint,text)'::regprocedure,'public.kb_file_finish(uuid,uuid,bigint,text,uuid,text)'::regprocedure,'public.kb_file_candidates(text)'::regprocedure] LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f);EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO everydayai_runtime,everydayai',f);
 END LOOP;
END $$;
CREATE FUNCTION public.kb_local_corrections(p_object uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('key',x.key,'text',x.value->>'text','source_generation',x.source_generation)),'[]'::jsonb)
 FROM public.kb_knowledge_overrides x JOIN public.kb_objects o ON o.id=x.object_id
 WHERE x.object_id=p_object AND public.kb_can(o.container_id,'read') AND o.deleted_at IS NULL AND NOT o.undo_hidden
 AND x.key LIKE 'section:%' AND x.value ? 'text' AND NOT COALESCE((x.value->>'hidden')::boolean,false)
 AND (x.source_generation IS NULL OR public.kb_derived_authorized(x.source_generation))
 AND NOT EXISTS(SELECT 1 FROM public.kb_derived_generations g CROSS JOIN LATERAL jsonb_array_elements(g.dependencies) d
   WHERE g.id=x.source_generation AND
   ((d ? 'binding_id' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b WHERE b.id=(d->>'binding_id')::uuid AND b.removed_at IS NULL AND b.current_revision=(d->>'revision')::bigint))
    OR (NOT d ? 'binding_id' AND NOT EXISTS(SELECT 1 FROM public.kb_objects src WHERE src.id=(d->>'object_id')::uuid AND src.current_revision=(d->>'revision')::bigint AND src.deleted_at IS NULL AND NOT src.undo_hidden))));
$$;
REVOKE ALL ON FUNCTION public.kb_local_corrections(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_local_corrections(uuid) TO everydayai_runtime,everydayai;
RESET ROLE;
