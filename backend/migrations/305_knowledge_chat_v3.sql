-- V3 additive migration; preserve references/snapshots on application rollback.
SET LOCAL ROLE everydayai;
ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS knowledge_refs jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE public.messages ADD CONSTRAINT messages_knowledge_refs_array CHECK
 (jsonb_typeof(knowledge_refs)='array' AND jsonb_array_length(knowledge_refs)<=8);
GRANT REFERENCES(id) ON public.messages,public.conversations TO everydayai_owner;
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.kb_read_snapshots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actor_id uuid NOT NULL REFERENCES public.users(id),
 org_id uuid, conversation_id uuid NOT NULL REFERENCES public.conversations(id),
 input_message_id uuid NOT NULL REFERENCES public.messages(id),
 snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(actor_id,input_message_id)
);
CREATE TABLE public.kb_read_receipts (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 snapshot_id uuid NOT NULL REFERENCES public.kb_read_snapshots(id),
 item_handle uuid NOT NULL, state text NOT NULL CHECK(state IN ('outline_seen','snippet_read','body_partial','body_complete','file_materialized','reference_followed')),
 start_offset integer NOT NULL DEFAULT 0 CHECK(start_offset>=0),
 end_offset integer NOT NULL DEFAULT 0 CHECK(end_offset>=start_offset),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_receipt_snapshot_item_state ON public.kb_read_receipts(snapshot_id,item_handle,state,start_offset);
ALTER TABLE public.kb_read_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_read_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE public.kb_read_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_read_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY kb_snapshot_actor ON public.kb_read_snapshots FOR ALL TO everydayai_runtime
 USING(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid())
 WITH CHECK(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid());
CREATE POLICY kb_receipt_actor ON public.kb_read_receipts FOR ALL TO everydayai_runtime
 USING(EXISTS(SELECT 1 FROM public.kb_read_snapshots s WHERE s.id=snapshot_id))
 WITH CHECK(EXISTS(SELECT 1 FROM public.kb_read_snapshots s WHERE s.id=snapshot_id));
GRANT SELECT,INSERT ON public.kb_read_snapshots,public.kb_read_receipts TO everydayai_runtime;
GRANT USAGE,SELECT ON SEQUENCE public.kb_read_receipts_id_seq TO everydayai_runtime;
CREATE FUNCTION public.kb_chat_file(p_object uuid,p_binding uuid,p_revision bigint) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT meta.value||jsonb_build_object('extracted_text',v.extracted_text)
 FROM (SELECT public.kb_file_version(p_object,p_binding,p_revision) AS value) meta
 JOIN public.kb_file_versions v ON v.id=(meta.value->>'version_id')::uuid
 WHERE meta.value IS NOT NULL;
$$;
REVOKE ALL ON FUNCTION public.kb_chat_file(uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_chat_file(uuid,uuid,bigint) TO everydayai_runtime;
CREATE FUNCTION public.kb_lock_read_snapshot(p_revisions jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE item record; actual bigint;
BEGIN
 IF NOT public.kb_actor_valid() OR jsonb_typeof(p_revisions)<>'object' THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='42501'; END IF;
 FOR item IN SELECT key,value FROM jsonb_each_text(p_revisions) ORDER BY key LOOP
  IF NOT public.kb_can(item.key::uuid,'read') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='42501'; END IF;
  SELECT revision INTO actual FROM public.kb_containers WHERE id=item.key::uuid AND deleted_at IS NULL FOR SHARE;
  IF actual IS DISTINCT FROM item.value::bigint THEN RAISE EXCEPTION 'KB_SOURCE_CHANGED' USING ERRCODE='40001'; END IF;
 END LOOP;
END; $$;
REVOKE ALL ON FUNCTION public.kb_lock_read_snapshot(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_lock_read_snapshot(jsonb) TO everydayai_runtime;
RESET ROLE;
