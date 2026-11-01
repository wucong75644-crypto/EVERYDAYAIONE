-- Per-file proof and cleanup addresses precede any backup or destructive IO.
SET LOCAL ROLE everydayai;
ALTER TABLE public.deleted_files ALTER COLUMN org_id DROP NOT NULL;
ALTER TABLE public.deleted_files ADD COLUMN native_status text CHECK(native_status IN ('allocated','completed'));
COMMENT ON COLUMN public.deleted_files.native_status IS 'NULL legacy; allocated backup hidden from restore until actual detach is proven; original expiry purger also cleans unused backups';
CREATE TABLE public.agent_task_file_deletions(
 native_invocation_id uuid NOT NULL,
 item_index int NOT NULL CHECK(item_index BETWEEN 0 AND 99), operation_id text NOT NULL,
 actor_user_id uuid NOT NULL,org_id uuid, manifest jsonb NOT NULL, deleted_record_id bigint NOT NULL UNIQUE REFERENCES public.deleted_files(id) ON DELETE CASCADE,
 settlement_token uuid NOT NULL,armed boolean NOT NULL DEFAULT false,
 phase text NOT NULL DEFAULT 'allocated' CHECK(phase IN ('allocated','backed_up','completed','abandoned')),
 backup_etag text,created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(native_invocation_id,item_index)
);
ALTER TABLE public.agent_task_file_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_file_deletions FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_task_file_deletions_scope ON public.agent_task_file_deletions
 USING(current_setting('app.access_kind',true)='runtime' AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid)
 WITH CHECK(current_setting('app.access_kind',true)='runtime' AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid);
CREATE FUNCTION public.agent_task_file_delete_items(p_native_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('index',f.item_index,'manifest',f.manifest,'phase',f.phase,
   'record_id',d.id,'oss_object_key',d.oss_object_key,'backup_etag',f.backup_etag) ORDER BY f.item_index),'[]'::jsonb)
 FROM public.agent_task_file_deletions f JOIN public.deleted_files d ON d.id=f.deleted_record_id
 WHERE f.native_invocation_id=p_native_id AND d.user_id=f.actor_user_id AND d.org_id IS NOT DISTINCT FROM f.org_id
   AND d.relative_path=f.manifest->>'relative_path'
