-- AI journal bodies remain ordinary, user-editable personal knowledge objects.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.kb_ai_branches (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actor_id uuid NOT NULL REFERENCES public.users(id),
 org_id uuid REFERENCES public.organizations(id), container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 root_id uuid REFERENCES public.kb_objects(id), categories jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE NULLS NOT DISTINCT(actor_id,org_id)
);
CREATE TABLE public.kb_ai_entries (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), branch_id uuid NOT NULL REFERENCES public.kb_ai_branches(id),
 entry_key text NOT NULL CHECK(length(entry_key) BETWEEN 1 AND 120),
 category text NOT NULL CHECK(category IN ('preference','method','work')),
 object_id uuid REFERENCES public.kb_objects(id), body_hash text NOT NULL,
 verification text NOT NULL CHECK(verification IN ('explicit','candidate','verified','observed','invalid')),
 source jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(branch_id,entry_key)
);
CREATE TABLE public.kb_ai_epoch (id boolean PRIMARY KEY DEFAULT true CHECK(id), started_at timestamptz NOT NULL DEFAULT now());
INSERT INTO public.kb_ai_epoch DEFAULT VALUES;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['kb_ai_branches','kb_ai_entries','kb_ai_epoch'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY ai_owner ON public.%I TO everydayai_owner USING(true) WITH CHECK(true)',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,everydayai,everydayai_runtime',t);
 END LOOP;
END $$;
CREATE POLICY ai_branch_read ON public.kb_ai_branches FOR SELECT TO everydayai,everydayai_runtime
 USING(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_can(container_id,'read'));
CREATE POLICY ai_entry_read ON public.kb_ai_entries FOR SELECT TO everydayai,everydayai_runtime
 USING(EXISTS(SELECT 1 FROM public.kb_ai_branches b WHERE b.id=branch_id));
GRANT SELECT ON public.kb_ai_branches,public.kb_ai_entries TO everydayai,everydayai_runtime;

CREATE FUNCTION public.kb_ai_inside(p_object uuid,p_root uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 WITH RECURSIVE parents AS (
  SELECT id,parent_id,container_id,ARRAY[id] AS visited FROM public.kb_objects
   WHERE id=p_object AND deleted_at IS NULL AND NOT undo_hidden
  UNION ALL SELECT o.id,o.parent_id,o.container_id,p.visited||o.id FROM public.kb_objects o JOIN parents p ON p.parent_id=o.id
   WHERE o.deleted_at IS NULL AND NOT o.undo_hidden AND NOT o.id=ANY(p.visited)
 ) SELECT EXISTS(SELECT 1 FROM parents WHERE id=p_root AND public.kb_can(container_id,'write'));
$$;

CREATE FUNCTION public.kb_ai_ensure_branch() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 b public.kb_ai_branches%ROWTYPE; cid uuid; root uuid; child uuid; name text; k text; cats jsonb:='{}'; result jsonb; x float8; y float8; i int:=0;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-ai:'||actor::text||COALESCE(org::text,''),0));
 SELECT * INTO b FROM public.kb_ai_branches WHERE actor_id=actor AND org_id IS NOT DISTINCT FROM org;
 IF FOUND THEN
  IF NOT EXISTS(SELECT 1 FROM public.kb_objects o WHERE o.id=b.root_id AND o.parent_id IS NULL
    AND o.deleted_at IS NULL AND NOT o.undo_hidden AND public.kb_can(o.container_id,'write')) THEN RETURN NULL; END IF;
  RETURN to_jsonb(b);
 END IF;
 PERFORM public.kb_initialize();
 SELECT id INTO cid FROM public.kb_containers WHERE kind='personal' AND owner_id=actor AND org_id IS NOT DISTINCT FROM org
   AND deleted_at IS NULL AND NOT undo_hidden;
 IF cid IS NULL THEN RETURN NULL; END IF;
 PERFORM 1 FROM public.kb_containers WHERE id=cid FOR UPDATE;
 SELECT COALESCE(max(n.x),0)+600,COALESCE(min(n.y),0) INTO x,y FROM public.kb_canvas_nodes n
 JOIN public.kb_objects o ON o.id=n.object_id WHERE n.container_id=cid AND NOT o.undo_hidden AND o.deleted_at IS NULL;
 x:=LEAST(x,99000);y:=GREATEST(-99000,LEAST(y,99000));
 result:=public.kb_canvas_command(gen_random_uuid(),'create_object',jsonb_build_object('container_id',cid,'kind','topic',
  'title','AI 工作记录与使用习惯','body','','parent_id',NULL,'position',jsonb_build_object('x',x,'y',y)));
 root:=(result->>'object_id')::uuid;
 FOREACH k IN ARRAY ARRAY['preference','method','work'] LOOP
  name:=CASE k WHEN 'preference' THEN '使用习惯' WHEN 'method' THEN '工作方法' ELSE '工作情况' END;
  result:=public.kb_canvas_command(gen_random_uuid(),'create_object',jsonb_build_object('container_id',cid,'parent_id',root,
   'kind','topic','title',name,'body','','position',jsonb_build_object('x',x+320,'y',y+i*180)));
  child:=(result->>'object_id')::uuid; cats:=cats||jsonb_build_object(k,child);i:=i+1;
 END LOOP;
 INSERT INTO public.kb_ai_branches(actor_id,org_id,container_id,root_id,categories) VALUES(actor,org,cid,root,cats) RETURNING * INTO b;
 RETURN to_jsonb(b);
END $$;

-- Task facts are already durable. The scanner materializes bounded, recoverable
-- work after terminal transitions, without adding a failing trigger to commits.
SET LOCAL ROLE everydayai;
CREATE TABLE public.kb_ai_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_id uuid NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
 actor_id uuid NOT NULL REFERENCES public.users(id), org_id uuid,
 task_status text NOT NULL CHECK(task_status IN ('completed','failed','cancelled','paused')),
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','processing','done','skipped','failed','blocked')),
 token uuid, lease_until timestamptz, attempts int NOT NULL DEFAULT 0,
 retry_at timestamptz NOT NULL DEFAULT now(), error_code text, outcome jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(task_id,task_status)
);
CREATE INDEX kb_ai_events_due ON public.kb_ai_events(retry_at,id) WHERE state IN ('queued','failed');
CREATE INDEX kb_ai_events_expired ON public.kb_ai_events(lease_until,id) WHERE state='processing';
CREATE INDEX kb_ai_tasks_terminal ON public.tasks(updated_at,id) WHERE status IN ('completed','failed','cancelled','paused');
ALTER TABLE public.kb_ai_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_ai_events FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_event_owner ON public.kb_ai_events TO everydayai USING(true) WITH CHECK(true);
CREATE POLICY ai_event_reader ON public.kb_ai_events FOR SELECT TO everydayai_runtime
 USING(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND
 org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid());
