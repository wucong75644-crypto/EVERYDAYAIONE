-- Reversible canvas mutations; historical objects/versions/files are never deleted.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.kb_objects ADD COLUMN undo_hidden boolean NOT NULL DEFAULT false;
ALTER TABLE public.kb_containers ADD COLUMN undo_hidden boolean NOT NULL DEFAULT false;
CREATE TABLE public.kb_canvas_history (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor_id uuid NOT NULL REFERENCES public.users(id), org_id uuid REFERENCES public.organizations(id),
 command_id uuid NOT NULL, container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 operation text NOT NULL, before_state jsonb NOT NULL, after_state jsonb NOT NULL,
 state text NOT NULL CHECK(state IN ('applied','undone','discarded')), UNIQUE(actor_id,command_id)
);
CREATE INDEX kb_canvas_history_actor ON public.kb_canvas_history(actor_id,org_id,sequence);
CREATE TABLE public.kb_canvas_history_heads (
 actor_id uuid NOT NULL REFERENCES public.users(id), container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 revision bigint NOT NULL, PRIMARY KEY(actor_id,container_id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['kb_canvas_history','kb_canvas_history_heads'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY kb_internal ON public.%I TO everydayai_owner USING(true) WITH CHECK(true)',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,everydayai,everydayai_runtime',t);
 END LOOP;
END $$;
CREATE FUNCTION public.kb_canvas_state(cid uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('container_hidden',(SELECT undo_hidden FROM public.kb_containers WHERE id=cid),
 'objects',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',o.id,'title',o.title,'parent_id',o.parent_id,'sort_order',o.sort_order,'revision',o.current_revision,'node_id',n.id,'x',n.x,'y',n.y) ORDER BY o.id)
  FROM public.kb_objects o JOIN public.kb_canvas_nodes n ON n.object_id=o.id WHERE o.container_id=cid AND o.deleted_at IS NULL AND NOT o.undo_hidden),'[]'::jsonb),
 'relations',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id) FROM public.kb_relations r WHERE r.container_id=cid
  AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=r.source_object_id AND NOT o.undo_hidden AND o.deleted_at IS NULL)
  AND EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=r.target_object_id AND NOT o.undo_hidden AND o.deleted_at IS NULL)),'[]'::jsonb))
$$;
CREATE FUNCTION public.kb_canvas_patch(a jsonb,b jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('container_hidden',a->'container_hidden',
  'objects',COALESCE((SELECT jsonb_agg(v ORDER BY v->>'id') FROM jsonb_array_elements(a->'objects') v
   WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(b->'objects') w WHERE v=w)),'[]'::jsonb),
  'relations',COALESCE((SELECT jsonb_agg(v ORDER BY v->>'id') FROM jsonb_array_elements(a->'relations') v
   WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(b->'relations') w WHERE v=w)),'[]'::jsonb))
$$;
CREATE FUNCTION public.kb_canvas_history_list() RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; row public.kb_canvas_history%ROWTYPE;
 result jsonb:='{"undo":null,"redo":null}'::jsonb; direction text; valid boolean;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 FOREACH direction IN ARRAY ARRAY['undo','redo'] LOOP
  SELECT * INTO row FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org
   AND state=CASE WHEN direction='undo' THEN 'applied' ELSE 'undone' END
   ORDER BY CASE WHEN direction='undo' THEN -sequence ELSE sequence END LIMIT 1;
  IF FOUND THEN
   valid:=public.kb_can(row.container_id,'write') AND EXISTS(SELECT 1 FROM public.kb_canvas_history_heads h JOIN public.kb_containers c ON c.id=h.container_id WHERE h.actor_id=actor AND h.container_id=row.container_id AND h.revision=c.revision);
   result:=jsonb_set(result,ARRAY[direction],jsonb_build_object('history_id',row.command_id,'operation',row.operation,'container_id',row.container_id,'container_title',(SELECT title FROM public.kb_containers WHERE id=row.container_id AND public.kb_can(id,'read')),'container_kind',(SELECT kind FROM public.kb_containers WHERE id=row.container_id AND public.kb_can(id,'read')),'can_apply',valid,'reason',CASE WHEN valid THEN '' WHEN NOT public.kb_can(row.container_id,'write') THEN '当前没有编辑权限，无法撤销或重做此操作' ELSE '此空间已有后续修改，无法恢复旧操作；请刷新核对，未覆盖新修改' END));
  END IF;
 END LOOP;
 RETURN result;