$$;
REVOKE ALL ON FUNCTION public.agent_task_file_delete_items(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_file_delete_items(uuid) TO everydayai;
CREATE FUNCTION public.prepare_agent_task_file_delete(p_task_id uuid,p_execution_token uuid,p_call_id text,p_args_hash text,p_intent jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; i public.tool_invocations%ROWTYPE;
 op jsonb; checked jsonb; r public.agent_task_native_writes%ROWTYPE; d public.deleted_files%ROWTYPE; item jsonb; pos bigint;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=p_task_id FOR UPDATE;
 IF s.root_task_id IS NULL THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 checked:=public.read_agent_task_native_results(p_task_id,p_execution_token,s.root_task_id,s.revision);
 IF checked->>'outcome' IS DISTINCT FROM 'found' OR s.state->>'image_binding_version' IS DISTINCT FROM '3'
   OR jsonb_typeof(p_intent) IS DISTINCT FROM 'object'
   OR octet_length(p_intent::text)>1048576 THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO i FROM public.tool_invocations WHERE task_id=t.id AND turn_id=t.turn_id AND conversation_id=t.conversation_id
   AND tool_call_id=p_call_id AND tool_name='file_delete' AND status='running' AND args_hash=p_args_hash;
 SELECT v INTO op FROM jsonb_array_elements(s.state->'operations') v WHERE v->>'owner_task_id'=t.id::text
   AND v->>'owner_turn_id'=t.turn_id::text AND v->>'invocation_id'=p_call_id AND v->>'tool_name'=i.tool_name
   AND v->>'capability'='workspace.file.delete' AND v->>'arguments_sha256'=i.args_hash AND v->>'status'='reserved';
 IF i.id IS NULL OR op IS NULL THEN RAISE EXCEPTION 'TASK_NATIVE_WRITE_BINDING_REQUIRED' USING ERRCODE='42501'; END IF;
 IF p_intent->>'action' IS DISTINCT FROM 'delete' OR jsonb_typeof(p_intent->'items') IS DISTINCT FROM 'array'
   OR jsonb_array_length(p_intent->'items') NOT BETWEEN 1 AND 100 THEN
   RAISE EXCEPTION 'TASK_FILE_DELETE_ITEMS_REQUIRED' USING ERRCODE='42501'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_intent->'items') LOOP
   IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR NULLIF(item->>'relative_path','') IS NULL
     OR NULLIF(item->>'workspace_path','') IS NULL OR (item->>'sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
     OR jsonb_typeof(item->'version') IS DISTINCT FROM 'array' OR jsonb_array_length(item->'version')<>4 THEN
     RAISE EXCEPTION 'TASK_FILE_DELETE_ITEMS_REQUIRED' USING ERRCODE='42501'; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value->>'relative_path') FROM jsonb_array_elements(p_intent->'items'))<>jsonb_array_length(p_intent->'items') THEN
   RAISE EXCEPTION 'TASK_FILE_DELETE_DUPLICATE_TARGET' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=op->>'id' FOR UPDATE;
 IF r.operation_id IS NOT NULL THEN
   IF r.native_invocation_id=i.id AND r.args_hash=i.args_hash AND r.intent=p_intent AND r.domain='file' THEN
     RETURN jsonb_build_object('operation_id',r.operation_id,'native_id',i.id,'dispatch_token',r.dispatch_token,'items',public.agent_task_file_delete_items(i.id)); END IF;
   -- Only the original not-started evidence authorizes a newly fenced attempt.
   IF r.phase<>'prepared' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op->'previous_attempts') old
       WHERE old->>'invocation_id'=(SELECT tool_call_id FROM public.tool_invocations WHERE id=r.native_invocation_id)
         AND old->>'arguments_sha256'=r.args_hash AND old->>'owner_task_id'=r.owner_task_id::text) THEN
     RAISE EXCEPTION 'TASK_NATIVE_WRITE_ALREADY_DISPATCHED' USING ERRCODE='42501'; END IF;
 END IF;
 UPDATE public.agent_task_file_deletions SET phase='abandoned'
 WHERE operation_id=op->>'id' AND phase IN ('allocated','backed_up');
 INSERT INTO public.agent_task_native_writes(operation_id,native_invocation_id,owner_task_id,actor_user_id,org_id,domain,args_hash,intent,execution_token,dispatch_token)
 VALUES(op->>'id',i.id,t.id,t.user_id,t.org_id,'file',i.args_hash,p_intent,p_execution_token,gen_random_uuid())
 ON CONFLICT(operation_id) DO UPDATE SET native_invocation_id=EXCLUDED.native_invocation_id,owner_task_id=EXCLUDED.owner_task_id,
   args_hash=EXCLUDED.args_hash,intent=EXCLUDED.intent,execution_token=EXCLUDED.execution_token,dispatch_token=EXCLUDED.dispatch_token,
   dispatch=NULL,response=NULL,phase='prepared',updated_at=now()
 RETURNING * INTO r;
 FOR item,pos IN SELECT value,ordinality-1 FROM jsonb_array_elements(p_intent->'items') WITH ORDINALITY LOOP
   INSERT INTO public.deleted_files(org_id,user_id,relative_path,oss_object_key,purge_after,native_status)
   VALUES(t.org_id,t.user_id,item->>'relative_path','workspace/.agent-task-backups/'||i.id::text||'/'||pos::text,
     now()+interval '30 days','allocated') RETURNING * INTO d;
   INSERT INTO public.agent_task_file_deletions(native_invocation_id,item_index,operation_id,actor_user_id,org_id,manifest,deleted_record_id,settlement_token)
   VALUES(i.id,pos,r.operation_id,t.user_id,t.org_id,item,d.id,r.dispatch_token);
 END LOOP;
 RETURN jsonb_build_object('operation_id',r.operation_id,'native_id',i.id,'dispatch_token',r.dispatch_token,'items',public.agent_task_file_delete_items(i.id));
