-- Provider-observed refresh facts. No raw credentials and no business dispatch.
SET LOCAL ROLE everydayai;
CREATE TABLE public.agent_erp_session_rotations(
 org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
 old_fingerprint text NOT NULL CHECK(old_fingerprint ~ '^[0-9a-f]{64}$'),
 new_fingerprint text NOT NULL CHECK(new_fingerprint ~ '^[0-9a-f]{64}$'),
 actor_user_id uuid, observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(org_id,old_fingerprint), UNIQUE(org_id,new_fingerprint),
 CHECK(old_fingerprint<>new_fingerprint)
);
ALTER TABLE public.agent_erp_session_rotations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_erp_session_rotations FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_erp_session_rotation_scope ON public.agent_erp_session_rotations
 USING(org_id=NULLIF(current_setting('app.org_id',true),'')::uuid
   AND current_setting('app.access_kind',true) IN ('runtime','runtime_admin','worker')
   AND EXISTS(SELECT 1 FROM public.organizations o WHERE o.id=agent_erp_session_rotations.org_id AND o.status='active')
   AND (current_setting('app.access_kind',true)='worker' OR EXISTS(
     SELECT 1 FROM public.org_members m JOIN public.users u ON u.id=m.user_id
     WHERE m.org_id=agent_erp_session_rotations.org_id AND m.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       AND m.status='active' AND u.status::text='active')))
 WITH CHECK(org_id=NULLIF(current_setting('app.org_id',true),'')::uuid
   AND current_setting('app.access_kind',true) IN ('runtime','runtime_admin','worker')
   AND EXISTS(SELECT 1 FROM public.organizations o WHERE o.id=agent_erp_session_rotations.org_id AND o.status='active')
   AND (current_setting('app.access_kind',true)='worker' OR EXISTS(
     SELECT 1 FROM public.org_members m JOIN public.users u ON u.id=m.user_id
     WHERE m.org_id=agent_erp_session_rotations.org_id AND m.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       AND m.status='active' AND u.status::text='active')));
CREATE FUNCTION public.guard_agent_erp_session_rotation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN RAISE EXCEPTION 'ERP_SESSION_ROTATION_IMMUTABLE' USING ERRCODE='42501'; END $$;
REVOKE ALL ON FUNCTION public.guard_agent_erp_session_rotation() FROM PUBLIC;
CREATE TRIGGER agent_erp_session_rotation_immutable BEFORE UPDATE ON public.agent_erp_session_rotations
 FOR EACH ROW EXECUTE FUNCTION public.guard_agent_erp_session_rotation();
