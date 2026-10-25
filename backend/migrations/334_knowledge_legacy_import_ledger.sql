-- Body-free reconciliation metadata. All migrated content stays in kb_objects.
SET LOCAL ROLE everydayai;
GRANT SELECT ON public.knowledge_nodes,public.knowledge_edges TO everydayai_owner;
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.kb_legacy_import_runs (
 id uuid PRIMARY KEY, actor_id uuid NOT NULL REFERENCES public.users(id), org_id uuid NOT NULL REFERENCES public.organizations(id),
 source_digest text NOT NULL, backup_manifest_sha256 text NOT NULL CHECK(length(backup_manifest_sha256)=64),
 backup_path text NOT NULL, source_count int NOT NULL CHECK(source_count>=0), edge_count int NOT NULL,
 edge_digest text NOT NULL, state jsonb NOT NULL DEFAULT '{}', verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.kb_legacy_import_items (
 legacy_id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES public.kb_legacy_import_runs(id),
 source_fingerprint text NOT NULL, object_id uuid NOT NULL REFERENCES public.kb_objects(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), parent_id uuid REFERENCES public.kb_objects(id),
 imported_revision bigint NOT NULL, body_hash text NOT NULL, decision text NOT NULL, imported_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.kb_legacy_import_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_legacy_import_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.kb_legacy_import_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_legacy_import_items FORCE ROW LEVEL SECURITY;
CREATE POLICY migration_owner ON public.kb_legacy_import_runs TO everydayai_owner USING(true) WITH CHECK(true);
CREATE POLICY migration_owner ON public.kb_legacy_import_items TO everydayai_owner USING(true) WITH CHECK(true);
CREATE POLICY migration_operator ON public.kb_legacy_import_runs TO everydayai
 USING(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND org_id=NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid() AND public.reach_active_member(org_id,true))
 WITH CHECK(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND org_id=NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid() AND public.reach_active_member(org_id,true));
CREATE POLICY migration_operator ON public.kb_legacy_import_items TO everydayai
 USING(EXISTS(SELECT 1 FROM public.kb_legacy_import_runs r WHERE r.id=run_id))
 WITH CHECK(EXISTS(SELECT 1 FROM public.kb_legacy_import_runs r WHERE r.id=run_id));
REVOKE ALL ON public.kb_legacy_import_runs,public.kb_legacy_import_items FROM PUBLIC,everydayai_runtime;
GRANT SELECT,INSERT,UPDATE ON public.kb_legacy_import_runs TO everydayai;
GRANT SELECT,INSERT ON public.kb_legacy_import_items TO everydayai;
RESET ROLE;
