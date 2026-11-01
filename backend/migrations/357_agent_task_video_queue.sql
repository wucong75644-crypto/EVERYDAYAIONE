-- Native video queue shares tasks/messages/credits and original Actor identity.
-- No provider IO or enabled caller is introduced by the migration itself.
SET LOCAL ROLE everydayai;
ALTER TABLE public.agent_task_native_writes DROP CONSTRAINT agent_task_native_writes_domain_check;
ALTER TABLE public.agent_task_native_writes ADD CONSTRAINT agent_task_native_writes_domain_check CHECK(domain IN ('erp','file','video'));
CREATE UNIQUE INDEX agent_video_one_call ON public.tasks
 ((request_params#>>'{_agent_video_request_v1,origin,parent_task_id}'),(request_params#>>'{_agent_video_request_v1,origin,tool_call_id}'))
 WHERE type='video' AND request_params ? '_agent_video_request_v1';
CREATE INDEX agent_video_queue ON public.tasks((request_params#>>'{_agent_video_lifecycle_v1,phase}'),created_at,id)
 WHERE type='video' AND request_params ? '_agent_video_request_v1';
CREATE POLICY agent_video_worker_source_read ON public.agent_task_states FOR SELECT
 USING(current_setting('app.access_kind',true)='worker'
   AND actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   AND EXISTS(SELECT 1 FROM public.tasks child WHERE child.type='video' AND child.user_id=agent_task_states.actor_user_id
     AND child.org_id IS NOT DISTINCT FROM agent_task_states.org_id
     AND child.request_params#>>'{_agent_video_request_v1,origin,logical_task_id}'=agent_task_states.root_task_id::text AND child.delivery_context @> '{"agent_video":true}'::jsonb));
GRANT SELECT ON public.agent_task_states TO everydayai_worker;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.accept_agent_video_request(
    p_parent_task_id UUID, p_execution_token UUID, p_snapshot JSONB,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE
    v_parent public.tasks%ROWTYPE;
    v_child public.tasks%ROWTYPE;
    v_conversation public.conversations%ROWTYPE;
    v_actor UUID := NULLIF(current_setting('app.actor_user_id', TRUE), '')::UUID;
    v_org UUID := NULLIF(current_setting('app.org_id', TRUE), '')::UUID;
    v_origin JSONB := p_snapshot->'origin';
    v_budget JSONB;
    v_call TEXT := v_origin->>'tool_call_id';
    v_count INTEGER;
    v_cost INTEGER;
    v_max_count INTEGER;
    v_max_cost INTEGER;
    v_task UUID := gen_random_uuid();
    v_message UUID := gen_random_uuid();
    logical public.agent_task_states%ROWTYPE; invocation public.tool_invocations%ROWTYPE; operation jsonb; checked jsonb;
BEGIN
    IF SESSION_USER <> 'everydayai'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'runtime'
       OR v_actor IS NULL OR v_org IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'AGENT_VIDEO_SCOPE_DENIED' USING ERRCODE = '42501';
    END IF;
    IF p_snapshot IS NULL OR jsonb_typeof(p_snapshot) <> 'object'
       OR p_snapshot->>'schema_version' IS DISTINCT FROM '1'
       OR COALESCE(p_snapshot->>'mode', '') NOT IN ('text_to_video')
       OR p_snapshot->>'duration_seconds' IS DISTINCT FROM '10'
       OR NULLIF(BTRIM(p_snapshot->>'prompt'), '') IS NULL
       OR COALESCE(p_snapshot->>'request_hash', '') !~ '^[0-9a-f]{64}$'
       OR COALESCE(v_call, '') !~ '^.{1,200}$'
       OR jsonb_typeof(p_snapshot->'references') IS DISTINCT FROM 'array'
       OR octet_length(p_snapshot::TEXT) > 131072 THEN
        RAISE EXCEPTION 'AGENT_VIDEO_INPUT_INVALID' USING ERRCODE = '22023';
    END IF;
    v_cost := (p_snapshot->>'estimated_credits')::INTEGER;
    v_max_count := (p_snapshot->'budget'->>'max_requests')::INTEGER;
    v_max_cost := (p_snapshot->'budget'->>'max_credits')::INTEGER;
    IF v_cost IS NULL OR v_cost < 1 OR v_max_count IS NULL OR v_max_count NOT BETWEEN 1 AND 8
       OR v_max_cost IS NULL OR v_max_cost NOT BETWEEN 1 AND 100000 THEN
        RAISE EXCEPTION 'AGENT_VIDEO_BUDGET_INVALID' USING ERRCODE = '22023';
    END IF;

    PERFORM 1 FROM public.conversations WHERE id=(SELECT conversation_id FROM public.tasks WHERE id=p_parent_task_id) FOR UPDATE;
    SELECT * INTO v_parent FROM public.tasks WHERE id = p_parent_task_id FOR UPDATE;
    IF NOT FOUND OR v_parent.type <> 'chat' OR v_parent.user_id IS DISTINCT FROM v_actor
       OR v_parent.org_id IS DISTINCT FROM p_org_id
       OR v_origin->>'parent_task_id' IS DISTINCT FROM p_parent_task_id::TEXT
       OR v_origin->>'actor_user_id' IS DISTINCT FROM v_actor::TEXT
       OR v_origin->>'org_id' IS DISTINCT FROM p_org_id::TEXT
       OR v_origin->>'conversation_id' IS DISTINCT FROM v_parent.conversation_id::TEXT
       OR v_origin->>'workspace_owner_id' IS DISTINCT FROM v_actor::TEXT
       OR v_origin->>'context_scope' IS DISTINCT FROM 'user'
       OR v_origin->>'turn_id' IS DISTINCT FROM v_parent.turn_id::TEXT
       OR v_origin->>'input_message_id' IS DISTINCT FROM v_parent.input_message_id::TEXT
       OR v_origin->>'base_context_revision' IS DISTINCT FROM v_parent.base_context_revision::TEXT
       OR v_parent.turn_id IS NULL OR v_parent.input_message_id IS NULL
       OR v_parent.base_context_revision IS NULL
       OR (v_parent.delivery_context @> '{"actor":true}') IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'AGENT_VIDEO_PARENT_DENIED' USING ERRCODE = '42501';
    END IF;
    IF p_execution_token IS NULL OR v_parent.execution_token IS DISTINCT FROM p_execution_token THEN
        RAISE EXCEPTION 'AGENT_VIDEO_FENCING_LOST' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_conversation FROM public.conversations WHERE id = v_parent.conversation_id;
    IF NOT FOUND OR v_conversation.user_id IS DISTINCT FROM v_actor
       OR v_conversation.org_id IS DISTINCT FROM p_org_id
       OR COALESCE(v_conversation.scope_type, 'user') <> 'user'
       OR NOT EXISTS (SELECT 1 FROM public.users WHERE id = v_actor AND status::TEXT = 'active')
       OR (p_org_id IS NOT NULL AND (NOT EXISTS (
           SELECT 1 FROM public.org_members WHERE org_id = p_org_id AND user_id = v_actor AND status = 'active'
       ) OR NOT EXISTS (SELECT 1 FROM public.organizations WHERE id = p_org_id AND status = 'active'))) THEN
        RAISE EXCEPTION 'AGENT_VIDEO_IDENTITY_DENIED' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO logical FROM public.agent_task_states WHERE owner_task_id=v_parent.id FOR UPDATE;
    IF logical.root_task_id IS NULL OR logical.state->>'image_binding_version' IS DISTINCT FROM '3'
       OR logical.state->>'status' IS DISTINCT FROM 'active' OR v_origin ? 'trial_id'
       OR v_origin->>'logical_task_id' IS DISTINCT FROM logical.root_task_id::text
   OR jsonb_array_length(p_snapshot->'references')<>0 THEN
        RAISE EXCEPTION 'AGENT_VIDEO_OPERATION_REQUIRED' USING ERRCODE='42501'; END IF;
    checked:=public.read_agent_task_native_results(v_parent.id,p_execution_token,logical.root_task_id,logical.revision);
    IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RAISE EXCEPTION 'AGENT_VIDEO_FENCE_LOST' USING ERRCODE='42501'; END IF;
    SELECT * INTO invocation FROM public.tool_invocations WHERE task_id=v_parent.id AND turn_id=v_parent.turn_id
      AND tool_call_id=v_call AND tool_name='generate_video' AND status='running' AND args_hash=v_origin->>'args_hash';
    SELECT item INTO operation FROM jsonb_array_elements(logical.state->'operations') item
      WHERE item->>'owner_task_id'=v_parent.id::text AND item->>'owner_turn_id'=v_parent.turn_id::text
        AND item->>'invocation_id'=v_call AND item->>'tool_name'='generate_video'
        AND item->>'capability'='platform.generate_video' AND item->>'status'='reserved'
        AND item->>'arguments_sha256'=invocation.args_hash;
    IF invocation.id IS NULL OR operation IS NULL THEN
      RAISE EXCEPTION 'AGENT_VIDEO_OPERATION_REQUIRED' USING ERRCODE='42501'; END IF;
    SELECT * INTO v_child FROM public.tasks
     WHERE type = 'video' AND request_params->'_agent_video_request_v1'->'origin'->>'parent_task_id' = p_parent_task_id::TEXT
       AND request_params->'_agent_video_request_v1'->'origin'->>'tool_call_id' = v_call;
    IF FOUND THEN
        IF v_child.request_params->'_agent_video_request_v1' IS DISTINCT FROM p_snapshot THEN
            RAISE EXCEPTION 'AGENT_VIDEO_CALL_CONFLICT' USING ERRCODE = '22023';
        END IF;
        RETURN jsonb_build_object('outcome','replay','task_id',v_child.id,
            'message_id',v_child.assistant_message_id,
            'submission_state',v_child.request_params->'_agent_video_lifecycle_v1'->>'phase');
    END IF;
    IF v_parent.status <> 'running' THEN
        RAISE EXCEPTION 'AGENT_VIDEO_PARENT_STOPPED' USING ERRCODE = '55000';
    END IF;
    v_budget := COALESCE(v_parent.request_params->'_agent_video_budget_v1', '{}'::JSONB);
    -- Keep the first accepted bounds; random variants and later requests cannot raise them.
    v_max_count := LEAST(v_max_count, COALESCE((v_budget->>'max_requests')::INTEGER, v_max_count));
    v_max_cost := LEAST(v_max_cost, COALESCE((v_budget->>'max_credits')::INTEGER, v_max_cost));
    v_count := COALESCE((v_budget->>'requests')::INTEGER, 0);
    IF v_count >= v_max_count OR COALESCE((v_budget->>'reserved_credits')::INTEGER, 0) + v_cost > v_max_cost THEN
        RAISE EXCEPTION 'AGENT_VIDEO_BUDGET_EXCEEDED' USING ERRCODE = '54000';
    END IF;
    INSERT INTO public.messages(id, conversation_id, org_id, role, content, status, generation_params)
    VALUES (v_message, v_parent.conversation_id, p_org_id, 'assistant', '[{"type":"text","text":"视频任务排队中"}]', 'pending',
        jsonb_build_object('origin','agent_video','task_id',v_task,'mode',p_snapshot->>'mode',
            'type','video','duration_seconds',10,'submission_state','queued','model',p_snapshot->>'model',
            'aspect_ratio',p_snapshot->>'aspect_ratio','resolution',p_snapshot->>'resolution'));
    INSERT INTO public.tasks(id, user_id, org_id, conversation_id, type, status, model_id,
        assistant_message_id, placeholder_message_id, delivery_context,request_params)
    VALUES (v_task, v_actor, p_org_id, v_parent.conversation_id, 'video', 'pending', p_snapshot->>'model',
        v_message, v_message::TEXT,jsonb_build_object('agent_video',true,'operation_id',operation->>'id','native_id',invocation.id),
        jsonb_build_object('_agent_video_request_v1',p_snapshot,
            '_agent_video_lifecycle_v1',jsonb_build_object('phase','queued','attempt',0),
            'prompt',p_snapshot->>'prompt','model',p_snapshot->>'model', 'duration_seconds',10));
    INSERT INTO public.agent_task_native_writes(operation_id,native_invocation_id,owner_task_id,actor_user_id,org_id,
      domain,args_hash,intent,execution_token,dispatch_token,phase,response)
    VALUES(operation->>'id',invocation.id,v_parent.id,v_actor,p_org_id,'video',invocation.args_hash,
      jsonb_build_object('action','video','child_id',v_task,'request_hash',p_snapshot->>'request_hash'),p_execution_token,gen_random_uuid(),
      'returned',jsonb_build_object('method','generate_video','body',jsonb_build_object('accepted',true,'task_id',v_task,'message_id',v_message)));
    UPDATE public.tasks SET request_params = jsonb_set(COALESCE(request_params,'{}'::JSONB),
        '{_agent_video_budget_v1}', jsonb_build_object('requests',v_count+1,
            'reserved_credits',COALESCE((v_budget->>'reserved_credits')::INTEGER,0)+v_cost,
            'max_requests',v_max_count,'max_credits',v_max_cost)) WHERE id = p_parent_task_id;
    RETURN jsonb_build_object('outcome','accepted','task_id',v_task,'message_id',v_message,'submission_state','queued');
END;
$$;


REVOKE ALL ON FUNCTION public.accept_agent_video_request(uuid,uuid,jsonb,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.accept_agent_video_request(uuid,uuid,jsonb,uuid) TO everydayai;

CREATE FUNCTION public.assert_agent_video_worker_task(p_task_id uuid,p_org_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM public.assert_chat_image_worker(p_org_id);
 IF NOT EXISTS(SELECT 1 FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id
   AND delivery_context @> '{"agent_video":true}'::jsonb AND request_params ? '_agent_video_request_v1') THEN
   RAISE EXCEPTION 'AGENT_VIDEO_TASK_DENIED' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION public.assert_agent_video_worker_task(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.assert_agent_video_worker_task(uuid,uuid) TO everydayai,everydayai_worker;

RESET ROLE;
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.guard_agent_video_snapshot() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF OLD.delivery_context @> '{"agent_video":true}'::jsonb AND
  ((NEW.user_id,NEW.org_id,NEW.conversation_id,NEW.type,NEW.model_id,NEW.delivery_context,NEW.request_params->'_agent_video_request_v1')
   IS DISTINCT FROM (OLD.user_id,OLD.org_id,OLD.conversation_id,OLD.type,OLD.model_id,OLD.delivery_context,OLD.request_params->'_agent_video_request_v1')) THEN
   RAISE EXCEPTION 'AGENT_VIDEO_SNAPSHOT_IMMUTABLE' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_video_snapshot() FROM PUBLIC;
CREATE TRIGGER agent_video_snapshot BEFORE UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.guard_agent_video_snapshot();

RESET ROLE;
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.agent_video_submission_gate(p_task_id uuid,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE value text;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 SELECT s.state->>'status' INTO value FROM public.agent_task_states s JOIN public.tasks t
   ON s.root_task_id=(t.request_params#>>'{_agent_video_request_v1,origin,logical_task_id}')::uuid WHERE t.id=p_task_id;
 RETURN jsonb_build_object('outcome',CASE WHEN value IN ('active','waiting_dependencies') THEN 'ready'
   WHEN value='paused' THEN 'paused' ELSE 'stopped' END);
END $$;
REVOKE ALL ON FUNCTION public.agent_video_submission_gate(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_video_submission_gate(uuid,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.claim_agent_video_submission(p_task_id uuid,p_claim_token uuid,p_lease_seconds int,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE; snap jsonb; state jsonb; root_state jsonb; parent uuid; tx uuid; cost int; balance int;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 IF p_claim_token IS NULL OR p_lease_seconds NOT BETWEEN 10 AND 300 THEN RAISE EXCEPTION 'AGENT_VIDEO_CLAIM_INVALID'; END IF;
 SELECT (request_params#>>'{_agent_video_request_v1,origin,parent_task_id}')::uuid INTO parent
 FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id;
 IF parent IS NULL THEN RAISE EXCEPTION 'AGENT_VIDEO_TASK_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.conversations WHERE id=(SELECT conversation_id FROM public.tasks WHERE id=p_task_id) FOR UPDATE;
 PERFORM 1 FROM public.tasks WHERE id=parent FOR UPDATE;
 SELECT s.state INTO root_state FROM public.agent_task_states s WHERE s.root_task_id=(
   SELECT (request_params#>>'{_agent_video_request_v1,origin,logical_task_id}')::uuid FROM public.tasks WHERE id=p_task_id);
 IF root_state IS NULL OR root_state->>'status' NOT IN ('active','waiting_dependencies') THEN
   RETURN jsonb_build_object('outcome','not_active','reason',root_state->>'status'); END IF;
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 snap:=child.request_params->'_agent_video_request_v1';state:=child.request_params->'_agent_video_lifecycle_v1';
 IF NOT ((child.status='pending' AND state->>'phase'='queued') OR (child.status='running' AND state->>'phase'='submitting'
     AND NOT state ? 'dispatch_started_at' AND (state->>'lease_expires_at')::timestamptz<=now())) THEN
   RETURN jsonb_build_object('outcome','not_queued'); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=child.user_id AND status::text='active')
   OR (p_org_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM public.org_members WHERE org_id=p_org_id AND user_id=child.user_id AND status='active')
     OR NOT EXISTS(SELECT 1 FROM public.organizations WHERE id=p_org_id AND status='active'))) THEN
   RETURN jsonb_build_object('outcome','identity_denied'); END IF;
 cost:=(snap->>'estimated_credits')::int;
 IF cost IS NULL OR cost<=0 THEN RAISE EXCEPTION 'AGENT_VIDEO_COST_INVALID'; END IF;
 tx:=child.credit_transaction_id;
 IF tx IS NULL THEN
   UPDATE public.users SET credits=credits-cost,updated_at=now() WHERE id=child.user_id AND credits>=cost RETURNING credits INTO balance;
   IF NOT FOUND THEN RETURN jsonb_build_object('outcome','insufficient_credits'); END IF;
   tx:=gen_random_uuid();
   INSERT INTO public.credit_transactions(id,task_id,user_id,org_id,amount,type,status,reason,expires_at)
   VALUES(tx,child.id,child.user_id,p_org_id,cost,'lock','pending','Agent video',NULL);
 ELSIF NOT EXISTS(SELECT 1 FROM public.credit_transactions WHERE id=tx AND task_id=child.id AND user_id=child.user_id
    AND org_id IS NOT DISTINCT FROM p_org_id AND amount=cost AND status='pending') THEN
   RAISE EXCEPTION 'AGENT_VIDEO_CREDIT_BINDING_DENIED' USING ERRCODE='42501';
 END IF;
 UPDATE public.tasks SET credit_transaction_id=tx,credits_locked=cost,status='running',started_at=COALESCE(started_at,now()),
   request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1}',jsonb_build_object('phase','submitting','claim_token',p_claim_token,
     'lease_expires_at',now()+make_interval(secs=>p_lease_seconds))) WHERE id=child.id;
 RETURN jsonb_build_object('outcome','claimed','transaction_id',tx,'task_id',child.id);
END $$;
REVOKE ALL ON FUNCTION public.claim_agent_video_submission(uuid,uuid,int,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_agent_video_submission(uuid,uuid,int,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.mark_agent_video_dispatch(p_task_id uuid,p_claim_token uuid,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE; state jsonb;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 PERFORM 1 FROM public.conversations WHERE id=(SELECT conversation_id FROM public.tasks WHERE id=p_task_id) FOR UPDATE;
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 state:=child.request_params->'_agent_video_lifecycle_v1';
 IF child.id IS NULL OR child.status<>'running' OR state->>'phase' IS DISTINCT FROM 'submitting'
   OR state->>'claim_token' IS DISTINCT FROM p_claim_token::text OR (state->>'lease_expires_at')::timestamptz<=now()
   OR state ? 'dispatch_started_at' THEN RETURN jsonb_build_object('outcome','not_owned'); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.agent_task_states s WHERE s.root_task_id=(child.request_params#>>'{_agent_video_request_v1,origin,logical_task_id}')::uuid
   AND s.state->>'status' IN ('active','waiting_dependencies')) THEN RETURN jsonb_build_object('outcome','not_active'); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=child.user_id AND status::text='active')
   OR (p_org_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM public.org_members WHERE org_id=p_org_id AND user_id=child.user_id AND status='active')
     OR NOT EXISTS(SELECT 1 FROM public.organizations WHERE id=p_org_id AND status='active'))) THEN
   RETURN jsonb_build_object('outcome','identity_denied'); END IF;
 UPDATE public.tasks SET request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1}',state||jsonb_build_object('dispatch_started_at',now())) WHERE id=child.id;
 RETURN jsonb_build_object('outcome','dispatch');
END $$;
REVOKE ALL ON FUNCTION public.mark_agent_video_dispatch(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.mark_agent_video_dispatch(uuid,uuid,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.bind_agent_video_submission(p_task_id uuid,p_claim_token uuid,p_external_task_id text,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE; state jsonb;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 IF NULLIF(btrim(p_external_task_id),'') IS NULL OR length(p_external_task_id)>100 THEN RAISE EXCEPTION 'AGENT_VIDEO_EXTERNAL_ID_INVALID'; END IF;
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 state:=child.request_params->'_agent_video_lifecycle_v1';
 IF child.id IS NULL OR NOT child.request_params ? '_agent_video_request_v1' THEN RAISE EXCEPTION 'AGENT_VIDEO_TASK_DENIED' USING ERRCODE='42501'; END IF;
 IF child.external_task_id=p_external_task_id THEN RETURN jsonb_build_object('outcome','accepted'); END IF;
 IF state->>'claim_token' IS DISTINCT FROM p_claim_token::text OR NOT state ? 'dispatch_started_at'
   OR state->>'phase' NOT IN ('submitting','uncertain') OR child.external_task_id IS NOT NULL THEN
   RETURN jsonb_build_object('outcome','not_owned'); END IF;
 UPDATE public.tasks SET external_task_id=p_external_task_id,request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1}',
   state||jsonb_build_object('phase','accepted','accepted_at',now())) WHERE id=child.id;
 RETURN jsonb_build_object('outcome','accepted');
END $$;
REVOKE ALL ON FUNCTION public.bind_agent_video_submission(uuid,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.bind_agent_video_submission(uuid,uuid,text,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.mark_agent_video_uncertain(p_task_id uuid,p_claim_token uuid,p_timeout_seconds int DEFAULT 900,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE; state jsonb;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 IF p_timeout_seconds IS NULL OR p_timeout_seconds NOT BETWEEN 60 AND 86400 THEN RAISE EXCEPTION 'AGENT_VIDEO_TIMEOUT_INVALID'; END IF;
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 state:=child.request_params->'_agent_video_lifecycle_v1';
 IF child.id IS NULL OR state->>'claim_token' IS DISTINCT FROM p_claim_token::text OR NOT state ? 'dispatch_started_at'
   OR state->>'phase' NOT IN ('submitting','accepted','uncertain') THEN RETURN jsonb_build_object('outcome','not_owned'); END IF;
 UPDATE public.tasks SET request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1}',
   state||jsonb_build_object('phase','uncertain','uncertain_at',COALESCE(state->'uncertain_at',to_jsonb(now())),
     'uncertain_deadline',COALESCE(state->'uncertain_deadline',to_jsonb(now()+make_interval(secs=>p_timeout_seconds))))) WHERE id=child.id;
 RETURN jsonb_build_object('outcome','uncertain');
END $$;
REVOKE ALL ON FUNCTION public.mark_agent_video_uncertain(uuid,uuid,int,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.mark_agent_video_uncertain(uuid,uuid,int,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.record_agent_video_result(p_task_id uuid,p_external_task_id text,p_result jsonb,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE; state jsonb;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 state:=child.request_params->'_agent_video_lifecycle_v1';
 IF child.id IS NULL OR child.external_task_id IS DISTINCT FROM p_external_task_id
   OR NULLIF(p_external_task_id,'') IS NULL OR jsonb_typeof(p_result) IS DISTINCT FROM 'object'
   OR (p_result->>'status' IN ('success','failed')) IS NOT TRUE OR p_result->>'task_id' IS DISTINCT FROM p_external_task_id THEN
   RAISE EXCEPTION 'AGENT_VIDEO_RESULT_DENIED' USING ERRCODE='42501'; END IF;
 IF state->>'phase' IN ('settling','published') THEN
   IF child.result IS DISTINCT FROM p_result THEN RAISE EXCEPTION 'AGENT_VIDEO_RESULT_CHANGED'; END IF;
   RETURN jsonb_build_object('outcome','recorded'); END IF;
 IF state->>'phase' NOT IN ('accepted','uncertain') THEN RAISE EXCEPTION 'AGENT_VIDEO_RESULT_DENIED' USING ERRCODE='42501'; END IF;
 UPDATE public.tasks SET result=p_result,request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1}',
   state||jsonb_build_object('phase','settling','provider_finished_at',now())) WHERE id=child.id;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.record_agent_video_result(uuid,text,jsonb,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_agent_video_result(uuid,text,jsonb,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.fail_agent_video_before_dispatch(p_task_id uuid,p_claim_token uuid,p_error text,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE; state jsonb;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 state:=child.request_params->'_agent_video_lifecycle_v1';
 IF child.id IS NULL OR state ? 'dispatch_started_at' OR state->>'phase' NOT IN ('queued','submitting')
   OR (state->>'phase'='submitting' AND state->>'claim_token' IS DISTINCT FROM p_claim_token::text) THEN
   RETURN jsonb_build_object('outcome','not_owned'); END IF;
 UPDATE public.tasks SET result=jsonb_build_object('task_id',child.external_task_id,'status','failed','error',left(p_error,2000),'not_sent',true),
   request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1}',state||jsonb_build_object('phase','settling')) WHERE id=child.id;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.fail_agent_video_before_dispatch(uuid,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fail_agent_video_before_dispatch(uuid,uuid,text,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.record_agent_video_output(p_task_id uuid,p_output jsonb,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 IF child.id IS NULL OR child.request_params#>>'{_agent_video_lifecycle_v1,phase}' IS DISTINCT FROM 'settling'
   OR child.result->>'status' IS DISTINCT FROM 'success' OR jsonb_typeof(p_output) IS DISTINCT FROM 'object'
   OR (p_output->>'content_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE OR NULLIF(p_output->>'etag','') IS NULL
   OR NULLIF(p_output->>'url','') IS NULL OR ((p_output->>'size')::bigint>0) IS NOT TRUE THEN
   RAISE EXCEPTION 'AGENT_VIDEO_OUTPUT_DENIED' USING ERRCODE='42501'; END IF;
 IF child.request_params ? '_agent_video_output_v1' THEN
   IF child.request_params->'_agent_video_output_v1' IS DISTINCT FROM p_output THEN RAISE EXCEPTION 'AGENT_VIDEO_OUTPUT_CHANGED'; END IF;
 ELSE UPDATE public.tasks SET request_params=request_params||jsonb_build_object('_agent_video_output_v1',p_output) WHERE id=child.id;
 END IF;
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.record_agent_video_output(uuid,jsonb,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_agent_video_output(uuid,jsonb,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.publish_agent_video(p_task_id uuid,p_content jsonb,p_status text,p_error text DEFAULT '',p_platform_pays boolean DEFAULT false,p_org_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE child public.tasks%ROWTYPE; message public.messages%ROWTYPE; tx public.credit_transactions%ROWTYPE;
 conv uuid; revision bigint; cost int:=0; state jsonb; output jsonb;
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 IF p_status IS NULL OR p_status NOT IN ('completed','failed','cancelled') OR jsonb_typeof(p_content) IS DISTINCT FROM 'array'
   OR jsonb_array_length(p_content)<>1 THEN RAISE EXCEPTION 'AGENT_VIDEO_PUBLICATION_INVALID'; END IF;
 SELECT conversation_id INTO conv FROM public.tasks WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id
   AND request_params ? '_agent_video_request_v1';
 IF conv IS NULL THEN RAISE EXCEPTION 'AGENT_VIDEO_TASK_DENIED' USING ERRCODE='42501'; END IF;
 SELECT context_revision INTO revision FROM public.conversations WHERE id=conv FOR UPDATE;
 SELECT * INTO child FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 state:=child.request_params->'_agent_video_lifecycle_v1';output:=child.request_params->'_agent_video_output_v1';
 SELECT * INTO message FROM public.messages WHERE id=child.assistant_message_id FOR UPDATE;
 IF message.id IS NULL OR message.conversation_id IS DISTINCT FROM conv OR message.generation_params->>'origin' IS DISTINCT FROM 'agent_video'
   OR message.generation_params->>'task_id' IS DISTINCT FROM child.id::text THEN RAISE EXCEPTION 'AGENT_VIDEO_TARGET_DENIED'; END IF;
 IF state->>'phase'='published' THEN RETURN jsonb_build_object('outcome','replay','message_id',message.id,'revision',message.context_revision); END IF;
 IF p_platform_pays IS NULL THEN RAISE EXCEPTION 'AGENT_VIDEO_PUBLICATION_INVALID'; END IF;
 IF p_platform_pays THEN
   IF state->>'phase' IS DISTINCT FROM 'uncertain' OR p_status<>'failed'
     OR (state->>'uncertain_deadline')::timestamptz IS NULL OR (state->>'uncertain_deadline')::timestamptz>now() THEN
     RAISE EXCEPTION 'AGENT_VIDEO_UNCERTAIN_NOT_DUE'; END IF;
 ELSIF state->>'phase' IS DISTINCT FROM 'settling' THEN RAISE EXCEPTION 'AGENT_VIDEO_NOT_SETTLING'; END IF;
 IF p_status='completed' THEN
   IF child.result->>'status' IS DISTINCT FROM 'success' OR output IS NULL OR p_content->0->>'type' IS DISTINCT FROM 'video'
     OR p_content->0->>'url' IS DISTINCT FROM output->>'url' OR p_content->0->>'asset_id' IS DISTINCT FROM output->>'asset_id'
     OR NOT EXISTS(SELECT 1 FROM public.user_assets a JOIN public.user_asset_refs ref ON ref.asset_id=a.id
       WHERE a.id=(output->>'asset_id')::uuid AND a.status='ready' AND a.media_type='video' AND a.storage_scope='user'
         AND a.storage_owner_key=child.user_id::text AND a.org_id IS NOT DISTINCT FROM child.org_id
         AND a.content_sha256=output->>'content_sha256' AND ref.source_task_id=child.id AND ref.actor_user_id=child.user_id) THEN
     RAISE EXCEPTION 'AGENT_VIDEO_PERSISTENCE_UNCONFIRMED'; END IF;
 ELSIF NOT p_platform_pays AND child.result->>'status' IS DISTINCT FROM 'failed' THEN RAISE EXCEPTION 'AGENT_VIDEO_FAILURE_UNCONFIRMED'; END IF;
 PERFORM 1 FROM public.users WHERE id=child.user_id FOR UPDATE;
 IF child.credit_transaction_id IS NOT NULL THEN
   SELECT * INTO tx FROM public.credit_transactions WHERE id=child.credit_transaction_id FOR UPDATE;
   IF tx.id IS NULL OR tx.task_id IS DISTINCT FROM child.id OR tx.user_id IS DISTINCT FROM child.user_id
     OR tx.org_id IS DISTINCT FROM child.org_id OR tx.amount IS DISTINCT FROM child.credits_locked THEN RAISE EXCEPTION 'AGENT_VIDEO_CREDIT_BINDING_DENIED'; END IF;
   IF p_status='completed' THEN
     IF tx.status NOT IN ('pending','confirmed') THEN RAISE EXCEPTION 'AGENT_VIDEO_CREDITS_REFUNDED'; END IF;
     UPDATE public.credit_transactions SET status='confirmed',confirmed_at=now() WHERE id=tx.id AND status='pending';cost:=tx.amount;
   ELSE
     IF tx.status NOT IN ('pending','refunded') THEN RAISE EXCEPTION 'AGENT_VIDEO_CREDITS_NOT_REFUNDABLE'; END IF;
     PERFORM public.atomic_refund_credits(tx.id);
     IF NOT EXISTS(SELECT 1 FROM public.credit_transactions WHERE id=tx.id AND status='refunded') THEN RAISE EXCEPTION 'AGENT_VIDEO_REFUND_UNCONFIRMED'; END IF;
   END IF;
 ELSIF p_status='completed' OR p_platform_pays THEN RAISE EXCEPTION 'AGENT_VIDEO_CREDITS_MISSING'; END IF;
 revision:=COALESCE(revision,0)+1;
 UPDATE public.messages SET content=p_content::text,status=CASE WHEN p_status='completed' THEN 'completed' ELSE 'failed' END,credits_cost=cost,context_revision=revision,
   generation_params=generation_params||jsonb_build_object('submission_state','published') WHERE id=message.id;
 UPDATE public.conversations SET context_revision=revision WHERE id=conv;
 UPDATE public.tasks SET status=p_status,completed_at=now(),credits_used=cost,error_message=left(p_error,2000),result_data=p_content->0,
   request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1}',state||jsonb_build_object('phase','published','delivery_pending',true))
   || CASE WHEN p_platform_pays THEN jsonb_build_object('_media_platform_cost_v1',jsonb_build_object(
     'reason','submission_uncertain_expired','refunded_user_credits',COALESCE(tx.amount,0),
     'estimated_provider_credits',child.request_params#>'{_agent_video_request_v1,estimated_provider_credits}',
     'evidence','unconfirmed','recorded_at',now(),'model',child.model_id)) ELSE '{}'::jsonb END WHERE id=child.id;
 -- No input message, original user Turn or sidebar preview is rewritten.
 RETURN jsonb_build_object('outcome','published','message_id',message.id,'revision',revision);
END $$;
REVOKE ALL ON FUNCTION public.publish_agent_video(uuid,jsonb,text,text,boolean,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.publish_agent_video(uuid,jsonb,text,text,boolean,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.scan_agent_video_work(p_org_id uuid DEFAULT NULL,p_after_created timestamptz DEFAULT NULL,p_after_id uuid DEFAULT NULL,p_limit int DEFAULT 20)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM public.assert_chat_image_worker(p_org_id);
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'AGENT_VIDEO_SCAN_INVALID'; END IF;
 RETURN (SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.created_at,t.id),'[]'::jsonb) FROM(
  SELECT * FROM public.tasks WHERE type='video' AND org_id IS NOT DISTINCT FROM p_org_id AND delivery_context @> '{"agent_video":true}'::jsonb AND request_params ? '_agent_video_request_v1'
   AND (request_params#>>'{_agent_video_lifecycle_v1,phase}' IN ('queued','submitting','accepted','uncertain','settling')
     OR request_params#>>'{_agent_video_lifecycle_v1,delivery_pending}'='true')
   AND (p_after_created IS NULL OR (created_at,id)>(p_after_created,p_after_id)) ORDER BY created_at,id LIMIT p_limit)t);
END $$;
REVOKE ALL ON FUNCTION public.scan_agent_video_work(uuid,timestamptz,uuid,int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.scan_agent_video_work(uuid,timestamptz,uuid,int) TO everydayai,everydayai_worker;

CREATE FUNCTION public.ack_agent_video_delivery(p_task_id uuid,p_org_id uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM public.assert_agent_video_worker_task(p_task_id,p_org_id);
 UPDATE public.tasks SET request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1,delivery_pending}','false'::jsonb)
 WHERE id=p_task_id AND type='video' AND org_id IS NOT DISTINCT FROM p_org_id AND request_params#>>'{_agent_video_lifecycle_v1,phase}'='published';
 RETURN jsonb_build_object('outcome','recorded');
END $$;
REVOKE ALL ON FUNCTION public.ack_agent_video_delivery(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ack_agent_video_delivery(uuid,uuid) TO everydayai,everydayai_worker;

CREATE FUNCTION public.agent_task_video_rejection_verified(p_result jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(p_result->'tool_result' @> '{"kind":"agent","status":"error", "execution":{"status":"succeeded","handler_started":true,"cancelled":false,"cached":false,"replayed":false},
 "audit":{"tool_name":"generate_video"},"raw":{"status":"error","metadata":{"accepted":false,"completed":false,"submission_state":"not_accepted"}}}'::jsonb,false)
$$;
REVOKE ALL ON FUNCTION public.agent_task_video_rejection_verified(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_video_rejection_verified(jsonb) TO everydayai;

CREATE FUNCTION public.read_agent_task_video(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,p_operation_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE checked jsonb; proof jsonb; child public.tasks%ROWTYPE;
BEGIN
 checked:=public.read_agent_task_native_write(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_operation_id);
 IF checked->>'outcome' IS DISTINCT FROM 'found' THEN RETURN checked; END IF;
 proof:=checked->'proof';
 IF proof->>'domain' IS DISTINCT FROM 'video' THEN RAISE EXCEPTION 'AGENT_VIDEO_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO child FROM public.tasks WHERE id=(proof#>>'{intent,child_id}')::uuid AND type='video'
   AND user_id=(proof->>'actor_user_id')::uuid AND org_id IS NOT DISTINCT FROM (proof->>'org_id')::uuid
   AND conversation_id=(SELECT conversation_id FROM public.tasks WHERE id=p_task_id)
   AND request_params#>>'{_agent_video_request_v1,request_hash}'=proof#>>'{intent,request_hash}'
   AND request_params#>>'{_agent_video_request_v1,origin,parent_task_id}'=proof->>'task_id'
   AND request_params#>>'{_agent_video_request_v1,origin,tool_call_id}'=proof->>'tool_call_id';
 IF child.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 RETURN jsonb_build_object('outcome','found','proof',proof||jsonb_build_object('video',jsonb_build_object(
   'id',child.id,'actor_user_id',child.user_id,'org_id',child.org_id,'conversation_id',child.conversation_id,
   'phase',child.request_params#>>'{_agent_video_lifecycle_v1,phase}','status',child.status,
   'request_hash',child.request_params#>>'{_agent_video_request_v1,request_hash}',
   'output',child.request_params->'_agent_video_output_v1','content',child.result_data,
   'asset_available',EXISTS(SELECT 1 FROM public.user_assets asset JOIN public.user_asset_refs ref ON ref.asset_id=asset.id
     WHERE asset.id=(child.request_params#>>'{_agent_video_output_v1,asset_id}')::uuid AND asset.status='ready' AND asset.media_type='video' AND asset.storage_scope='user'
       AND asset.storage_owner_key=child.user_id::text AND asset.org_id IS NOT DISTINCT FROM child.org_id
       AND asset.content_sha256=child.request_params#>>'{_agent_video_output_v1,content_sha256}'
       AND ref.source_task_id=child.id AND ref.actor_user_id=child.user_id))));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_video(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_video(uuid,uuid,uuid,bigint,text) TO everydayai;
RESET ROLE;
