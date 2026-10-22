-- Additive V1 knowledge workspace. No changes to agent knowledge or detail projects.
-- Existing identity tables belong to everydayai. The workspace owner needs
-- only REFERENCES on their keys to create the scoped foreign keys below.
SET LOCAL ROLE everydayai;
GRANT REFERENCES (id) ON public.organizations, public.users TO everydayai_owner;
SET LOCAL ROLE everydayai_owner;
CREATE TABLE public.kb_containers (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid REFERENCES public.organizations(id),
 kind text NOT NULL CHECK(kind IN ('personal','project','team')), owner_id uuid NOT NULL REFERENCES public.users(id),
 title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 200), revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
 acl_revision bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz,
 CHECK(kind='personal' OR org_id IS NOT NULL), UNIQUE(org_id,id)
);
CREATE UNIQUE INDEX kb_personal_unique ON public.kb_containers(org_id,owner_id) NULLS NOT DISTINCT WHERE kind='personal';
CREATE UNIQUE INDEX kb_team_unique ON public.kb_containers(org_id) WHERE kind='team';
CREATE TABLE public.kb_projects (
 container_id uuid PRIMARY KEY REFERENCES public.kb_containers(id), org_id uuid NOT NULL REFERENCES public.organizations(id),
 owner_id uuid NOT NULL REFERENCES public.users(id), status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
 summary text NOT NULL DEFAULT ''
);
CREATE TABLE public.kb_container_members (
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), org_id uuid NOT NULL REFERENCES public.organizations(id),
 user_id uuid NOT NULL REFERENCES public.users(id), role text NOT NULL CHECK(role IN ('viewer','editor','manager')),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','removed')), PRIMARY KEY(container_id,user_id)
);
CREATE TABLE public.kb_objects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid REFERENCES public.organizations(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), parent_id uuid,
 kind text NOT NULL CHECK(kind IN ('topic','document','rule')), title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 200),
 current_revision bigint NOT NULL DEFAULT 1 CHECK(current_revision>0), created_by uuid NOT NULL REFERENCES public.users(id),
 deleted_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(container_id,id),
 FOREIGN KEY(container_id,parent_id) REFERENCES public.kb_objects(container_id,id), CHECK(parent_id IS DISTINCT FROM id)
);
CREATE INDEX kb_objects_tree ON public.kb_objects(container_id,parent_id,id);
CREATE TABLE public.kb_object_versions (
 object_id uuid NOT NULL REFERENCES public.kb_objects(id), container_id uuid NOT NULL REFERENCES public.kb_containers(id),
 org_id uuid REFERENCES public.organizations(id), revision bigint NOT NULL CHECK(revision>0),
 title text NOT NULL, body text NOT NULL DEFAULT '' CHECK(length(body)<=200000), parent_id uuid,
 created_by uuid NOT NULL REFERENCES public.users(id), created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(object_id,revision)
);
ALTER TABLE public.kb_objects ADD CONSTRAINT kb_current_version FOREIGN KEY(id,current_revision)
 REFERENCES public.kb_object_versions(object_id,revision) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE public.kb_canvas_nodes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid REFERENCES public.organizations(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), object_id uuid NOT NULL UNIQUE REFERENCES public.kb_objects(id),
 x double precision NOT NULL, y double precision NOT NULL, revision bigint NOT NULL DEFAULT 1,
 CHECK(x BETWEEN -1000000 AND 1000000 AND y BETWEEN -1000000 AND 1000000)
);
CREATE TABLE public.kb_relations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid REFERENCES public.organizations(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), source_object_id uuid NOT NULL REFERENCES public.kb_objects(id),
 target_object_id uuid NOT NULL REFERENCES public.kb_objects(id), relation_type text NOT NULL CHECK(relation_type IN ('reference','related','dependency')),
 UNIQUE(source_object_id,target_object_id,relation_type), CHECK(source_object_id<>target_object_id)
);
CREATE TABLE public.kb_mutation_receipts (
 actor_id uuid NOT NULL REFERENCES public.users(id), command_id uuid NOT NULL, org_id uuid REFERENCES public.organizations(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), request_payload jsonb NOT NULL, result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(actor_id,command_id)
);
CREATE TABLE public.kb_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid REFERENCES public.organizations(id),
 container_id uuid NOT NULL REFERENCES public.kb_containers(id), actor_id uuid NOT NULL REFERENCES public.users(id),
 command_id uuid NOT NULL, operation text NOT NULL, revision bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_events_cursor ON public.kb_events(container_id,revision);

