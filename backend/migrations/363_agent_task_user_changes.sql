-- Preserve original authenticated steer and full Actor checkpoint atomically.
-- Delivery is not authorization to alter requirements or repeat an effect.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.guard_agent_task_user_changes() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE oldbox jsonb; box jsonb:=NEW.state->'_task_user_changes'; owner public.tasks%ROWTYPE;
 item jsonb; prior jsonb; e public.conversation_control_events%ROWTYPE; src public.tasks%ROWTYPE; root uuid; ordinal bigint;
BEGIN
 IF TG_OP='UPDATE' THEN oldbox:=OLD.state->'_task_user_changes'; END IF;
 IF box IS NOT DISTINCT FROM oldbox THEN RETURN NEW; END IF;
 IF box IS NULL OR (box-ARRAY['version','root_task_id','items'])<>'{}'::jsonb OR box->'version' IS DISTINCT FROM '1'::jsonb OR jsonb_typeof(box->'items') IS DISTINCT FROM 'array'
    OR jsonb_array_length(box->'items')>32 OR octet_length(box::text)>300000 THEN
   RAISE EXCEPTION 'TASK_USER_CHANGE_INBOX_INVALID' USING ERRCODE='42501';
 END IF;
 SELECT * INTO owner FROM public.tasks WHERE id=NEW.task_id;
 root:=COALESCE((NEW.state#>>'{_task_execution,root_task_id}')::uuid,(NEW.state#>>'{_task_bootstrap,input,task_id}')::uuid);
 IF root IS NULL OR box->>'root_task_id' IS DISTINCT FROM root::text OR owner.id IS NULL THEN
   RAISE EXCEPTION 'TASK_USER_CHANGE_SCOPE_INVALID' USING ERRCODE='42501';
 END IF;
 IF oldbox IS NOT NULL AND (oldbox->>'root_task_id' IS DISTINCT FROM box->>'root_task_id'
   OR jsonb_array_length(box->'items')<jsonb_array_length(oldbox->'items')) THEN
   RAISE EXCEPTION 'TASK_USER_CHANGE_HISTORY_IMMUTABLE' USING ERRCODE='42501';
 END IF;
 IF (SELECT count(*)<>count(DISTINCT value->>'event_id') FROM jsonb_array_elements(box->'items')) THEN
   RAISE EXCEPTION 'TASK_USER_CHANGE_INBOX_INVALID' USING ERRCODE='42501';
 END IF;
 FOR item,ordinal IN SELECT value, ordinality FROM jsonb_array_elements(box->'items') WITH ORDINALITY LOOP
   prior:=oldbox->'items'->((ordinal-1)::integer);
   IF prior IS NOT NULL AND ((prior-'delivered') IS DISTINCT FROM (item-'delivered')
       OR (prior->'delivered'='true'::jsonb AND item->'delivered' IS DISTINCT FROM 'true'::jsonb)) THEN
     RAISE EXCEPTION 'TASK_USER_CHANGE_HISTORY_IMMUTABLE' USING ERRCODE='42501';
   END IF;
   SELECT * INTO e FROM public.conversation_control_events WHERE id=(item->>'event_id')::uuid;
   SELECT * INTO src FROM public.tasks WHERE id=e.task_id;
   IF e.id IS NULL OR e.event_type IS DISTINCT FROM 'steer' OR e.status NOT IN ('pending','applied')
       OR src.id IS NULL OR src.type IS DISTINCT FROM 'chat' OR src.conversation_id IS DISTINCT FROM owner.conversation_id
       OR e.task_id::text IS DISTINCT FROM item->>'owner_task_id' OR e.conversation_id<>owner.conversation_id
       OR e.turn_id IS DISTINCT FROM src.turn_id OR src.user_id<>owner.user_id OR src.org_id IS DISTINCT FROM owner.org_id
       OR (src.id<>root AND src.delivery_context->>'logical_task_id' IS DISTINCT FROM root::text)
       OR jsonb_typeof(item->'message') IS DISTINCT FROM 'string'
       OR item->>'message' IS DISTINCT FROM btrim(e.payload->>'message')
       OR length(item->>'message') NOT BETWEEN 1 AND 20000
       OR item->>'message_sha256' IS DISTINCT FROM encode(sha256(convert_to(item->>'message','UTF8')),'hex')
       OR jsonb_typeof(item->'delivered') IS DISTINCT FROM 'boolean'
       OR (item-ARRAY['event_id','owner_task_id','message','message_sha256','delivered'])<>'{}'::jsonb THEN
     RAISE EXCEPTION 'TASK_USER_CHANGE_SOURCE_INVALID' USING ERRCODE='42501';
   END IF;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_task_user_changes() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.guard_agent_task_user_changes() TO everydayai_owner;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 SELECT pg_get_userbyid(relowner) INTO owner FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 CREATE TRIGGER agent_task_user_changes_guard BEFORE INSERT OR UPDATE OF state ON public.conversation_turn_checkpoints
   FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_user_changes();
END $$;
RESET ROLE;
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.capture_agent_task_user_change(p_task_id uuid,p_execution_token uuid,p_event_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; e public.conversation_control_events%ROWTYPE; cp jsonb; box jsonb;
 root uuid; existing jsonb; addition jsonb;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
   OR t.id IS NULL OR t.type IS DISTINCT FROM 'chat' OR (t.delivery_context @> '{"actor":true}') IS NOT TRUE
   OR t.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR t.execution_token IS DISTINCT FROM p_execution_token OR t.status IS DISTINCT FROM 'running'
   OR t.lease_expires_at IS NULL OR t.lease_expires_at<=now()
   OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
   OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
       WHERE m.org_id=t.org_id AND m.user_id=t.user_id AND m.status='active' AND o.status='active')) THEN
   RAISE EXCEPTION 'TASK_USER_CHANGE_CAPTURE_DENIED' USING ERRCODE='42501';
 END IF;
 SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=t.id FOR UPDATE;
 root:=COALESCE((cp#>>'{_task_execution,root_task_id}')::uuid,(cp#>>'{_task_bootstrap,input,task_id}')::uuid);
 IF root IS NULL THEN RAISE EXCEPTION 'TASK_USER_CHANGE_CHECKPOINT_REQUIRED' USING ERRCODE='42501'; END IF;
 SELECT * INTO e FROM public.conversation_control_events WHERE id=p_event_id AND task_id=t.id FOR UPDATE;
 IF e.id IS NULL OR e.event_type IS DISTINCT FROM 'steer' OR e.conversation_id IS DISTINCT FROM t.conversation_id
   OR e.turn_id IS DISTINCT FROM t.turn_id OR e.status NOT IN ('pending','applied')
   OR jsonb_typeof(e.payload->'message') IS DISTINCT FROM 'string'
   OR NULLIF(btrim(e.payload->>'message'),'') IS NULL OR length(btrim(e.payload->>'message'))>20000 THEN
   RAISE EXCEPTION 'TASK_USER_CHANGE_SOURCE_INVALID' USING ERRCODE='42501';
 END IF;
 box:=COALESCE(cp->'_task_user_changes',jsonb_build_object('version',1,'root_task_id',root,'items','[]'::jsonb));
 SELECT value INTO existing FROM jsonb_array_elements(box->'items') WHERE value->>'event_id'=p_event_id::text;
 addition:=jsonb_build_object('event_id',e.id,'owner_task_id',t.id,'message',btrim(e.payload->>'message'),
     'message_sha256',encode(sha256(convert_to(btrim(e.payload->>'message'),'UTF8')),'hex'),'delivered',false);
 IF existing IS NOT NULL AND (existing-'delivered') IS DISTINCT FROM (addition-'delivered') THEN
   RAISE EXCEPTION 'TASK_USER_CHANGE_SOURCE_INVALID' USING ERRCODE='42501';
 END IF;
 IF existing IS NULL THEN
   box:=jsonb_set(box,'{items}',(box->'items')||jsonb_build_array(addition));
   UPDATE public.conversation_turn_checkpoints SET state=jsonb_set(cp,'{_task_user_changes}',box),version=version+1,updated_at=now() WHERE task_id=t.id;
 END IF;
 UPDATE public.conversation_control_events SET status='applied',applied_execution_token=p_execution_token,applied_at=COALESCE(applied_at,now()) WHERE id=e.id AND status='pending';
 RETURN jsonb_build_object('outcome','captured','inbox',box);
END $$;
REVOKE ALL ON FUNCTION public.capture_agent_task_user_change(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.capture_agent_task_user_change(uuid,uuid,uuid) TO everydayai;
RESET ROLE;
