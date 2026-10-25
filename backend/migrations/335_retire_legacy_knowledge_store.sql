-- User-authorized physical retirement. Any missing source/target/ACL proof stops
-- the entire transaction. Never CASCADE and never synthesize compatibility views.
SET LOCAL ROLE everydayai;
LOCK TABLE public.knowledge_nodes,public.knowledge_edges IN ACCESS EXCLUSIVE MODE;
SET LOCAL ROLE everydayai_owner;
DO $$
DECLARE run_ids uuid[]; r public.kb_legacy_import_runs%ROWTYPE; total bigint; bad bigint;
 edge_count bigint; edge_digest text; archive public.kb_containers%ROWTYPE;
 index_body text; index_revision bigint; edge record;
BEGIN
 SELECT count(*) INTO total FROM public.knowledge_nodes;
 IF total=0 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_EMPTY_SOURCE_REQUIRES_REVIEW'; END IF;
 SELECT array_agg(DISTINCT i.run_id) INTO run_ids FROM public.knowledge_nodes n
 JOIN public.kb_legacy_import_items i ON i.legacy_id=n.id;
 IF cardinality(run_ids) IS DISTINCT FROM 1 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_RUN_INCOMPLETE'; END IF;
 SELECT * INTO r FROM public.kb_legacy_import_runs WHERE id=run_ids[1];
 IF NOT FOUND OR r.verified_at IS NULL OR r.source_count<>total OR COALESCE((r.state->>'rolled_back')::boolean,false)
 OR length(r.backup_manifest_sha256)<>64 OR length(r.backup_path)=0
 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_NOT_VERIFIED'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users u JOIN public.org_members m ON m.user_id=u.id
 WHERE u.id=r.actor_id AND u.status='active' AND m.org_id=r.org_id AND m.status='active' AND m.role IN ('owner','admin'))
 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_OPERATOR_REVOKED'; END IF;
 SELECT count(*) INTO bad FROM public.kb_legacy_import_items WHERE run_id=r.id;
 IF bad<>total THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_MAPPING_COUNT'; END IF;
 SELECT * INTO archive FROM public.kb_containers WHERE id=(r.state->>'archive_container')::uuid;
 IF NOT FOUND OR archive.kind<>'project' OR archive.org_id IS DISTINCT FROM r.org_id
 OR archive.owner_id<>r.actor_id OR archive.deleted_at IS NOT NULL OR archive.undo_hidden
 OR NOT EXISTS(SELECT 1 FROM public.kb_container_members WHERE container_id=archive.id AND user_id=r.actor_id AND status='active' AND role='manager')
 OR EXISTS(SELECT 1 FROM public.kb_container_members WHERE container_id=archive.id AND status='active' AND (user_id<>r.actor_id OR role<>'manager'))
 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_ARCHIVE_ACL_CHANGED'; END IF;
 SELECT count(*) INTO bad FROM public.knowledge_nodes n
 LEFT JOIN public.kb_legacy_import_items i ON i.legacy_id=n.id AND i.run_id=r.id
 LEFT JOIN public.kb_objects o ON o.id=i.object_id
 LEFT JOIN public.kb_containers c ON c.id=o.container_id
 LEFT JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision
 WHERE i.legacy_id IS NULL OR i.source_fingerprint<>md5(to_jsonb(n)::text)
 OR o.id IS NULL OR o.deleted_at IS NOT NULL OR o.undo_hidden OR c.deleted_at IS NOT NULL OR c.undo_hidden
 OR o.current_revision<>i.imported_revision OR o.parent_id IS DISTINCT FROM i.parent_id OR o.container_id<>i.container_id
 OR md5(v.body) IS DISTINCT FROM i.body_hash OR strpos(v.body,n.id::text)=0
 OR (i.decision NOT IN ('archive_sensitive','archive_deleted') AND strpos(v.body,n.content)=0)
 OR (i.decision LIKE 'team_%' AND (c.kind<>'team' OR c.org_id IS DISTINCT FROM n.org_id OR c.org_id IS DISTINCT FROM r.org_id OR n.scope<>'org' OR n.category<>'experience'))
 OR (i.decision NOT LIKE 'team_%' AND c.id<>archive.id);
 IF bad>0 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_SOURCE_OR_TARGET_CHANGED'; END IF;
 SELECT count(*),md5(COALESCE(jsonb_agg(to_jsonb(e) ORDER BY id),'[]'::jsonb)::text)
 INTO edge_count,edge_digest FROM public.knowledge_edges e;
 IF edge_count<>r.edge_count OR edge_digest<>r.edge_digest THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_EDGES_CHANGED'; END IF;
 SELECT v.body,o.current_revision INTO index_body,index_revision FROM public.kb_objects o
 JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision
 WHERE o.id=(r.state->>'edge_object_id')::uuid AND o.container_id=archive.id AND o.deleted_at IS NULL AND NOT o.undo_hidden;
 IF NOT FOUND OR md5(index_body) IS DISTINCT FROM r.state->>'edge_hash' OR index_revision IS DISTINCT FROM (r.state->>'edge_revision')::bigint
 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_EDGE_INDEX_MISSING'; END IF;
 FOR edge IN SELECT id FROM public.knowledge_edges LOOP
  IF strpos(index_body,edge.id::text)=0 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_EDGE_INDEX_INCOMPLETE'; END IF;
 END LOOP;
 -- Unknown textual dependencies (including PL/pgSQL with deferred binding) need
 -- explicit review; DROP RESTRICT separately guards catalog-bound dependencies.
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
 AND (p.prosrc LIKE '%knowledge_nodes%' OR p.prosrc LIKE '%knowledge_edges%')
 AND NOT (p.oid=ANY(array_remove(ARRAY[
   to_regprocedure('public.kb_ai_import_entry(uuid,text)')::oid,
   to_regprocedure('public._worker_upsert_model_score_knowledge(uuid,uuid,text,text,text,jsonb,text,double precision,text)')::oid,
   to_regprocedure('public.worker_replace_global_knowledge_seed(jsonb)')::oid],NULL))))
 THEN RAISE EXCEPTION 'LEGACY_RETIREMENT_UNKNOWN_FUNCTION_DEPENDENCY'; END IF;
 UPDATE public.kb_legacy_import_runs SET state=state||jsonb_build_object('legacy_tables_dropped',true,'retired_at',now()) WHERE id=r.id;
END $$;
DROP FUNCTION IF EXISTS public.kb_ai_import_entry(uuid,text);
SET LOCAL ROLE everydayai;
DROP FUNCTION IF EXISTS public._worker_upsert_model_score_knowledge(uuid,uuid,text,text,text,jsonb,text,double precision,text);
DROP FUNCTION IF EXISTS public.worker_replace_global_knowledge_seed(jsonb);
DROP TABLE public.knowledge_edges;
DROP TABLE public.knowledge_nodes;
RESET ROLE;