-- Only the definer role writes. Runtime policies never invoke themselves via a
-- membership lookup: the helper reads owner-only policies as everydayai_owner.
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['kb_containers','kb_projects','kb_container_members','kb_objects','kb_object_versions',
  'kb_canvas_nodes','kb_relations','kb_mutation_receipts','kb_events'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY kb_internal ON public.%I TO everydayai_owner USING(true) WITH CHECK(true)',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,everydayai,everydayai_runtime',t);
  EXECUTE format('GRANT SELECT ON public.%I TO everydayai,everydayai_runtime',t);
 END LOOP;
END $$;
CREATE FUNCTION public.kb_actor_valid() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT 1 FROM public.users u WHERE u.id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND u.status='active')
 AND (NULLIF(current_setting('app.org_id',true),'') IS NULL OR public.reach_active_member(NULLIF(current_setting('app.org_id',true),'')::uuid));
$$;
CREATE FUNCTION public.kb_role(p_container uuid) RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT CASE WHEN c.kind='personal' AND c.owner_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid THEN 'manager'
 WHEN c.kind='personal' THEN NULL
 WHEN c.kind='team' AND public.reach_active_member(c.org_id,true) THEN 'manager'
 ELSE COALESCE((SELECT m.role FROM public.kb_container_members m WHERE m.container_id=c.id
  AND m.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND m.status='active'),
  CASE WHEN c.kind='team' THEN 'viewer' END) END
 FROM public.kb_containers c WHERE c.id=p_container AND c.deleted_at IS NULL
 AND c.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND public.kb_actor_valid();
$$;
CREATE FUNCTION public.kb_can(p_container uuid,p_action text) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(CASE p_action WHEN 'read' THEN public.kb_role(p_container) IN ('viewer','editor','manager')
 WHEN 'write' THEN public.kb_role(p_container) IN ('editor','manager') WHEN 'manage' THEN public.kb_role(p_container)='manager' ELSE false END,false);
$$;
CREATE POLICY kb_read ON public.kb_containers FOR SELECT TO everydayai,everydayai_runtime USING(public.kb_can(id,'read'));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['kb_projects','kb_container_members','kb_objects','kb_object_versions','kb_canvas_nodes','kb_relations','kb_events'] LOOP
  EXECUTE format('CREATE POLICY kb_read ON public.%I FOR SELECT TO everydayai,everydayai_runtime USING(public.kb_can(container_id,''read''))',t);
 END LOOP;
END $$;
CREATE POLICY kb_receipt_read ON public.kb_mutation_receipts FOR SELECT TO everydayai,everydayai_runtime
 USING(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND public.kb_can(container_id,'read'));

CREATE FUNCTION public.kb_initialize() RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('kb-init:'||COALESCE(org::text,'')||actor::text,0));
 INSERT INTO public.kb_containers(org_id,kind,owner_id,title) VALUES(org,'personal',actor,'个人知识库') ON CONFLICT DO NOTHING;
 IF org IS NOT NULL THEN
  INSERT INTO public.kb_containers(org_id,kind,owner_id,title) VALUES(org,'team',actor,'团队知识库') ON CONFLICT DO NOTHING;
 END IF;
END $$;

