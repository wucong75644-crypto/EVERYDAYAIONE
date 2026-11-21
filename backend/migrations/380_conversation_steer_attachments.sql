-- Keep attachment selectors bound to the authenticated steer event and checkpoint.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.valid_conversation_steer_attachments(value jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
BEGIN
 IF jsonb_typeof(value) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 RETURN jsonb_array_length(value) BETWEEN 1 AND 16 AND NOT EXISTS (
   SELECT FROM jsonb_array_elements(value) AS entry(item)
    WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
       OR item-ARRAY['kind','workspace_path','resource_ref'] <> '{}'::jsonb
       OR COALESCE(item->>'kind','') NOT IN ('image','file')
       OR jsonb_typeof(item->'workspace_path') IS DISTINCT FROM 'string'
       OR COALESCE(length(item->>'workspace_path'),0) NOT BETWEEN 1 AND 512
       OR jsonb_typeof(item->'resource_ref') IS DISTINCT FROM 'string'
       OR COALESCE(length(item->>'resource_ref'),0) NOT BETWEEN 7 AND 2048
       OR COALESCE(item->>'resource_ref','') !~ '^fref1_'
 );
END $$;
CREATE OR REPLACE FUNCTION append_conversation_steer(
    p_conversation_id UUID,
    p_task_id UUID,
    p_turn_id UUID,
    p_dedupe_key TEXT,
    p_payload JSONB DEFAULT '{}'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task tasks%ROWTYPE;
    v_event conversation_control_events%ROWTYPE;
    v_inserted_count BIGINT;
    v_message TEXT;
    v_checkpoint JSONB;
BEGIN
    v_message := NULLIF(BTRIM(p_payload->>'message'), '');
    IF p_conversation_id IS NULL
       OR p_task_id IS NULL
       OR NULLIF(BTRIM(p_dedupe_key), '') IS NULL
       OR p_payload IS NULL
       OR jsonb_typeof(p_payload) <> 'object'
       OR (p_payload ? 'attachments' AND NOT public.valid_conversation_steer_attachments(p_payload->'attachments'))
       OR v_message IS NULL
       OR length(v_message) > 20000 THEN
        RAISE EXCEPTION 'ACTOR_STEER_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.type <> 'chat'
       OR v_task.status <> 'running'
       OR (p_payload ? 'attachments' AND v_task.turn_id IS DISTINCT FROM p_turn_id)
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB) THEN
        RAISE EXCEPTION 'ACTOR_STEER_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    IF p_payload ? 'attachments' THEN
        SELECT state INTO v_checkpoint FROM public.conversation_turn_checkpoints WHERE task_id = p_task_id;
        IF v_checkpoint IS NULL OR v_checkpoint->>'checkpoint_kind' = 'commit_ready'
           OR (
               COALESCE((v_checkpoint#>>'{_task_execution,image_binding_version}')::integer, 0) >= 3
               OR v_checkpoint#>>'{_task_bootstrap,input,model_directed}' = 'true'
               OR v_checkpoint#>>'{_task_bootstrap,input,require_coverage_review}' = 'true'
           ) IS NOT TRUE THEN
            RAISE EXCEPTION 'ACTOR_STEER_ATTACHMENT_WINDOW_CLOSED' USING ERRCODE = '42501';
        END IF;
    END IF;

    INSERT INTO conversation_control_events(
        conversation_id, task_id, turn_id, event_type, dedupe_key, payload
    ) VALUES (
        p_conversation_id, p_task_id, p_turn_id, 'steer',
        BTRIM(p_dedupe_key), jsonb_build_object('message', v_message) ||
          CASE WHEN p_payload ? 'attachments'
               THEN jsonb_build_object('attachments', p_payload->'attachments')
               ELSE '{}'::jsonb END
    )
    ON CONFLICT (task_id, dedupe_key) DO NOTHING;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;

    SELECT * INTO v_event
      FROM conversation_control_events
     WHERE task_id = p_task_id
       AND dedupe_key = BTRIM(p_dedupe_key);

    RETURN jsonb_build_object(
        'outcome', 'enqueued',
        'event_id', v_event.id,
        'event_sequence', v_event.event_sequence,
        'already_enqueued', v_inserted_count = 0,
        'payload', v_event.payload
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.guard_agent_task_user_changes() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE oldbox jsonb; box jsonb:=NEW.state->'_task_user_changes'; owner public.tasks%ROWTYPE;
 item jsonb; prior jsonb; e public.conversation_control_events%ROWTYPE; src public.tasks%ROWTYPE; root uuid; ordinal bigint;
BEGIN
 IF TG_OP='UPDATE' THEN oldbox:=OLD.state->'_task_user_changes'; END IF;
 IF NEW.state->>'checkpoint_kind' = 'commit_ready' AND (
   EXISTS (SELECT FROM public.conversation_control_events incoming
            WHERE incoming.task_id=NEW.task_id AND incoming.event_type='steer' AND incoming.status='pending'
              AND incoming.payload ? 'attachments')
   OR EXISTS (SELECT FROM jsonb_array_elements(COALESCE(box->'items','[]'::jsonb)) entry(pending_item)
              WHERE pending_item ? 'attachments' AND pending_item->'delivered' IS DISTINCT FROM 'true'::jsonb)
 ) THEN
   RAISE EXCEPTION 'ACTOR_STEER_ATTACHMENT_BEFORE_COMMIT' USING ERRCODE='40001';
 END IF;
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
       OR item->'attachments' IS DISTINCT FROM e.payload->'attachments'
       OR (item ? 'attachments' AND NOT public.valid_conversation_steer_attachments(item->'attachments'))
       OR jsonb_typeof(item->'delivered') IS DISTINCT FROM 'boolean'
       OR (item-ARRAY['event_id','owner_task_id','message','message_sha256','delivered','attachments'])<>'{}'::jsonb THEN
     RAISE EXCEPTION 'TASK_USER_CHANGE_SOURCE_INVALID' USING ERRCODE='42501';
   END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION public.capture_agent_task_user_change(p_task_id uuid,p_execution_token uuid,p_event_id uuid)
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
 IF e.payload ? 'attachments' THEN
   addition:=jsonb_set(addition,'{attachments}',e.payload->'attachments');
 END IF;
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
RESET ROLE;