END $$;
REVOKE ALL ON FUNCTION public.prepare_agent_task_file_delete(uuid,uuid,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.prepare_agent_task_file_delete(uuid,uuid,text,text,jsonb) TO everydayai;



CREATE FUNCTION public.read_agent_task_file_delete(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; items jsonb;
BEGIN
 checked:=public.read_agent_task_native_write(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 IF proof->>'domain' IS DISTINCT FROM 'file' OR proof#>>'{intent,action}' IS DISTINCT FROM 'delete' THEN
   RAISE EXCEPTION 'TASK_FILE_DELETE_PROOF_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('index',f.item_index,'manifest',f.manifest,'phase',f.phase,
   'record_id',d.id,'oss_object_key',d.oss_object_key,'backup_etag',f.backup_etag) ORDER BY f.item_index),'[]'::jsonb) INTO items
 FROM public.agent_task_file_deletions f JOIN public.deleted_files d ON d.id=f.deleted_record_id
 WHERE f.operation_id=p_operation_id AND f.native_invocation_id=(proof->>'native_id')::uuid
   AND d.user_id=f.actor_user_id AND d.org_id IS NOT DISTINCT FROM f.org_id AND d.relative_path=f.manifest->>'relative_path';
 RETURN jsonb_build_object('outcome','found','proof',proof||jsonb_build_object('items',items));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_file_delete(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_file_delete(uuid,uuid,uuid,bigint,text) TO everydayai;

CREATE FUNCTION public.record_agent_task_file_backup(p_operation_id text,p_dispatch_token uuid,p_item_index int,p_etag text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE r public.agent_task_native_writes%ROWTYPE; f public.agent_task_file_deletions%ROWTYPE;
BEGIN
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id FOR UPDATE;
 SELECT * INTO f FROM public.agent_task_file_deletions WHERE native_invocation_id=r.native_invocation_id AND item_index=p_item_index FOR UPDATE;
 IF r.operation_id IS NULL OR r.domain IS DISTINCT FROM 'file' OR r.intent->>'action' IS DISTINCT FROM 'delete'
   OR r.dispatch_token IS DISTINCT FROM p_dispatch_token OR r.phase IS DISTINCT FROM 'prepared'
   OR f.operation_id IS DISTINCT FROM r.operation_id OR f.phase NOT IN ('allocated','backed_up') OR NULLIF(p_etag,'') IS NULL THEN
   RAISE EXCEPTION 'TASK_FILE_BACKUP_RECORD_DENIED' USING ERRCODE='42501'; END IF;
 IF f.phase='backed_up' AND f.backup_etag IS DISTINCT FROM p_etag THEN
   RAISE EXCEPTION 'TASK_FILE_BACKUP_CHANGED' USING ERRCODE='42501'; END IF;
 UPDATE public.agent_task_file_deletions SET phase='backed_up',backup_etag=p_etag WHERE native_invocation_id=f.native_invocation_id AND item_index=f.item_index;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.record_agent_task_file_backup(text,uuid,int,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_agent_task_file_backup(text,uuid,int,text) TO everydayai;

CREATE FUNCTION public.authorize_agent_task_file_delete_item(p_operation_id text,p_dispatch_token uuid,p_item_index int)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE r public.agent_task_native_writes%ROWTYPE; t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; checked jsonb;
BEGIN
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
 SELECT * INTO t FROM public.tasks WHERE id=r.owner_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=t.id FOR UPDATE;
 IF s.root_task_id IS NULL OR s.state->>'status' IS DISTINCT FROM 'active' THEN
   RAISE EXCEPTION 'TASK_FILE_DELETE_DISPATCH_DENIED' USING ERRCODE='42501'; END IF;
 checked:=public.read_agent_task_native_results(t.id,r.execution_token,s.root_task_id,s.revision);
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id FOR UPDATE;
 IF checked->>'outcome' IS DISTINCT FROM 'found' OR r.dispatch_token IS DISTINCT FROM p_dispatch_token
   OR NOT EXISTS(SELECT 1 FROM public.tool_invocations i WHERE i.id=r.native_invocation_id AND i.status='running')
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'operations') op WHERE op->>'id'=p_operation_id AND op->>'status'='reserved')
   OR r.domain IS DISTINCT FROM 'file' OR r.intent->>'action' IS DISTINCT FROM 'delete' OR r.phase IS DISTINCT FROM 'dispatched'
   OR NOT EXISTS(SELECT 1 FROM public.agent_task_file_deletions f JOIN public.deleted_files d ON d.id=f.deleted_record_id
     WHERE f.native_invocation_id=r.native_invocation_id AND f.item_index=p_item_index AND f.phase='backed_up' AND f.armed
       AND NOT d.purged AND d.purge_after>now()) THEN
   RAISE EXCEPTION 'TASK_FILE_DELETE_DISPATCH_DENIED' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object('outcome','ready');
END $$;
REVOKE ALL ON FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,int) TO everydayai;

