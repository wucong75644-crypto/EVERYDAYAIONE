SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_insert_after(oid uuid,payload jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE obj public.kb_objects%ROWTYPE; anchor uuid:=(payload->>'after_object_id')::uuid; sibling uuid; ordinal bigint:=0; node jsonb;
BEGIN
 SELECT * INTO obj FROM public.kb_objects WHERE id=oid;
 IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=anchor AND id<>oid AND container_id=obj.container_id AND parent_id IS NOT DISTINCT FROM obj.parent_id AND NOT undo_hidden AND deleted_at IS NULL) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF (SELECT count(DISTINCT v->>'node_id') FROM jsonb_array_elements(payload->'nodes') v)<>jsonb_array_length(payload->'nodes') THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 FOR node IN SELECT * FROM jsonb_array_elements(payload->'nodes') LOOP
  UPDATE public.kb_canvas_nodes n SET x=(node->'position'->>'x')::float8,y=(node->'position'->>'y')::float8,revision=revision+1
   WHERE n.id=(node->>'node_id')::uuid AND n.object_id<>oid AND n.container_id=obj.container_id AND n.revision=(node->>'expected_revision')::bigint
    AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=n.object_id AND NOT o.undo_hidden AND o.deleted_at IS NULL);
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 END LOOP;
 FOR sibling IN SELECT id FROM public.kb_objects WHERE container_id=obj.container_id AND parent_id IS NOT DISTINCT FROM obj.parent_id AND id<>oid AND NOT undo_hidden AND deleted_at IS NULL ORDER BY sort_order,id LOOP
  ordinal:=ordinal+1024; UPDATE public.kb_objects SET sort_order=ordinal WHERE id=sibling;
  IF sibling=anchor THEN ordinal:=ordinal+1024; UPDATE public.kb_objects SET sort_order=ordinal WHERE id=oid; END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.kb_insert_after(uuid,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
RESET ROLE;