REVOKE ALL ON public.kb_ai_events FROM PUBLIC,everydayai_runtime;
GRANT SELECT ON public.kb_ai_events TO everydayai_runtime;
SET LOCAL ROLE everydayai_owner;
GRANT SELECT ON public.kb_ai_epoch TO everydayai;
CREATE POLICY ai_epoch_scanner ON public.kb_ai_epoch FOR SELECT TO everydayai USING(true);
SET LOCAL ROLE everydayai;

CREATE FUNCTION public.kb_ai_candidates() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_setting('app.access_kind',true) IS DISTINCT FROM 'worker' OR NULLIF(current_setting('app.actor_user_id',true),'') IS NOT NULL
 THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 INSERT INTO public.kb_ai_events(task_id,actor_id,org_id,task_status)
 SELECT t.id,c.user_id,c.org_id,t.status FROM public.tasks t JOIN public.conversations c ON c.id=t.conversation_id
 WHERE ((COALESCE(current_setting('app.knowledge_users',true),'')='' AND COALESCE(current_setting('app.knowledge_orgs',true),'')='') OR c.user_id::text=ANY(string_to_array(current_setting('app.knowledge_users',true),',')) OR c.org_id::text=ANY(string_to_array(current_setting('app.knowledge_orgs',true),','))) AND t.status IN ('completed','failed','cancelled','paused') AND t.updated_at>=(SELECT started_at FROM public.kb_ai_epoch)
 AND COALESCE(c.scope_type,'user')='user' AND c.user_id IS NOT NULL AND c.user_id=t.user_id
 AND c.org_id IS NOT DISTINCT FROM t.org_id AND t.input_message_id IS NOT NULL
 AND EXISTS(SELECT 1 FROM public.users u WHERE u.id=c.user_id AND u.status='active')
 AND (c.org_id IS NULL OR EXISTS(SELECT 1 FROM public.org_members m WHERE m.user_id=c.user_id AND m.org_id=c.org_id AND m.status='active'))
 AND NOT EXISTS(SELECT 1 FROM public.kb_ai_events e WHERE e.task_id=t.id AND e.task_status=t.status)
 ORDER BY t.updated_at LIMIT 50 ON CONFLICT DO NOTHING;
 RETURN (SELECT COALESCE(jsonb_agg(to_jsonb(q)),'[]') FROM (
  SELECT e.id,e.actor_id,e.org_id FROM public.kb_ai_events e
  WHERE ((COALESCE(current_setting('app.knowledge_users',true),'')='' AND COALESCE(current_setting('app.knowledge_orgs',true),'')='') OR e.actor_id::text=ANY(string_to_array(current_setting('app.knowledge_users',true),',')) OR e.org_id::text=ANY(string_to_array(current_setting('app.knowledge_orgs',true),','))) AND ((e.state IN ('queued','failed') AND e.retry_at<=now()) OR (e.state='processing' AND e.lease_until<now())) AND e.attempts<5
  ORDER BY e.retry_at,e.id LIMIT 50) q);
