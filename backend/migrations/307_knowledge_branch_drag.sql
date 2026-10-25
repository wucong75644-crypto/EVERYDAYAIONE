-- Atomic hierarchy, sibling order and branch positions. Existing revisions stay immutable.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.kb_objects ADD COLUMN sort_order bigint NOT NULL DEFAULT 0;
WITH ranked AS (
 SELECT o.id,row_number() OVER(PARTITION BY o.container_id,o.parent_id ORDER BY n.y,n.x,o.id)*1024 AS ordinal
 FROM public.kb_objects o LEFT JOIN public.kb_canvas_nodes n ON n.object_id=o.id
) UPDATE public.kb_objects o SET sort_order=r.ordinal FROM ranked r WHERE r.id=o.id;
CREATE FUNCTION public.kb_append_sibling_order() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$ BEGIN
 IF TG_OP='INSERT' OR NEW.parent_id IS DISTINCT FROM OLD.parent_id THEN
  SELECT COALESCE(max(sort_order),0)+1024 INTO NEW.sort_order FROM public.kb_objects
   WHERE container_id=NEW.container_id AND parent_id IS NOT DISTINCT FROM NEW.parent_id AND id<>NEW.id AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER kb_append_sibling_order BEFORE INSERT OR UPDATE OF parent_id ON public.kb_objects
 FOR EACH ROW EXECUTE FUNCTION public.kb_append_sibling_order();
CREATE FUNCTION public.kb_apply_branch_command(p_command_id uuid,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 oid uuid:=(p_payload->>'object_id')::uuid; target uuid:=(p_payload->>'target_id')::uuid;
 placement text:=p_payload->>'placement'; cid uuid; pid uuid; old public.kb_objects%ROWTYPE;
 prior public.kb_mutation_receipts%ROWTYPE; full_request jsonb:=jsonb_build_object('operation','move_branch','payload',p_payload);
 node jsonb; subtree uuid[]; siblings uuid[]; final_order uuid[]:='{}'; sibling uuid; inserted boolean:=false;
 rev bigint; response jsonb; event_id uuid:=gen_random_uuid();
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(actor::text||':'||p_command_id::text,0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command_id;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>full_request THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 SELECT container_id INTO cid FROM public.kb_objects WHERE id=oid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
 IF cid IS NULL OR NOT public.kb_can(cid,'read') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.kb_containers WHERE id=cid FOR UPDATE;
 IF NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF (SELECT revision FROM public.kb_containers WHERE id=cid)<>(p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 SELECT * INTO old FROM public.kb_objects WHERE id=oid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
 IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF COALESCE((p_payload->>'expected_revision')::bigint,0)<1 OR COALESCE((p_payload->>'expected_container_revision')::bigint,0)<1 OR jsonb_typeof(p_payload->'nodes') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'KB_INVALID_LAYOUT' USING ERRCODE='22023'; END IF;
 IF old.current_revision<>(p_payload->>'expected_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF placement IS NULL OR placement NOT IN ('free','child','before','after') OR (placement='free')<>(target IS NULL) THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 WITH RECURSIVE tree AS (SELECT id FROM public.kb_objects WHERE id=oid UNION SELECT o.id FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id WHERE o.deleted_at IS NULL AND NOT COALESCE((to_jsonb(o)->>'undo_hidden')::boolean,false))
 SELECT array_agg(id) INTO subtree FROM tree;
 IF target=ANY(subtree) THEN RAISE EXCEPTION 'KB_TREE_CYCLE' USING ERRCODE='22023'; END IF;
 pid:=old.parent_id;
 IF target IS NOT NULL THEN
  SELECT CASE WHEN placement='child' THEN id ELSE parent_id END INTO pid FROM public.kb_objects WHERE id=target AND container_id=cid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 END IF;
 IF jsonb_array_length(p_payload->'nodes') NOT BETWEEN 1 AND 10000 OR
  (SELECT count(*)<>count(DISTINCT v->>'node_id') FROM jsonb_array_elements(p_payload->'nodes') v) THEN RAISE EXCEPTION 'KB_INVALID_LAYOUT' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM unnest(subtree) s WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'nodes') v JOIN public.kb_canvas_nodes n ON n.id=(v->>'node_id')::uuid WHERE n.object_id=s)) THEN RAISE EXCEPTION 'KB_INCOMPLETE_BRANCH' USING ERRCODE='22023'; END IF;
 FOR node IN SELECT * FROM jsonb_array_elements(p_payload->'nodes') LOOP
  UPDATE public.kb_canvas_nodes SET x=(node->'position'->>'x')::float8,y=(node->'position'->>'y')::float8,revision=revision+1
   WHERE id=(node->>'node_id')::uuid AND container_id=cid AND revision=(node->>'expected_revision')::bigint;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 END LOOP;
 IF pid IS DISTINCT FROM old.parent_id THEN
  UPDATE public.kb_objects SET parent_id=pid,current_revision=current_revision+1 WHERE id=oid;
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
   SELECT oid,cid,org,old.current_revision+1,v.title,v.body,pid,actor FROM public.kb_object_versions v WHERE v.object_id=oid AND v.revision=old.current_revision;
 END IF;
 IF placement<>'free' THEN
  SELECT array_agg(id ORDER BY sort_order,id) INTO siblings FROM public.kb_objects WHERE container_id=cid AND parent_id IS NOT DISTINCT FROM pid AND id<>oid AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(kb_objects)->>'undo_hidden')::boolean,false);
  FOREACH sibling IN ARRAY COALESCE(siblings,'{}'::uuid[]) LOOP
   IF sibling=target AND placement='before' THEN final_order:=array_append(final_order,oid); inserted:=true; END IF;
   final_order:=array_append(final_order,sibling);
   IF sibling=target AND placement='after' THEN final_order:=array_append(final_order,oid); inserted:=true; END IF;
  END LOOP;
  IF NOT inserted THEN final_order:=array_append(final_order,oid); END IF;
  UPDATE public.kb_objects o SET sort_order=t.ordinal*1024 FROM unnest(final_order) WITH ORDINALITY AS t(id,ordinal) WHERE o.id=t.id;
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command_id,'move_branch',rev);
 response:=jsonb_build_object('command_id',p_command_id,'status','applied','object_id',oid,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,p_command_id,org,cid,full_request,response);
 RETURN response;
END $$;
REVOKE ALL ON FUNCTION public.kb_append_sibling_order(),public.kb_apply_branch_command(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_apply_branch_command(uuid,jsonb) TO everydayai,everydayai_runtime;
RESET ROLE;
