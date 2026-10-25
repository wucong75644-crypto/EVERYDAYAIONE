-- Relation editing uses the canvas history transaction and its actor/container locks.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_apply_relation_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 cid uuid:=(p_payload->>'container_id')::uuid; rid uuid:=(p_payload->>'relation_id')::uuid;
 source_id uuid; target_id uuid; rev bigint; rel public.kb_relations%ROWTYPE;
 event_id uuid:=gen_random_uuid(); result jsonb;
BEGIN
 IF p_operation NOT IN ('delete_relation','reconnect_relation') THEN RAISE EXCEPTION 'KB_INVALID_RELATION' USING ERRCODE='22023'; END IF;
 IF NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT revision INTO rev FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF rev IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 SELECT * INTO rel FROM public.kb_relations WHERE id=rid AND container_id=cid;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=rel.source_object_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
  OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=rel.target_object_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
  THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF p_operation='reconnect_relation' THEN
  source_id:=(p_payload->>'source_object_id')::uuid; target_id:=(p_payload->>'target_object_id')::uuid;
  IF source_id IS NULL OR target_id IS NULL OR source_id=target_id THEN RAISE EXCEPTION 'KB_INVALID_RELATION' USING ERRCODE='22023'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=source_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
   OR NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=target_id AND container_id=cid AND deleted_at IS NULL AND NOT undo_hidden)
   THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF EXISTS(SELECT 1 FROM public.kb_relations WHERE container_id=cid AND source_object_id=source_id AND target_object_id=target_id AND relation_type=rel.relation_type AND id<>rid)
   THEN RAISE EXCEPTION 'KB_RELATION_DUPLICATE' USING ERRCODE='22023'; END IF;
  IF source_id IS DISTINCT FROM rel.source_object_id OR target_id IS DISTINCT FROM rel.target_object_id THEN
   UPDATE public.kb_relations SET source_object_id=source_id,target_object_id=target_id WHERE id=rid AND container_id=cid;
   UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
  END IF;
 ELSE
  DELETE FROM public.kb_relations WHERE id=rid AND container_id=cid;
  UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 END IF;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command_id,p_operation,rev);
 result:=jsonb_build_object('command_id',p_command_id,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
  VALUES(actor,p_command_id,org,cid,jsonb_build_object('operation',p_operation,'payload',p_payload),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_apply_relation_command(uuid,text,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
RESET ROLE;
