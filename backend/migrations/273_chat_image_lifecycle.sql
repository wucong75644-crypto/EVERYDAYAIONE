-- Additive foundations. No new request caller is enabled by this migration.
-- SECURITY INVOKER deliberately preserves the caller's existing table RLS.
-- Lock order: parent task -> child task -> user -> credit transaction.

CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_image_call
ON public.tasks ((request_params->'_media_request_v1'->'origin'->>'parent_task_id'),
                 (request_params->'_media_request_v1'->'origin'->>'tool_call_id'))
WHERE type = 'image' AND request_params ? '_media_request_v1';

CREATE INDEX IF NOT EXISTS idx_chat_image_submission
ON public.tasks ((request_params->'_media_lifecycle_v1'->>'phase'), created_at, id)
WHERE type = 'image' AND request_params ? '_media_request_v1';

CREATE OR REPLACE FUNCTION public.accept_chat_image_request(
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
BEGIN
    IF SESSION_USER <> 'everydayai'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'runtime'
       OR v_actor IS NULL OR v_org IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'CHAT_IMAGE_SCOPE_DENIED' USING ERRCODE = '42501';
    END IF;
    IF p_snapshot IS NULL OR jsonb_typeof(p_snapshot) <> 'object'
       OR p_snapshot->>'schema_version' IS DISTINCT FROM '1'
       OR COALESCE(p_snapshot->>'mode', '') NOT IN ('text_to_image', 'image_to_image')
       OR p_snapshot->>'num_images' IS DISTINCT FROM '1'
       OR NULLIF(BTRIM(p_snapshot->>'prompt'), '') IS NULL
       OR COALESCE(p_snapshot->>'request_hash', '') !~ '^[0-9a-f]{64}$'
       OR COALESCE(v_call, '') !~ '^.{1,200}$'
       OR jsonb_typeof(p_snapshot->'references') IS DISTINCT FROM 'array'
       OR octet_length(p_snapshot::TEXT) > 131072 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_INPUT_INVALID' USING ERRCODE = '22023';
    END IF;
    v_cost := (p_snapshot->>'estimated_credits')::INTEGER;
    v_max_count := (p_snapshot->'budget'->>'max_requests')::INTEGER;
    v_max_cost := (p_snapshot->'budget'->>'max_credits')::INTEGER;
    IF v_cost IS NULL OR v_cost < 1 OR v_max_count IS NULL OR v_max_count NOT BETWEEN 1 AND 8
       OR v_max_cost IS NULL OR v_max_cost NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_BUDGET_INVALID' USING ERRCODE = '22023';
    END IF;

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
        RAISE EXCEPTION 'CHAT_IMAGE_PARENT_DENIED' USING ERRCODE = '42501';
    END IF;
    IF p_execution_token IS NULL OR v_parent.execution_token IS DISTINCT FROM p_execution_token THEN
        RAISE EXCEPTION 'CHAT_IMAGE_FENCING_LOST' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_conversation FROM public.conversations WHERE id = v_parent.conversation_id;
    IF NOT FOUND OR v_conversation.user_id IS DISTINCT FROM v_actor
       OR v_conversation.org_id IS DISTINCT FROM p_org_id
       OR COALESCE(v_conversation.scope_type, 'user') <> 'user'
       OR NOT EXISTS (SELECT 1 FROM public.users WHERE id = v_actor AND status::TEXT = 'active')
       OR (p_org_id IS NOT NULL AND (NOT EXISTS (
           SELECT 1 FROM public.org_members WHERE org_id = p_org_id AND user_id = v_actor AND status = 'active'
       ) OR NOT EXISTS (SELECT 1 FROM public.organizations WHERE id = p_org_id AND status = 'active'))) THEN
        RAISE EXCEPTION 'CHAT_IMAGE_IDENTITY_DENIED' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_child FROM public.tasks
     WHERE type = 'image' AND request_params->'_media_request_v1'->'origin'->>'parent_task_id' = p_parent_task_id::TEXT
       AND request_params->'_media_request_v1'->'origin'->>'tool_call_id' = v_call;
    IF FOUND THEN
        IF v_child.request_params->'_media_request_v1' IS DISTINCT FROM p_snapshot THEN
            RAISE EXCEPTION 'CHAT_IMAGE_CALL_CONFLICT' USING ERRCODE = '22023';
        END IF;
        RETURN jsonb_build_object('outcome','replay','task_id',v_child.id,
            'message_id',v_child.assistant_message_id,
            'submission_state',v_child.request_params->'_media_lifecycle_v1'->>'phase');
    END IF;
    IF v_parent.status <> 'running' THEN
        RAISE EXCEPTION 'CHAT_IMAGE_PARENT_STOPPED' USING ERRCODE = '55000';
    END IF;
    v_budget := COALESCE(v_parent.request_params->'_media_budget_v1', '{}'::JSONB);
    -- Keep the first accepted bounds; random variants and later requests cannot raise them.
    v_max_count := LEAST(v_max_count, COALESCE((v_budget->>'max_requests')::INTEGER, v_max_count));
    v_max_cost := LEAST(v_max_cost, COALESCE((v_budget->>'max_credits')::INTEGER, v_max_cost));
    v_count := COALESCE((v_budget->>'requests')::INTEGER, 0);
    IF v_count >= v_max_count OR COALESCE((v_budget->>'reserved_credits')::INTEGER, 0) + v_cost > v_max_cost THEN
        RAISE EXCEPTION 'CHAT_IMAGE_BUDGET_EXCEEDED' USING ERRCODE = '54000';
    END IF;
    INSERT INTO public.messages(id, conversation_id, org_id, role, content, status, generation_params)
    VALUES (v_message, v_parent.conversation_id, p_org_id, 'assistant', '[{"type":"image","url":null}]', 'pending',
        jsonb_build_object('origin','chat_image','task_id',v_task,'mode',p_snapshot->>'mode',
            'type','image','num_images',1,'submission_state','queued','model',p_snapshot->>'model',
            'aspect_ratio',p_snapshot->>'aspect_ratio','resolution',p_snapshot->>'resolution'));
    INSERT INTO public.tasks(id, user_id, org_id, conversation_id, type, status, model_id,
        assistant_message_id, placeholder_message_id, batch_id, image_index, request_params)
    VALUES (v_task, v_actor, p_org_id, v_parent.conversation_id, 'image', 'pending', p_snapshot->>'model',
        v_message, v_message::TEXT, v_task::TEXT, 0,
        jsonb_build_object('_media_request_v1',p_snapshot,
            '_media_lifecycle_v1',jsonb_build_object('phase','queued','attempt',0),
            'prompt',p_snapshot->>'prompt','model',p_snapshot->>'model', 'num_images',1));
    UPDATE public.tasks SET request_params = jsonb_set(COALESCE(request_params,'{}'::JSONB),
        '{_media_budget_v1}', jsonb_build_object('requests',v_count+1,
            'reserved_credits',COALESCE((v_budget->>'reserved_credits')::INTEGER,0)+v_cost,
            'max_requests',v_max_count,'max_credits',v_max_cost)) WHERE id = p_parent_task_id;
    RETURN jsonb_build_object('outcome','accepted','task_id',v_task,'message_id',v_message,'submission_state','queued');
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_chat_image_submission(
    p_task_id UUID, p_claim_token UUID, p_lease_seconds INTEGER,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_snapshot JSONB;
    v_parent UUID;
    v_tx UUID := gen_random_uuid();
    v_cost INTEGER;
    v_balance INTEGER;
BEGIN
    IF SESSION_USER NOT IN ('everydayai', 'everydayai_worker')
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'worker'
       OR NULLIF(current_setting('app.org_id', TRUE), '')::UUID IS DISTINCT FROM p_org_id
       OR p_claim_token IS NULL OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 10 AND 300 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_WORKER_DENIED' USING ERRCODE = '42501';
    END IF;
    SELECT (request_params->'_media_request_v1'->'origin'->>'parent_task_id')::UUID INTO v_parent
      FROM public.tasks WHERE id = p_task_id AND type = 'image' AND org_id IS NOT DISTINCT FROM p_org_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE = '42501';
    END IF;
    IF v_parent IS NULL THEN
        PERFORM 1 FROM public.skill_draft_trial_runs r JOIN public.tasks t ON t.id=p_task_id
          WHERE t.request_params->'_media_request_v1'->'origin'->>'destination'='skill_trial'
            AND r.id=(t.request_params->'_media_request_v1'->'origin'->>'trial_id')::UUID
            AND r.actor_user_id=t.user_id AND r.org_id=p_org_id AND r.status='running'
            AND r.result->>'image_task_id'=p_task_id::TEXT FOR UPDATE OF r;
        IF NOT FOUND THEN RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE = '42501'; END IF;
    ELSE
        PERFORM 1 FROM public.tasks WHERE id = v_parent FOR UPDATE;
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.status <> 'pending' OR v_task.request_params->'_media_lifecycle_v1'->>'phase' <> 'queued' THEN
        RETURN jsonb_build_object('outcome','not_queued');
    END IF;
    v_snapshot := v_task.request_params->'_media_request_v1';
    v_cost := (v_snapshot->>'estimated_credits')::INTEGER;
    IF NOT EXISTS (SELECT 1 FROM public.users WHERE id = v_task.user_id AND status::TEXT = 'active')
       OR (p_org_id IS NOT NULL AND (NOT EXISTS (SELECT 1 FROM public.org_members
            WHERE org_id=p_org_id AND user_id=v_task.user_id AND status='active')
            OR NOT EXISTS (SELECT 1 FROM public.organizations WHERE id=p_org_id AND status='active'))) THEN
        RETURN jsonb_build_object('outcome','identity_denied');
    END IF;
    UPDATE public.users SET credits = credits-v_cost, updated_at=NOW()
     WHERE id=v_task.user_id AND credits >= v_cost RETURNING credits INTO v_balance;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome','insufficient_credits');
    END IF;
    INSERT INTO public.credit_transactions(id, task_id, user_id, org_id, amount, type, status, reason, expires_at)
    VALUES (v_tx,p_task_id,v_task.user_id,p_org_id,v_cost,'lock','pending','Chat image',NULL);
    -- No generic credit expiry can refund an accepted/uncertain provider request.
    UPDATE public.tasks SET credit_transaction_id=v_tx, credits_locked=v_cost,
        request_params=jsonb_set(request_params,'{_media_lifecycle_v1}',
            jsonb_build_object('phase','submitting','claim_token',p_claim_token,
                'lease_expires_at',NOW()+make_interval(secs=>p_lease_seconds), 'attempt',1)),
        started_at=NOW(), status='running', version=COALESCE(version,0)+1
    WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','claimed','transaction_id',v_tx,'task_id',p_task_id);
END;
$$;

REVOKE ALL ON FUNCTION public.accept_chat_image_request(UUID,UUID,JSONB,UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim_chat_image_submission(UUID,UUID,INTEGER,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.accept_chat_image_request(UUID,UUID,JSONB,UUID) TO everydayai;
GRANT EXECUTE ON FUNCTION public.claim_chat_image_submission(UUID,UUID,INTEGER,UUID) TO everydayai, everydayai_worker;
