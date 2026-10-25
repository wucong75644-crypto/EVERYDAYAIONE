-- Persist manual creation placement and parent-edge anchors in the original history transaction.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.kb_validate_canvas_data(cid uuid,data jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE g jsonb; member jsonb;
BEGIN
 IF data ? 'manual_layout' THEN
  g:=data->'manual_layout';
  IF jsonb_typeof(g) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_object_keys(g) k WHERE k NOT IN ('source_anchor','target_anchor')) OR (g ? 'source_anchor')<>(g ? 'target_anchor') THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  FOR member IN SELECT value FROM jsonb_each(g) LOOP
   IF jsonb_typeof(member) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
   IF jsonb_typeof(member->'x') IS DISTINCT FROM 'number' OR jsonb_typeof(member->'y') IS DISTINCT FROM 'number' OR EXISTS(SELECT 1 FROM jsonb_object_keys(member) k WHERE k NOT IN ('x','y')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
   IF (member->>'x')::numeric NOT BETWEEN 0 AND 1 OR (member->>'y')::numeric NOT BETWEEN 0 AND 1 OR NOT ((member->>'x')::numeric IN (0,1) OR (member->>'y')::numeric IN (0,1)) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  END LOOP;
 END IF;
 IF data ? 'title_format' THEN PERFORM public.kb_validate_title_format(data->'title_format',NULL); END IF;
 IF jsonb_typeof(data)<>'object' OR pg_column_size(data)>65536 OR EXISTS(SELECT 1 FROM jsonb_object_keys(data) k WHERE k NOT IN ('note','labels','task','summary','boundary','style','image','video','link','width','height','structure','title_format','boundaries','manual_layout')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'height' AND (jsonb_typeof(data->'height')<>'number' OR (data->>'height')::numeric NOT BETWEEN 80 AND 1600 OR (data->>'height')::numeric<>trunc((data->>'height')::numeric)) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'width' AND (jsonb_typeof(data->'width')<>'number' OR (data->>'width')::numeric NOT BETWEEN 80 AND 800 OR (data->>'width')::numeric<>trunc((data->>'width')::numeric)) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 IF data ? 'image' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions v ON v.binding_id=b.id JOIN public.kb_file_versions f ON f.id=v.version_id WHERE f.mime_type IN ('image/png','image/jpeg','image/webp','image/gif') AND b.container_id=cid AND b.id=(data->'image'->>'binding_id')::uuid AND v.revision=(data->'image'->>'revision')::bigint) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF data ? 'video' AND NOT EXISTS(SELECT 1 FROM public.kb_file_bindings b JOIN public.kb_file_binding_versions v ON v.binding_id=b.id JOIN public.kb_file_versions f ON f.id=v.version_id WHERE f.mime_type='video/mp4' AND b.container_id=cid AND b.id=(data->'video'->>'binding_id')::uuid AND v.revision=(data->'video'->>'revision')::bigint) THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF data ? 'boundaries' THEN
  IF jsonb_typeof(data->'boundaries') IS DISTINCT FROM 'array' OR jsonb_array_length(data->'boundaries')>100 OR (SELECT count(DISTINCT v->>'id') FROM jsonb_array_elements(data->'boundaries') v)<>jsonb_array_length(data->'boundaries') THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  FOR g IN SELECT value FROM jsonb_array_elements(data->'boundaries') LOOP
   PERFORM (g->>'id')::uuid;
   IF g->>'id' IS NULL OR COALESCE(length(g->>'title'),0)>200 OR EXISTS(SELECT 1 FROM jsonb_object_keys(g) k WHERE k NOT IN ('id','members','title')) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  END LOOP;
 END IF;
 FOR g IN SELECT value FROM jsonb_each(data) WHERE key IN ('summary','boundary') UNION ALL SELECT value FROM jsonb_array_elements(COALESCE(data->'boundaries','[]'::jsonb)) LOOP
  IF jsonb_typeof(g->'members')<>'array' OR jsonb_array_length(g->'members') NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  IF (SELECT count(DISTINCT parent_id) FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')))<>1 OR EXISTS(SELECT 1 FROM public.kb_objects WHERE id IN (SELECT value::uuid FROM jsonb_array_elements_text(g->'members')) AND parent_id IS NULL) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
  FOR member IN SELECT * FROM jsonb_array_elements(g->'members') LOOP
   IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=(member#>>'{}')::uuid AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden) THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  END LOOP;
 END LOOP;
END $$;