CREATE FUNCTION public.complete_agent_task_file_delete_item(p_operation_id text,p_dispatch_token uuid,p_item_index int,p_evidence jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE r public.agent_task_native_writes%ROWTYPE; f public.agent_task_file_deletions%ROWTYPE; done jsonb; all_done boolean;
BEGIN
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id FOR UPDATE;
 SELECT * INTO f FROM public.agent_task_file_deletions WHERE operation_id=p_operation_id
   AND item_index=p_item_index AND settlement_token=p_dispatch_token AND (r.operation_id IS NULL OR native_invocation_id=r.native_invocation_id) FOR UPDATE;
 IF f.operation_id IS NULL OR f.armed IS NOT TRUE OR f.settlement_token IS DISTINCT FROM p_dispatch_token
   OR (r.operation_id IS NOT NULL AND (r.domain IS DISTINCT FROM 'file' OR r.intent->>'action' IS DISTINCT FROM 'delete'
     OR r.dispatch_token IS DISTINCT FROM p_dispatch_token OR r.phase NOT IN ('dispatched','returned')))
   OR f.phase NOT IN ('backed_up','completed')
   OR p_evidence IS NULL OR p_evidence IS DISTINCT FROM f.manifest OR NULLIF(f.backup_etag,'') IS NULL THEN
   RAISE EXCEPTION 'TASK_FILE_DELETE_EVIDENCE_DENIED' USING ERRCODE='42501'; END IF;
 IF f.phase<>'completed' THEN
   UPDATE public.deleted_files SET native_status='completed',deleted_at=now(),
      purge_after=CASE WHEN NOT purged AND purge_after>now() THEN now()+interval '30 days' ELSE purge_after END
    WHERE id=f.deleted_record_id AND user_id=f.actor_user_id AND org_id IS NOT DISTINCT FROM f.org_id;
   IF NOT FOUND THEN RAISE EXCEPTION 'TASK_FILE_DELETE_RECORD_CHANGED' USING ERRCODE='42501'; END IF;
   UPDATE public.agent_task_file_deletions SET phase='completed' WHERE native_invocation_id=f.native_invocation_id AND item_index=f.item_index;
 END IF;
 SELECT bool_and(phase='completed'),jsonb_agg(jsonb_build_object('index',item_index,'manifest',manifest,'record_id',deleted_record_id,
   'backup_etag',backup_etag) ORDER BY item_index) FILTER(WHERE phase='completed') INTO all_done,done
 FROM public.agent_task_file_deletions WHERE native_invocation_id=f.native_invocation_id;
 UPDATE public.agent_task_native_writes SET phase=CASE WHEN all_done THEN 'returned' ELSE 'dispatched' END,
   response=jsonb_build_object('method','file_delete','body',jsonb_build_object('success',all_done,'deleted',done)),updated_at=now()
 WHERE operation_id=p_operation_id;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.complete_agent_task_file_delete_item(text,uuid,int,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.complete_agent_task_file_delete_item(text,uuid,int,jsonb) TO everydayai;

CREATE FUNCTION public.reconcile_agent_task_file_delete(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,
 p_operation_id text,p_item_index int,p_evidence jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; r public.agent_task_native_writes%ROWTYPE;
BEGIN
 checked:=public.read_agent_task_file_delete(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
 PERFORM public.complete_agent_task_file_delete_item(p_operation_id,r.dispatch_token,p_item_index,p_evidence);
 RETURN public.read_agent_task_file_delete(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
END $$;
REVOKE ALL ON FUNCTION public.reconcile_agent_task_file_delete(uuid,uuid,uuid,bigint,text,int,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reconcile_agent_task_file_delete(uuid,uuid,uuid,bigint,text,int,jsonb) TO everydayai;
RESET ROLE;
