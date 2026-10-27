-- Literal navigation terms are computed once per immutable cached extraction.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_directory_terms(p_text text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
DECLARE line text; term text; terms jsonb:='[]'::jsonb;
BEGIN
 FOR line IN SELECT btrim(t) FROM regexp_split_to_table(COALESCE(p_text,''),E'\\n') t LOOP
  line:=btrim(CASE WHEN strpos(line,'|')>0 THEN split_part(line,'|',CASE WHEN left(line,1)='|' THEN 2 ELSE 1 END) ELSE ltrim(line,'# ') END);
  -- Keep literal caption fragments beside URLs, not the URL or other table columns.
  FOR term IN SELECT left(btrim(t),128) FROM regexp_split_to_table(line,'[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]]*') t LOOP
   IF length(term)<2 OR term ~ '^[: \-]+$' OR terms ? term THEN CONTINUE; END IF;
   terms:=terms || jsonb_build_array(term);
   IF jsonb_array_length(terms)>=256 THEN RETURN terms; END IF;
  END LOOP;
 END LOOP;
 RETURN terms;
END $$;
REVOKE ALL ON FUNCTION public.kb_directory_terms(text) FROM PUBLIC;
ALTER TABLE public.kb_media_extractions ADD COLUMN directory_terms jsonb
 GENERATED ALWAYS AS (public.kb_directory_terms(result->>'text')) STORED;
CREATE FUNCTION public.kb_file_directory(p_object uuid,p_binding uuid,p_revision bigint,p_config text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v jsonb;
BEGIN
 v:=public.kb_file_version(p_object,p_binding,p_revision);
 IF v IS NULL THEN RETURN NULL; END IF;
 RETURN (SELECT directory_terms FROM public.kb_media_extractions WHERE version_id=(v->>'version_id')::uuid AND config=p_config);
END $$;
REVOKE ALL ON FUNCTION public.kb_file_directory(uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_file_directory(uuid,uuid,bigint,text) TO everydayai_runtime,everydayai;