END $$;
CREATE FUNCTION public.kb_canvas_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; cid uuid; revision_before bigint;
 before_state jsonb; after_state jsonb; result jsonb; prior public.kb_mutation_receipts%ROWTYPE;
 tracked boolean:=p_operation IN ('create_project','create_object','update_title','connect','move_branch','reparent','move_nodes','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation');
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(org::text,''),0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command_id;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>jsonb_build_object('operation',p_operation,'payload',p_payload) THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 IF p_operation<>'create_project' THEN
  IF p_operation IN ('create_object','set_member','copy_branches','delete_branches','move_branches','delete_relation','reconnect_relation') THEN cid:=(p_payload->>'container_id')::uuid;
  ELSIF p_operation='move_nodes' THEN SELECT container_id INTO cid FROM public.kb_canvas_nodes WHERE id=(p_payload->'nodes'->0->>'node_id')::uuid;
  ELSE SELECT container_id INTO cid FROM public.kb_objects WHERE id=COALESCE(p_payload->>'object_id',p_payload->>'source_object_id')::uuid AND deleted_at IS NULL AND NOT undo_hidden; END IF;
  IF cid IS NULL OR NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  SELECT revision INTO revision_before FROM public.kb_containers WHERE id=cid AND NOT undo_hidden FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.kb_canvas_history_heads WHERE actor_id=actor AND container_id=cid AND revision=revision_before) THEN
   UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND container_id=cid;
  END IF;
  IF EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id IN (
    (p_payload->>'parent_id')::uuid,(p_payload->>'target_id')::uuid,(p_payload->>'target_object_id')::uuid)
    AND (o.undo_hidden OR o.deleted_at IS NOT NULL OR EXISTS(SELECT 1 FROM public.kb_containers c WHERE c.id=o.container_id AND c.undo_hidden))) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF p_operation='move_nodes' AND EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'nodes') v JOIN public.kb_canvas_nodes n ON n.id=(v->>'node_id')::uuid JOIN public.kb_objects o ON o.id=n.object_id WHERE o.undo_hidden) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF p_operation='create_object' AND p_payload->>'after_object_id' IS NOT NULL AND revision_before IS DISTINCT FROM (p_payload->>'expected_container_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  before_state:=public.kb_canvas_state(cid);
 ELSE before_state:='{"container_hidden":true,"objects":[],"relations":[]}'::jsonb;
 END IF;
 IF p_operation IN ('delete_relation','reconnect_relation') THEN result:=public.kb_apply_relation_command(p_command_id,p_operation,p_payload);
 ELSIF p_operation='move_branches' THEN result:=public.kb_apply_group_move(p_command_id,p_payload);
 ELSIF p_operation='copy_branches' AND p_payload->>'snapshot_id' IS NOT NULL THEN result:=public.kb_paste_snapshot(p_command_id,p_payload);
 ELSIF p_operation IN ('copy_branches','delete_branches') THEN result:=public.kb_apply_batch_command(p_command_id,p_operation,p_payload);
 ELSIF p_operation='move_branch' THEN result:=public.kb_apply_branch_command(p_command_id,p_payload);
 ELSE result:=public.kb_apply_command(p_command_id,p_operation,p_payload); END IF;
 IF p_operation='create_object' AND p_payload->>'after_object_id' IS NOT NULL THEN PERFORM public.kb_insert_after((result->>'object_id')::uuid,p_payload); END IF;
 IF cid IS NULL THEN SELECT container_id INTO cid FROM public.kb_objects WHERE id=(result->>'object_id')::uuid; END IF;
 after_state:=public.kb_canvas_state(cid);
 IF before_state<>after_state THEN
  UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND state='undone';
 END IF;
 IF tracked AND before_state<>after_state THEN
  INSERT INTO public.kb_canvas_history(actor_id,org_id,command_id,container_id,operation,before_state,after_state,state)
   VALUES(actor,org,p_command_id,cid,p_operation,public.kb_canvas_patch(before_state,after_state),public.kb_canvas_patch(after_state,before_state),'applied');
 ELSIF NOT tracked THEN
  UPDATE public.kb_canvas_history SET state='discarded' WHERE actor_id=actor AND container_id=cid;
 END IF;
 INSERT INTO public.kb_canvas_history_heads(actor_id,container_id,revision) SELECT actor,cid,revision FROM public.kb_containers WHERE id=cid
  ON CONFLICT(actor_id,container_id) DO UPDATE SET revision=EXCLUDED.revision;
 DELETE FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org AND
  (state='discarded' OR sequence NOT IN (SELECT sequence FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org ORDER BY sequence DESC LIMIT 100));
 RETURN result;
END $$;
CREATE FUNCTION public.kb_canvas_restore(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid; entry public.kb_canvas_history%ROWTYPE;
 prior public.kb_mutation_receipts%ROWTYPE; wanted jsonb; other jsonb; obj jsonb; rel jsonb;
 current public.kb_objects%ROWTYPE; oid uuid; cid uuid; rev bigint; body text; event_id uuid:=gen_random_uuid(); result jsonb;
BEGIN
 IF actor IS NULL OR NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_operation NOT IN ('undo_canvas','redo_canvas') THEN RAISE EXCEPTION 'KB_INVALID_PLACEMENT' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-history:'||actor::text||':'||COALESCE(org::text,''),0));
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command_id;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>jsonb_build_object('operation',p_operation,'payload',p_payload) THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 SELECT * INTO entry FROM public.kb_canvas_history WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org
  AND state=CASE WHEN p_operation='undo_canvas' THEN 'applied' ELSE 'undone' END
  ORDER BY CASE WHEN p_operation='undo_canvas' THEN -sequence ELSE sequence END LIMIT 1;
 IF NOT FOUND OR entry.command_id IS DISTINCT FROM (p_payload->>'history_id')::uuid THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
 cid:=entry.container_id;
 PERFORM 1 FROM public.kb_containers WHERE id=cid FOR UPDATE;
 IF NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.kb_canvas_history_heads h JOIN public.kb_containers c ON c.id=h.container_id WHERE h.actor_id=actor AND h.container_id=cid AND h.revision=c.revision) THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
 wanted:=CASE WHEN p_operation='undo_canvas' THEN entry.before_state ELSE entry.after_state END;
 other:=CASE WHEN p_operation='undo_canvas' THEN entry.after_state ELSE entry.before_state END;
 -- Hide objects introduced by the forward step instead of deleting versions or bytes.
 FOR obj IN SELECT v FROM jsonb_array_elements(other->'objects') v WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(wanted->'objects') w WHERE w->>'id'=v->>'id') LOOP
  UPDATE public.kb_objects SET undo_hidden=true WHERE id=(obj->>'id')::uuid AND container_id=cid;
 END LOOP;
 FOR obj IN SELECT * FROM jsonb_array_elements(wanted->'objects') LOOP
  oid:=(obj->>'id')::uuid;
  SELECT * INTO current FROM public.kb_objects WHERE id=oid AND container_id=cid AND deleted_at IS NULL;
  IF NOT FOUND THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF current.title IS DISTINCT FROM obj->>'title' OR current.parent_id IS DISTINCT FROM (obj->>'parent_id')::uuid THEN
   SELECT v.body INTO body FROM public.kb_object_versions v WHERE v.object_id=oid AND v.revision=(obj->>'revision')::bigint;
   IF NOT FOUND THEN RAISE EXCEPTION 'KB_HISTORY_CONFLICT' USING ERRCODE='P0001'; END IF;
   UPDATE public.kb_objects SET title=obj->>'title',parent_id=(obj->>'parent_id')::uuid,current_revision=current_revision+1 WHERE id=oid;
   INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
    VALUES(oid,cid,org,current.current_revision+1,obj->>'title',body,(obj->>'parent_id')::uuid,actor);
  END IF;
  -- Apply order after parent triggers have run; never decrement revision counters.
  UPDATE public.kb_objects SET undo_hidden=false,sort_order=(obj->>'sort_order')::bigint WHERE id=oid;
  UPDATE public.kb_canvas_nodes SET x=(obj->>'x')::float8,y=(obj->>'y')::float8,revision=revision+1
   WHERE id=(obj->>'node_id')::uuid AND object_id=oid AND (x IS DISTINCT FROM (obj->>'x')::float8 OR y IS DISTINCT FROM (obj->>'y')::float8);
 END LOOP;
 DELETE FROM public.kb_relations WHERE container_id=cid AND id IN (SELECT (v->>'id')::uuid FROM jsonb_array_elements(other->'relations') v)
  AND id NOT IN (SELECT (v->>'id')::uuid FROM jsonb_array_elements(wanted->'relations') v);
 FOR rel IN SELECT * FROM jsonb_array_elements(wanted->'relations') LOOP
  INSERT INTO public.kb_relations(id,org_id,container_id,source_object_id,target_object_id,relation_type)
   VALUES((rel->>'id')::uuid,org,cid,(rel->>'source_object_id')::uuid,(rel->>'target_object_id')::uuid,rel->>'relation_type') ON CONFLICT(id) DO UPDATE SET source_object_id=EXCLUDED.source_object_id,target_object_id=EXCLUDED.target_object_id,relation_type=EXCLUDED.relation_type
    WHERE kb_relations.container_id=EXCLUDED.container_id;
 END LOOP;
 UPDATE public.kb_containers SET undo_hidden=(wanted->>'container_hidden')::boolean,revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 UPDATE public.kb_canvas_history SET state=CASE WHEN p_operation='undo_canvas' THEN 'undone' ELSE 'applied' END WHERE sequence=entry.sequence;
 UPDATE public.kb_canvas_history_heads SET revision=rev WHERE actor_id=actor AND container_id=cid;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command_id,p_operation,rev);
 result:=jsonb_build_object('command_id',p_command_id,'status','applied','object_id',NULL,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,p_command_id,org,cid,jsonb_build_object('operation',p_operation,'payload',p_payload),result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_canvas_state(uuid),public.kb_canvas_patch(jsonb,jsonb),public.kb_canvas_history_list(),public.kb_canvas_command(uuid,text,jsonb),public.kb_canvas_restore(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_canvas_history_list(),public.kb_canvas_command(uuid,text,jsonb),public.kb_canvas_restore(uuid,text,jsonb) TO everydayai,everydayai_runtime;
RESET ROLE;
