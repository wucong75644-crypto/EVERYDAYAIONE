-- Recoverable project removal. Personal/team system spaces are not deletable.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_delete_project(p_command uuid,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid:=(p_payload->>'container_id')::uuid;
 current public.kb_containers%ROWTYPE; prior public.kb_mutation_receipts%ROWTYPE;
 before_state jsonb; after_state jsonb; result jsonb; event_id uuid:=gen_random_uuid(); rev bigint;
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() OR NOT public.kb_can(cid,'manage') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(org::text,''),0));
 SELECT * INTO current FROM public.kb_containers WHERE id=cid FOR UPDATE;
 IF NOT public.kb_can(cid,'manage') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF current.kind<>'project' OR current.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>jsonb_build_object('operation','delete_project','payload',p_payload) THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 IF current.undo_hidden THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF current.revision IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.kb_canvas_history_heads WHERE actor_id=actor AND container_id=cid AND revision=current.revision) THEN
  UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND container_id=cid;
 END IF;
 before_state:=public.kb_canvas_state(cid);
 UPDATE public.kb_objects SET undo_hidden=true WHERE container_id=cid AND deleted_at IS NULL AND NOT undo_hidden;
 UPDATE public.kb_containers SET undo_hidden=true,revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 after_state:=public.kb_canvas_state(cid);
 UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND state='undone';
 INSERT INTO public.kb_canvas_history(actor_id,org_id,command_id,container_id,operation,before_state,after_state,state)
 VALUES(actor,org,p_command,cid,'delete_project',public.kb_canvas_patch(before_state,after_state),public.kb_canvas_patch(after_state,before_state),'applied');
 INSERT INTO public.kb_canvas_history_heads(actor_id,container_id,revision) VALUES(actor,cid,rev)
 ON CONFLICT(actor_id,container_id) DO UPDATE SET revision=excluded.revision;
 DELETE FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND
 (state='discarded' OR sequence NOT IN (SELECT sequence FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org ORDER BY sequence DESC LIMIT 100));
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command,'delete_project',rev);
 result:=jsonb_build_object('command_id',p_command,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result)
 VALUES(actor,p_command,org,cid,jsonb_build_object('operation','delete_project','payload',p_payload),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_delete_project(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_delete_project(uuid,jsonb) TO everydayai,everydayai_runtime;

-- Reuse the existing atomic restore, while retaining manager-only project semantics.
ALTER FUNCTION public.kb_canvas_restore(uuid,text,jsonb) RENAME TO kb_canvas_restore_before_project_delete;
REVOKE ALL ON FUNCTION public.kb_canvas_restore_before_project_delete(uuid,text,jsonb) FROM PUBLIC,everydayai,everydayai_runtime;
CREATE FUNCTION public.kb_canvas_restore(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE entry public.kb_canvas_history%ROWTYPE; actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(NULLIF(current_setting('app.org_id',true),''),''),0));
 SELECT * INTO entry FROM public.kb_canvas_history WHERE actor_id=actor AND command_id=(p_payload->>'history_id')::uuid AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid;
 IF entry.operation='delete_project' THEN
  PERFORM 1 FROM public.kb_containers WHERE id=entry.container_id FOR UPDATE;
  IF NOT public.kb_can(entry.container_id,'manage') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN public.kb_canvas_restore_before_project_delete(p_command_id,p_operation,p_payload);
END $$;
REVOKE ALL ON FUNCTION public.kb_canvas_restore(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_canvas_restore(uuid,text,jsonb) TO everydayai,everydayai_runtime;
ALTER FUNCTION public.kb_canvas_history_list() RENAME TO kb_canvas_history_list_before_project_delete;
REVOKE ALL ON FUNCTION public.kb_canvas_history_list_before_project_delete() FROM PUBLIC,everydayai,everydayai_runtime;
CREATE FUNCTION public.kb_canvas_history_list() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE result jsonb; direction text;
BEGIN
 result:=public.kb_canvas_history_list_before_project_delete();
 FOREACH direction IN ARRAY ARRAY['undo','redo'] LOOP
  IF result->direction->>'operation'='delete_project' AND NOT public.kb_can((result->direction->>'container_id')::uuid,'manage') THEN
   result:=jsonb_set(result,ARRAY[direction,'can_apply'],'false'::jsonb);
   result:=jsonb_set(result,ARRAY[direction,'reason'],to_jsonb('当前没有项目管理权限，无法恢复或重做项目删除'::text));
  END IF;
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_canvas_history_list() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_canvas_history_list() TO everydayai,everydayai_runtime;
RESET ROLE;