CREATE FUNCTION public.kb_apply_command(p_command_id uuid,p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org uuid:=NULLIF(current_setting('app.org_id',true),'')::uuid;
 cid uuid; oid uuid; pid uuid; target uuid; event_id uuid:=gen_random_uuid(); rev bigint; old public.kb_objects%ROWTYPE;
 prior public.kb_mutation_receipts%ROWTYPE; v_title text; v_body text; response jsonb; node jsonb; full_request jsonb;
BEGIN
 IF NOT public.kb_actor_valid() THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 -- Serialize identical commands before looking up receipts, including creates.
 PERFORM pg_advisory_xact_lock(hashtextextended(actor::text||p_command_id::text,0));
 full_request:=jsonb_build_object('operation',p_operation,'payload',p_payload);
 SELECT * INTO prior FROM public.kb_mutation_receipts WHERE actor_id=actor AND command_id=p_command_id;
 IF FOUND THEN
  IF prior.org_id IS DISTINCT FROM org OR prior.request_payload<>full_request THEN RAISE EXCEPTION 'KB_IDEMPOTENCY_PAYLOAD_CONFLICT' USING ERRCODE='P0001'; END IF;
  IF NOT public.kb_can(prior.container_id,'read') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  RETURN prior.result||jsonb_build_object('status','replayed');
 END IF;
 IF p_operation='create_project' THEN
  IF org IS NULL THEN RAISE EXCEPTION 'KB_ORG_REQUIRED' USING ERRCODE='42501'; END IF;
  INSERT INTO public.kb_containers(org_id,kind,owner_id,title) VALUES(org,'project',actor,p_payload->>'title') RETURNING id INTO cid;
  INSERT INTO public.kb_projects(container_id,org_id,owner_id) VALUES(cid,org,actor);
  INSERT INTO public.kb_container_members(container_id,org_id,user_id,role) VALUES(cid,org,actor,'manager');
 ELSIF p_operation='create_object' OR p_operation='set_member' THEN cid:=(p_payload->>'container_id')::uuid;
 ELSIF p_operation='move_nodes' THEN
  SELECT container_id INTO cid FROM public.kb_canvas_nodes WHERE id=(p_payload->'nodes'->0->>'node_id')::uuid;
 ELSIF p_operation='connect' THEN
  SELECT container_id INTO cid FROM public.kb_objects WHERE id=(p_payload->>'source_object_id')::uuid AND deleted_at IS NULL;
 ELSE
  SELECT container_id INTO cid FROM public.kb_objects WHERE id=(p_payload->>'object_id')::uuid AND deleted_at IS NULL;
 END IF;
 IF cid IS NULL OR NOT public.kb_can(cid,'read') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.kb_containers WHERE id=cid FOR UPDATE;
 IF NOT public.kb_can(cid,'write') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_operation IN ('create_project','create_object') THEN
  pid:=(p_payload->>'parent_id')::uuid;
  IF pid IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=pid AND container_id=cid AND deleted_at IS NULL) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  INSERT INTO public.kb_objects(org_id,container_id,parent_id,kind,title,created_by)
   VALUES(org,cid,pid,CASE WHEN p_operation='create_project' THEN 'topic' ELSE p_payload->>'kind' END,p_payload->>'title',actor) RETURNING id INTO oid;
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
   VALUES(oid,cid,org,1,p_payload->>'title',COALESCE(p_payload->>'body',''),pid,actor);
  INSERT INTO public.kb_canvas_nodes(org_id,container_id,object_id,x,y)
   VALUES(org,cid,oid,(p_payload->'position'->>'x')::float8,(p_payload->'position'->>'y')::float8);
 ELSIF p_operation IN ('save_document','update_title','reparent') THEN
  oid:=(p_payload->>'object_id')::uuid;
  SELECT * INTO old FROM public.kb_objects WHERE id=oid AND container_id=cid;
  IF old.current_revision<>(p_payload->>'expected_revision')::bigint THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  SELECT v.title,v.body INTO v_title,v_body FROM public.kb_object_versions v WHERE v.object_id=oid AND v.revision=old.current_revision;
  pid:=old.parent_id;
  IF p_operation='save_document' THEN v_body:=p_payload->>'body'; END IF;
  IF p_operation='update_title' THEN v_title:=p_payload->>'title'; END IF;
  IF p_operation='reparent' THEN
   pid:=(p_payload->>'parent_id')::uuid;
   IF pid IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=pid AND container_id=cid AND deleted_at IS NULL) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
   IF EXISTS(WITH RECURSIVE tree AS (SELECT id FROM public.kb_objects WHERE id=oid UNION ALL SELECT o.id FROM public.kb_objects o JOIN tree t ON o.parent_id=t.id) SELECT 1 FROM tree WHERE id=pid) THEN RAISE EXCEPTION 'KB_TREE_CYCLE' USING ERRCODE='22023'; END IF;
  END IF;
  UPDATE public.kb_objects SET title=v_title, parent_id=pid,current_revision=old.current_revision+1 WHERE id=oid;
  INSERT INTO public.kb_object_versions(object_id,container_id,org_id,revision,title,body,parent_id,created_by)
   VALUES(oid,cid,org,old.current_revision+1,v_title,v_body,pid,actor);
 ELSIF p_operation='move_nodes' THEN
  FOR node IN SELECT * FROM jsonb_array_elements(p_payload->'nodes') LOOP
   UPDATE public.kb_canvas_nodes SET x=(node->'position'->>'x')::float8,y=(node->'position'->>'y')::float8,revision=revision+1
   WHERE id=(node->>'node_id')::uuid AND container_id=cid AND revision=(node->>'expected_revision')::bigint;
   IF NOT FOUND THEN RAISE EXCEPTION 'KB_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
  END LOOP;
 ELSIF p_operation='connect' THEN
  oid:=(p_payload->>'source_object_id')::uuid; target:=(p_payload->>'target_object_id')::uuid;
  IF NOT EXISTS(SELECT 1 FROM public.kb_objects WHERE id=target AND container_id=cid AND deleted_at IS NULL) THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  INSERT INTO public.kb_relations(org_id,container_id,source_object_id,target_object_id,relation_type)
   VALUES(org,cid,oid,target,p_payload->>'relation_type') ON CONFLICT DO NOTHING;
 ELSIF p_operation='set_member' THEN
  IF NOT public.kb_can(cid,'manage') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  target:=(p_payload->>'user_id')::uuid;
  IF NOT EXISTS(SELECT 1 FROM public.org_members WHERE user_id=target AND org_id=org AND status='active') THEN RAISE EXCEPTION 'KB_NOT_FOUND' USING ERRCODE='P0002'; END IF;
  IF EXISTS(SELECT 1 FROM public.kb_containers WHERE id=cid AND kind='personal') THEN RAISE EXCEPTION 'KB_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
  IF target=actor THEN RAISE EXCEPTION 'KB_SELF_ROLE_CHANGE' USING ERRCODE='22023'; END IF;
  INSERT INTO public.kb_container_members(container_id,org_id,user_id,role,status) VALUES(cid,org,target,p_payload->>'role',p_payload->>'status')
   ON CONFLICT(container_id,user_id) DO UPDATE SET role=EXCLUDED.role,status=EXCLUDED.status;
  UPDATE public.kb_containers SET acl_revision=acl_revision+1 WHERE id=cid;
 ELSE RAISE EXCEPTION 'KB_OPERATION_UNSUPPORTED' USING ERRCODE='22023';
 END IF;
 UPDATE public.kb_containers SET revision=revision+1 WHERE id=cid RETURNING revision INTO rev;
 INSERT INTO public.kb_events(id,org_id,container_id,actor_id,command_id,operation,revision) VALUES(event_id,org,cid,actor,p_command_id,p_operation,rev);
 response:=jsonb_build_object('command_id',p_command_id,'status','applied','object_id',oid,'revision',rev,'event_id',event_id);
 INSERT INTO public.kb_mutation_receipts(actor_id,command_id,org_id,container_id,request_payload,result) VALUES(actor,p_command_id,org,cid,full_request,response);
 RETURN response;
END $$;
REVOKE ALL ON FUNCTION public.kb_actor_valid(),public.kb_role(uuid),public.kb_can(uuid,text),public.kb_initialize(),public.kb_apply_command(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.kb_actor_valid(),public.kb_role(uuid),public.kb_can(uuid,text),public.kb_initialize(),public.kb_apply_command(uuid,text,jsonb) TO everydayai,everydayai_runtime;
RESET ROLE;