END $$;

CREATE FUNCTION public.kb_ai_claim(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e public.kb_ai_events%ROWTYPE; t public.tasks%ROWTYPE; input_text text; output_text text; tools jsonb;
BEGIN
 IF NOT public.kb_actor_valid() THEN RETURN NULL; END IF;
 SELECT * INTO e FROM public.kb_ai_events WHERE id=p_id AND actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid FOR UPDATE;
 IF NOT FOUND OR e.attempts>=5 OR NOT ((e.state IN ('queued','failed') AND e.retry_at<=now()) OR (e.state='processing' AND e.lease_until<now())) THEN RETURN NULL; END IF;
 SELECT t0.* INTO t FROM public.tasks t0 JOIN public.conversations c ON c.id=t0.conversation_id
 WHERE t0.id=e.task_id AND t0.user_id=e.actor_id AND t0.org_id IS NOT DISTINCT FROM e.org_id AND c.user_id=e.actor_id AND COALESCE(c.scope_type,'user')='user' AND c.org_id IS NOT DISTINCT FROM e.org_id;
 IF NOT FOUND OR t.status<>e.task_status THEN UPDATE public.kb_ai_events SET state='skipped',finished_at=now() WHERE id=e.id; RETURN NULL; END IF;
 SELECT content INTO input_text FROM public.messages WHERE id=t.input_message_id AND conversation_id=t.conversation_id AND role='user';
 SELECT content INTO output_text FROM public.messages WHERE id=t.assistant_message_id AND conversation_id=t.conversation_id AND role='assistant';
 SELECT COALESCE(jsonb_agg(jsonb_build_object('name',tool_name,'status',status)),'[]') INTO tools
 FROM (SELECT tool_name,status FROM public.tool_audit_log WHERE task_id=e.task_id::text ORDER BY created_at DESC LIMIT 20) q;
 UPDATE public.kb_ai_events SET state='processing',token=gen_random_uuid(),lease_until=now()+interval '4 minutes',attempts=attempts+1 WHERE id=e.id RETURNING * INTO e;
 RETURN to_jsonb(e)||jsonb_build_object('conversation_id',t.conversation_id,'input_message_id',t.input_message_id,
 'input',left(COALESCE(input_text,''),200000),'output',left(COALESCE(output_text,''),200000),'tools',tools);
END $$;

CREATE FUNCTION public.kb_ai_finish(p_id uuid,p_token uuid,p_state text,p_error text DEFAULT NULL,p_outcome jsonb DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF p_state NOT IN ('done','skipped','failed') OR NOT public.kb_actor_valid() THEN RETURN false; END IF;
 UPDATE public.kb_ai_events SET state=CASE WHEN p_state='failed' AND attempts>=5 THEN 'blocked' ELSE p_state END,
  error_code=left(p_error,80),outcome=p_outcome,token=NULL,lease_until=NULL,retry_at=now()+interval '1 minute',finished_at=CASE WHEN p_state IN ('done','skipped') THEN now() END
 WHERE id=p_id AND actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
 AND state='processing' AND token=p_token AND lease_until>now();
 RETURN FOUND;
END $$;

CREATE FUNCTION public.kb_ai_event_valid(p_id uuid,p_token uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.kb_actor_valid() THEN RETURN false; END IF;
 PERFORM t.id FROM public.kb_ai_events e JOIN public.tasks t ON t.id=e.task_id
 JOIN public.conversations c ON c.id=t.conversation_id
 WHERE e.id=p_id AND e.actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 AND e.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
 AND e.state='processing' AND e.token=p_token AND e.lease_until>now() AND t.status=e.task_status AND t.user_id=e.actor_id AND t.org_id IS NOT DISTINCT FROM e.org_id
 AND c.user_id=e.actor_id AND c.org_id IS NOT DISTINCT FROM e.org_id AND COALESCE(c.scope_type,'user')='user' FOR SHARE OF e,t;
 RETURN FOUND;
END $$;
GRANT SELECT ON public.kb_ai_events TO everydayai_owner;
CREATE POLICY ai_event_definer ON public.kb_ai_events FOR SELECT TO everydayai_owner USING(true);
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION public.kb_ai_upsert_entry(p_key text,p_category text,p_title text,p_body text,p_verification text,p_source jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE b public.kb_ai_branches%ROWTYPE; prior public.kb_ai_entries%ROWTYPE;
 parent uuid; obj public.kb_objects%ROWTYPE; body text; result jsonb; oid uuid; x float8; y float8;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_category NOT IN ('preference','method','work') OR p_verification NOT IN ('explicit','candidate','verified','observed','invalid')
 OR length(p_key) NOT BETWEEN 1 AND 120 OR length(btrim(p_title)) NOT BETWEEN 1 AND 200 OR length(p_body) NOT BETWEEN 1 AND 6000
 THEN RAISE EXCEPTION 'KB_AI_INVALID_ENTRY' USING ERRCODE='22023'; END IF;
 result:=public.kb_ai_ensure_branch(); IF result IS NULL THEN RETURN jsonb_build_object('status','branch_unavailable'); END IF;
 SELECT * INTO b FROM public.kb_ai_branches WHERE id=(result->>'id')::uuid;
 PERFORM 1 FROM public.kb_containers WHERE id=b.container_id FOR UPDATE;
 parent:=(b.categories->>p_category)::uuid;
 IF NOT public.kb_ai_inside(parent,b.root_id) THEN RETURN jsonb_build_object('status','branch_unavailable'); END IF;
 SELECT * INTO prior FROM public.kb_ai_entries WHERE branch_id=b.id AND entry_key=p_key FOR UPDATE;
 IF FOUND THEN
  SELECT * INTO obj FROM public.kb_objects WHERE id=prior.object_id AND deleted_at IS NULL AND NOT undo_hidden;
  IF NOT FOUND OR NOT public.kb_ai_inside(prior.object_id,parent) THEN RETURN jsonb_build_object('status','user_removed'); END IF;
  SELECT v.body INTO body FROM public.kb_object_versions v WHERE v.object_id=obj.id AND v.revision=obj.current_revision;
  IF md5(body)<>prior.body_hash THEN RETURN jsonb_build_object('status','user_edited'); END IF;
  IF body=p_body THEN RETURN jsonb_build_object('status','unchanged','object_id',obj.id); END IF;
  result:=public.kb_canvas_command(gen_random_uuid(),'save_document',jsonb_build_object('object_id',obj.id,'expected_revision',obj.current_revision,'body',p_body));
  oid:=obj.id;
 ELSE
  -- Identical content under a different proposed key is not another knowledge entry.
  IF EXISTS(SELECT 1 FROM public.kb_ai_entries WHERE branch_id=b.id AND category=p_category AND body_hash=md5(p_body)) THEN RETURN jsonb_build_object('status','duplicate'); END IF;
  SELECT n.x+320,n.y+100+COALESCE((SELECT count(*) FROM public.kb_objects o WHERE o.parent_id=parent AND o.deleted_at IS NULL AND NOT o.undo_hidden),0)*100 INTO x,y FROM public.kb_canvas_nodes n WHERE n.object_id=parent;
  result:=public.kb_canvas_command(gen_random_uuid(),'create_object',jsonb_build_object('container_id',b.container_id,'parent_id',parent,
   'kind','document','title',p_title,'body',p_body,'position',jsonb_build_object('x',LEAST(x,99999),'y',LEAST(y,99999))));
  oid:=(result->>'object_id')::uuid;
 END IF;
 INSERT INTO public.kb_ai_entries(branch_id,entry_key,category,object_id,body_hash,verification,source)
 VALUES(b.id,p_key,p_category,oid,md5(p_body),p_verification,p_source)
 ON CONFLICT(branch_id,entry_key) DO UPDATE SET body_hash=excluded.body_hash,verification=excluded.verification,source=excluded.source,updated_at=now();
 RETURN jsonb_build_object('status','saved','object_id',oid);
END $$;

-- Runtime callers can only enter via an owned live event or an owned legacy source.
REVOKE ALL ON FUNCTION public.kb_ai_upsert_entry(text,text,text,text,text,jsonb) FROM PUBLIC;
CREATE FUNCTION public.kb_ai_apply_entry(p_event uuid,p_token uuid,p_key text,p_category text,p_title text,p_body text,p_verification text,p_source jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.kb_ai_event_valid(p_event,p_token) THEN RAISE EXCEPTION 'KB_AI_LEASE_LOST' USING ERRCODE='42501'; END IF;
 RETURN public.kb_ai_upsert_entry(p_key,p_category,p_title,p_body,p_verification,p_source);
END $$;

CREATE FUNCTION public.kb_ai_apply_batch(p_event uuid,p_token uuid,p_entries jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE item jsonb; results jsonb:='[]'; result jsonb;
BEGIN
 IF p_entries IS NULL OR jsonb_typeof(p_entries) IS DISTINCT FROM 'array' OR jsonb_array_length(p_entries) NOT BETWEEN 1 AND 3 THEN
  RAISE EXCEPTION 'KB_AI_INVALID_ENTRY' USING ERRCODE='22023';
 END IF;
 IF NOT public.kb_ai_event_valid(p_event,p_token) THEN RAISE EXCEPTION 'KB_AI_LEASE_LOST' USING ERRCODE='42501'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_entries) LOOP
  result:=public.kb_ai_upsert_entry(item->>'key',item->>'category',item->>'title',item->>'body',item->>'verification',item->'source');
  results:=results||jsonb_build_array(result);
 END LOOP;
 IF NOT public.kb_ai_finish(p_event,p_token,'done',NULL,results) THEN RAISE EXCEPTION 'KB_AI_LEASE_LOST' USING ERRCODE='42501'; END IF;
 RETURN results;
END $$;
REVOKE ALL ON FUNCTION public.kb_ai_apply_batch(uuid,uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_ai_apply_batch(uuid,uuid,jsonb) TO everydayai,everydayai_runtime;

SET LOCAL ROLE everydayai;
DO $$ BEGIN
 IF to_regclass('public.knowledge_nodes') IS NOT NULL THEN
  GRANT SELECT ON public.knowledge_nodes TO everydayai_owner;
 END IF;
END $$;
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.kb_ai_import_entry(p_legacy uuid,p_fingerprint text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE n record; result jsonb;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT id,title,content,scope,org_id,category,source,updated_at,
 md5(jsonb_build_array(id,title,content,scope,org_id,category,source,is_deleted,updated_at)::text) AS fingerprint INTO n FROM public.knowledge_nodes
 WHERE id=p_legacy AND NOT is_deleted;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','unavailable'); END IF;
 -- Organization/global records have no uniquely authorized personal owner.
 IF n.scope IS DISTINCT FROM 'user:'||NULLIF(current_setting('app.actor_user_id',true),'') OR n.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
 OR n.category<>'experience' THEN RETURN jsonb_build_object('status','review_required'); END IF;
 IF p_fingerprint IS DISTINCT FROM n.fingerprint THEN RETURN jsonb_build_object('status','source_changed'); END IF;
 IF length(n.content)>5000 THEN RETURN jsonb_build_object('status','oversize_review'); END IF;
 result:=public.kb_ai_upsert_entry('method:legacy:'||n.id,'method',left(n.title,160),
  '记录类型：旧库迁入的待验证方法'||E'\n\n'||n.content||E'\n\n来源：旧经验记录 '||n.id||'；原更新时间：'||n.updated_at,
  'candidate',jsonb_build_object('legacy_id',n.id,'legacy_source',n.source,'legacy_updated_at',n.updated_at));
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.kb_ai_import_entry(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_ai_import_entry(uuid,text) TO everydayai,everydayai_runtime;

REVOKE ALL ON FUNCTION public.kb_ai_inside(uuid,uuid),public.kb_ai_ensure_branch(),public.kb_ai_apply_entry(uuid,uuid,text,text,text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_ai_inside(uuid,uuid),public.kb_ai_ensure_branch(),public.kb_ai_apply_entry(uuid,uuid,text,text,text,text,text,jsonb) TO everydayai,everydayai_runtime;
REVOKE ALL ON FUNCTION public.kb_ai_candidates(),public.kb_ai_claim(uuid),public.kb_ai_finish(uuid,uuid,text,text,jsonb),public.kb_ai_event_valid(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_ai_candidates(),public.kb_ai_claim(uuid),public.kb_ai_finish(uuid,uuid,text,text,jsonb),public.kb_ai_event_valid(uuid,uuid) TO everydayai,everydayai_runtime,everydayai_owner;
RESET ROLE;