CREATE FUNCTION public.record_agent_erp_session_rotation(p_org_id uuid,p_old_fingerprint text,p_new_fingerprint text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE known public.agent_erp_session_rotations%ROWTYPE;
BEGIN
 IF SESSION_USER<>'everydayai' OR p_org_id IS NULL
   OR p_org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR (p_old_fingerprint ~ '^[0-9a-f]{64}$') IS NOT TRUE OR (p_new_fingerprint ~ '^[0-9a-f]{64}$') IS NOT TRUE
   OR p_old_fingerprint=p_new_fingerprint THEN RAISE EXCEPTION 'ERP_SESSION_ROTATION_DENIED' USING ERRCODE='42501'; END IF;
 INSERT INTO public.agent_erp_session_rotations(org_id,old_fingerprint,new_fingerprint,actor_user_id)
 VALUES(p_org_id,p_old_fingerprint,p_new_fingerprint,NULLIF(current_setting('app.actor_user_id',true),'')::uuid)
 ON CONFLICT(org_id,old_fingerprint) DO NOTHING;
 SELECT * INTO known FROM public.agent_erp_session_rotations WHERE org_id=p_org_id AND old_fingerprint=p_old_fingerprint;
 IF known.new_fingerprint IS DISTINCT FROM p_new_fingerprint THEN RAISE EXCEPTION 'ERP_SESSION_ROTATION_CONFLICT'; END IF;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.record_agent_erp_session_rotation(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_agent_erp_session_rotation(uuid,text,text) TO everydayai;
CREATE FUNCTION public.agent_erp_session_rotation_path(p_org_id uuid,p_old_fingerprint text,p_new_fingerprint text,p_before timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE cursor_fp text:=p_old_fingerprint; edges jsonb:='[]'::jsonb; edge public.agent_erp_session_rotations%ROWTYPE; seen text[]; prior timestamptz;
BEGIN
 IF p_org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid OR p_before IS NULL THEN RETURN NULL; END IF;
 IF p_old_fingerprint=p_new_fingerprint THEN RETURN edges; END IF;
 seen:=ARRAY[p_old_fingerprint];
 FOR depth IN 1..32 LOOP
   SELECT * INTO edge FROM public.agent_erp_session_rotations WHERE org_id=p_org_id AND old_fingerprint=cursor_fp AND observed_at<=p_before;
   IF NOT FOUND OR edge.new_fingerprint=ANY(seen) OR (prior IS NOT NULL AND edge.observed_at<prior) THEN RETURN NULL; END IF;
   edges:=edges||jsonb_build_array(jsonb_build_object('org_id',edge.org_id,'old',edge.old_fingerprint,'new',edge.new_fingerprint,'observed_at',edge.observed_at));
   IF edge.new_fingerprint=p_new_fingerprint THEN RETURN edges; END IF;
   cursor_fp:=edge.new_fingerprint;seen:=array_append(seen,cursor_fp);prior:=edge.observed_at;
 END LOOP;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.agent_erp_session_rotation_path(uuid,text,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_erp_session_rotation_path(uuid,text,text,timestamptz) TO everydayai;
RESET ROLE;

-- Read existing native facts; no extra query, mutation, user turn or credential.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.read_agent_task_erp_postcondition(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_operation_id text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; r public.agent_task_native_writes%ROWTYPE; current_task public.tasks%ROWTYPE; witness jsonb;
BEGIN
 checked:=public.read_agent_task_native_write(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 IF proof->>'domain' IS DISTINCT FROM 'erp' OR proof#>>'{dispatch,method}' IS DISTINCT FROM 'item.general.addorupdate'
   OR proof#>>'{dispatch,postcondition,kind}' IS DISTINCT FROM 'product.title' THEN
   RAISE EXCEPTION 'TASK_ERP_POSTCONDITION_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
 SELECT * INTO current_task FROM public.tasks WHERE id=p_task_id;
 IF r.phase='returned' THEN
   SELECT jsonb_build_object('id',i.id,'task_id',i.task_id,'tool_call_id',i.tool_call_id,'args_hash',i.args_hash,
     'tool_name',i.tool_name,'status',i.status,'result',i.result,
     'started_at',i.started_at,'completed_at',i.completed_at,'write_returned_at',r.updated_at) INTO witness
   FROM public.tool_invocations i JOIN public.tasks source ON source.id=i.task_id
   WHERE i.conversation_id=current_task.conversation_id AND source.conversation_id=current_task.conversation_id
     AND source.user_id=current_task.user_id AND source.org_id IS NOT DISTINCT FROM current_task.org_id AND source.type='chat'
     AND (source.id=p_task_id OR source.id=p_logical_task_id OR source.delivery_context->>'logical_task_id'=p_logical_task_id::text)
     AND i.tool_name='erp_product_query' AND i.status='succeeded' AND i.started_at>r.updated_at AND i.completed_at>=i.started_at
     AND i.result#>'{tool_result,execution}' @> '{"status":"succeeded","handler_started":true,"cancelled":false,"cached":false,"replayed":false}'::jsonb
     AND i.result#>>'{tool_result,raw,metadata,_erp_readback_v1,method}'='item.single.get'
     AND i.result#>'{tool_result,raw,metadata,_erp_readback_v1,parameters}'=
       jsonb_build_object('outerId',proof#>>'{dispatch,postcondition,outer_id}')
   ORDER BY i.started_at DESC,i.id DESC LIMIT 1;
 END IF;
 RETURN jsonb_build_object('outcome','found','proof',proof||jsonb_build_object('readback',witness,'source_rotations',
   CASE WHEN witness IS NULL THEN NULL ELSE public.agent_erp_session_rotation_path(current_task.org_id,
     proof#>>'{dispatch,postcondition,source_fingerprint}',witness#>>'{result,tool_result,raw,metadata,_erp_readback_v1,source_fingerprint}',
     (witness->>'completed_at')::timestamptz) END));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_erp_postcondition(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_erp_postcondition(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
