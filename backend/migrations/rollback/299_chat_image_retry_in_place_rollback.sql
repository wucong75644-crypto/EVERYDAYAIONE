-- Restore the prior publication/replay functions. Existing task/message history is retained.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.publish_chat_image_result(
    p_task_id UUID,p_content JSONB,p_status TEXT,p_error TEXT DEFAULT '',
    p_platform_pays BOOLEAN DEFAULT FALSE,p_org_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    v_task public.tasks%ROWTYPE; v_lifecycle JSONB; v_snapshot JSONB;
    v_tx public.credit_transactions%ROWTYPE; v_revision BIGINT; v_conv UUID;
    v_message public.messages%ROWTYPE; v_cost INTEGER:=0; v_balance INTEGER;
    v_trial_id UUID; v_trial RECORD; v_project_id UUID;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    IF p_platform_pays IS NULL OR p_status IS NULL OR p_status NOT IN ('completed','failed','cancelled')
       OR p_content IS NULL OR jsonb_typeof(p_content)<>'array' OR jsonb_array_length(p_content)<>1 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_PUBLICATION_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT conversation_id,(request_params->'_media_request_v1'->'origin'->>'trial_id')::UUID,
 (request_params->'_media_request_v1'->'origin'->>'project_id')::UUID INTO v_conv,v_trial_id,v_project_id
        FROM public.tasks WHERE id=p_task_id AND type='image'
        AND org_id IS NOT DISTINCT FROM p_org_id AND request_params ? '_media_request_v1';
    IF NOT FOUND THEN RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501'; END IF;
    -- Same ordering as Actor publication: conversation before output/task rows.
    IF v_project_id IS NOT NULL THEN
        PERFORM 1 FROM public.detail_projects WHERE id=v_project_id AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'DETAIL_PROJECT_DENIED' USING ERRCODE='42501'; END IF;
    ELSIF v_trial_id IS NULL THEN
        SELECT context_revision INTO v_revision FROM public.conversations WHERE id=v_conv FOR UPDATE;
    ELSE
        SELECT * INTO v_trial FROM public.skill_draft_trial_runs WHERE id=v_trial_id AND org_id=p_org_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_DENIED' USING ERRCODE='42501'; END IF;
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id FOR UPDATE;
    v_snapshot:=v_task.request_params->'_media_request_v1';
    v_lifecycle:=v_task.request_params->'_media_lifecycle_v1';
    IF v_project_id IS NOT NULL THEN
        IF v_snapshot->'origin'->>'destination' IS DISTINCT FROM 'detail_project' OR NOT EXISTS(
            SELECT 1 FROM public.detail_projects d WHERE d.id=v_project_id AND d.user_id=v_task.user_id
            AND d.org_id IS NOT DISTINCT FROM p_org_id AND d.run_state->>'run_id'=v_snapshot->'origin'->>'generation_run_id') THEN
            RAISE EXCEPTION 'DETAIL_PROJECT_DENIED' USING ERRCODE='42501';
        END IF;
    ELSIF v_trial_id IS NULL THEN
        SELECT * INTO v_message FROM public.messages WHERE id=v_task.assistant_message_id FOR UPDATE;
        IF NOT FOUND OR v_message.conversation_id IS DISTINCT FROM v_conv
       OR v_message.generation_params->>'task_id' IS DISTINCT FROM p_task_id::TEXT
       OR v_message.generation_params->>'origin' IS DISTINCT FROM 'chat_image' THEN
            RAISE EXCEPTION 'CHAT_IMAGE_TARGET_DENIED' USING ERRCODE='42501';
        END IF;
    ELSIF v_snapshot->'origin'->>'destination' IS DISTINCT FROM 'skill_trial'
        OR v_trial.actor_user_id IS DISTINCT FROM v_task.user_id
        OR v_trial.result->>'image_task_id' IS DISTINCT FROM p_task_id::TEXT THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_DENIED' USING ERRCODE='42501';
    END IF;
    IF v_lifecycle->>'phase'='published' THEN
        RETURN jsonb_build_object('outcome','replay','message_id',v_message.id,'revision',v_message.context_revision);
    END IF;
    IF p_platform_pays THEN
        IF v_lifecycle->>'phase' IS DISTINCT FROM 'uncertain' OR p_status<>'failed'
           OR (v_lifecycle->>'uncertain_deadline')::TIMESTAMPTZ IS NULL
           OR (v_lifecycle->>'uncertain_deadline')::TIMESTAMPTZ > NOW() THEN
            RAISE EXCEPTION 'CHAT_IMAGE_UNCERTAIN_NOT_DUE' USING ERRCODE='55000';
        END IF;
    ELSIF v_lifecycle->>'phase' IS DISTINCT FROM 'settling' THEN
        RAISE EXCEPTION 'CHAT_IMAGE_NOT_SETTLING' USING ERRCODE='55000';
    END IF;
    -- Prelock user before ledger, compatible with submission and legacy refunds.
    PERFORM 1 FROM public.users WHERE id=v_task.user_id FOR UPDATE;
    IF v_task.credit_transaction_id IS NOT NULL THEN
        SELECT * INTO v_tx FROM public.credit_transactions WHERE id=v_task.credit_transaction_id FOR UPDATE;
        IF NOT FOUND OR v_tx.task_id IS DISTINCT FROM p_task_id OR v_tx.user_id IS DISTINCT FROM v_task.user_id
           OR v_tx.org_id IS DISTINCT FROM p_org_id THEN
            RAISE EXCEPTION 'CHAT_IMAGE_LEDGER_DENIED' USING ERRCODE='42501';
        END IF;
        IF p_status='completed' THEN
            IF v_tx.status NOT IN ('pending','confirmed') THEN
                RAISE EXCEPTION 'CHAT_IMAGE_LEDGER_ALREADY_REFUNDED' USING ERRCODE='55000';
            END IF;
            UPDATE public.credit_transactions SET status='confirmed',confirmed_at=NOW()
                WHERE id=v_tx.id AND status='pending';
            v_cost:=v_tx.amount;
        ELSE
            IF v_tx.status NOT IN ('pending','refunded') THEN
                RAISE EXCEPTION 'CHAT_IMAGE_LEDGER_NOT_REFUNDABLE' USING ERRCODE='55000';
            END IF;
            PERFORM public.atomic_refund_credits(v_tx.id);
            IF NOT EXISTS(SELECT 1 FROM public.credit_transactions WHERE id=v_tx.id AND status='refunded') THEN
                RAISE EXCEPTION 'CHAT_IMAGE_REFUND_NOT_CONFIRMED' USING ERRCODE='55000';
            END IF;
        END IF;
    ELSIF p_status='completed' OR p_platform_pays OR v_task.result->>'status'='success' THEN
        RAISE EXCEPTION 'CHAT_IMAGE_LEDGER_MISSING' USING ERRCODE='55000';
    END IF;
    IF v_project_id IS NOT NULL THEN
        UPDATE public.detail_projects SET updated_at=NOW() WHERE id=v_project_id;
    ELSIF v_trial_id IS NOT NULL THEN
        UPDATE public.skill_draft_trial_runs SET status=CASE WHEN p_status='completed' THEN 'completed' ELSE 'failed' END,
            completed_at=NOW(),result=result || jsonb_build_object('status',p_status,
                'images',CASE WHEN p_status='completed' THEN p_content ELSE '[]'::JSONB END,
                'error',p_error,'credits_charged',v_cost,'submission_state','published') WHERE id=v_trial_id;
    ELSE
        v_revision:=COALESCE(v_revision,0)+1;
        UPDATE public.messages SET content=p_content::TEXT,status=CASE WHEN p_status='completed' THEN 'completed' ELSE 'failed' END,
        credits_cost=v_cost,context_revision=v_revision,
        generation_params=generation_params || jsonb_build_object('type','image','model',v_snapshot->>'model',
            'source_task_id',v_snapshot->>'source_task_id','num_images',1)
        WHERE id=v_message.id;
        UPDATE public.conversations SET context_revision=v_revision WHERE id=v_conv;
    END IF;
    -- Deliberately do not modify input message, closed parent Turn or sidebar preview.
    UPDATE public.tasks SET status=p_status,completed_at=NOW(),credits_used=v_cost,error_message=LEFT(p_error,2000),
        result_data=p_content->0,request_params=jsonb_set(request_params,'{_media_lifecycle_v1}',
            v_lifecycle || jsonb_build_object('phase','published','delivery_pending',TRUE,'published_at',NOW()))
        || CASE WHEN p_platform_pays OR (p_status='failed' AND v_task.result->>'status'='success') THEN jsonb_build_object('_media_platform_cost_v1',jsonb_build_object(
            'reason',CASE WHEN p_platform_pays THEN 'submission_uncertain_expired' ELSE 'image_output_contract_failure' END,
            'refunded_user_credits',COALESCE(v_tx.amount,0),
            'estimated_provider_credits',(v_snapshot->>'estimated_provider_credits')::INTEGER,
            'evidence',CASE WHEN p_platform_pays THEN 'unconfirmed' ELSE 'provider_success_unbilled' END,
            'recorded_at',NOW(),'model',v_snapshot->>'model',
            'resolution',v_snapshot->>'resolution')) ELSE '{}'::JSONB END,
        version=COALESCE(version,0)+1 WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','published','message_id',v_message.id,'revision',v_revision);
END;
$$;
CREATE OR REPLACE FUNCTION public.replay_chat_image_snapshot(
    p_source_task_id UUID,p_request_id UUID,p_snapshot JSONB,p_org_id UUID DEFAULT NULL,p_allow_new BOOLEAN DEFAULT FALSE
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_actor UUID:=NULLIF(current_setting('app.actor_user_id',TRUE),'')::UUID;
    v_source public.tasks%ROWTYPE; v_existing public.tasks%ROWTYPE; v_parent public.tasks%ROWTYPE;
    v_task UUID:=gen_random_uuid(); v_message UUID:=gen_random_uuid(); v_budget JSONB;
    v_cost INTEGER:=(p_snapshot->>'estimated_credits')::INTEGER; v_max_cost INTEGER; v_max_count INTEGER;
BEGIN
    IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime'
       OR v_actor IS NULL OR p_request_id IS NULL OR NULLIF(current_setting('app.org_id',TRUE),'')::UUID IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'CHAT_IMAGE_REPLAY_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_source FROM public.tasks WHERE id=p_source_task_id AND type='image' AND user_id=v_actor
        AND org_id IS NOT DISTINCT FROM p_org_id;
    IF NOT FOUND OR NOT(v_source.request_params ? '_media_request_v1')
       OR v_source.request_params->'_media_request_v1'->'origin'->>'destination'='skill_trial'
       OR v_source.status NOT IN ('completed','failed','cancelled')
       OR v_source.request_params->'_media_lifecycle_v1'->>'phase' IS DISTINCT FROM 'published' THEN
        RAISE EXCEPTION 'CHAT_IMAGE_REPLAY_SOURCE_DENIED' USING ERRCODE='42501';
    END IF;
    IF v_source.request_params->'_media_request_v1'->'origin'->>'destination'='detail_project' THEN
        IF p_snapshot->'origin'->>'retry_of_task_id' IS DISTINCT FROM p_source_task_id::text
        OR p_snapshot->'origin'->>'retry_request_id' IS DISTINCT FROM p_request_id::text THEN
            RAISE EXCEPTION 'DETAIL_REPLAY_DENIED' USING ERRCODE='42501'; END IF;
        SELECT * INTO v_existing FROM public.tasks WHERE user_id=v_actor AND org_id IS NOT DISTINCT FROM p_org_id
            AND request_params->'_media_request_v1'->'origin'->>'retry_of_task_id'=p_source_task_id::text
            AND request_params->'_media_request_v1'->'origin'->>'retry_request_id'=p_request_id::text;
        IF FOUND THEN RETURN jsonb_build_object('outcome','replay','task_id',v_existing.id,'message_id',NULL,
            'submission_state',v_existing.request_params->'_media_lifecycle_v1'->>'phase'); END IF;
        IF p_allow_new IS DISTINCT FROM TRUE THEN RETURN jsonb_build_object('outcome','new_required'); END IF;
        RETURN public.accept_detail_page_image(
            (p_snapshot->'origin'->>'project_id')::uuid,
            (p_snapshot->'origin'->'plan_source'->>'plan_id')::uuid,
            (p_snapshot->'origin'->'plan_source'->>'item_id')::uuid,p_snapshot,p_org_id);
    END IF;
    SELECT * INTO v_parent FROM public.tasks WHERE id=(v_source.request_params->'_media_request_v1'->'origin'->>'parent_task_id')::UUID FOR UPDATE;
    IF NOT FOUND OR v_parent.user_id IS DISTINCT FROM v_actor OR v_parent.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'CHAT_IMAGE_REPLAY_PARENT_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_snapshot->>'schema_version' IS DISTINCT FROM '1' OR p_snapshot->>'num_images' IS DISTINCT FROM '1'
       OR p_snapshot->'origin' ? 'tool_call_id' OR p_snapshot->'origin' ? 'destination'
       OR p_snapshot->'origin'->>'org_id' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->'origin'->>'org_id'
       OR p_snapshot->'origin'->>'context_scope' IS DISTINCT FROM 'user'
       OR p_snapshot->'origin'->>'input_message_id' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->'origin'->>'input_message_id'
       OR p_snapshot->'origin'->>'base_context_revision' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->'origin'->>'base_context_revision'
       OR p_snapshot->'origin'->>'retry_of_task_id' IS DISTINCT FROM p_source_task_id::TEXT
       OR p_snapshot->'origin'->>'retry_request_id' IS DISTINCT FROM p_request_id::TEXT
       OR p_snapshot->'origin'->>'parent_task_id' IS DISTINCT FROM v_parent.id::TEXT
       OR p_snapshot->'origin'->>'actor_user_id' IS DISTINCT FROM v_actor::TEXT
       OR p_snapshot->'origin'->>'workspace_owner_id' IS DISTINCT FROM v_actor::TEXT
       OR p_snapshot->'origin'->>'conversation_id' IS DISTINCT FROM v_source.conversation_id::TEXT
       OR p_snapshot->>'prompt' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->>'prompt'
       OR p_snapshot->'references' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->'references'
       OR p_snapshot->>'model' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->>'model'
       OR p_snapshot->>'mode' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->>'mode'
       OR p_snapshot->>'aspect_ratio' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->>'aspect_ratio'
       OR p_snapshot->>'resolution' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->>'resolution'
       OR p_snapshot->>'output_format' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->>'output_format'
       OR p_snapshot->>'background' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->>'background'
       OR p_snapshot->'size_requirement' IS DISTINCT FROM v_source.request_params->'_media_request_v1'->'size_requirement' THEN
        RAISE EXCEPTION 'CHAT_IMAGE_REPLAY_INPUT_CHANGED' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=v_actor AND status='active')
       OR NOT EXISTS(SELECT 1 FROM public.conversations WHERE id=v_source.conversation_id AND user_id=v_actor AND org_id IS NOT DISTINCT FROM p_org_id AND scope_type='user')
       OR (p_org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
            WHERE m.user_id=v_actor AND m.org_id=p_org_id AND m.status='active' AND o.status='active')) THEN
        RAISE EXCEPTION 'CHAT_IMAGE_REPLAY_IDENTITY_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_existing FROM public.tasks WHERE type='image'
        AND request_params->'_media_request_v1'->'origin'->>'retry_of_task_id'=p_source_task_id::TEXT
        AND request_params->'_media_request_v1'->'origin'->>'retry_request_id'=p_request_id::TEXT;
    IF FOUND THEN RETURN jsonb_build_object('outcome','replay','task_id',v_existing.id,'message_id',v_existing.assistant_message_id,
        'submission_state',v_existing.request_params->'_media_lifecycle_v1'->>'phase'); END IF;
    IF p_allow_new IS DISTINCT FROM TRUE THEN RETURN jsonb_build_object('outcome','new_required'); END IF;
    v_max_count:=(p_snapshot->'budget'->>'max_requests')::INTEGER;
    v_max_cost:=(p_snapshot->'budget'->>'max_credits')::INTEGER;
    IF v_cost IS NULL OR v_cost NOT BETWEEN 1 AND 300 OR v_max_count IS NULL OR v_max_count NOT BETWEEN 1 AND 15
       OR v_max_cost IS NULL OR v_max_cost NOT BETWEEN 1 AND 300 OR pg_column_size(p_snapshot)>128000 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_REPLAY_BUDGET_INVALID' USING ERRCODE='22023';
    END IF;
    v_budget:=COALESCE(v_parent.request_params->'_media_retry_budget_v1','{}'::JSONB);
    v_max_count:=LEAST(v_max_count,COALESCE((v_budget->>'max_requests')::INTEGER,v_max_count));
    v_max_cost:=LEAST(v_max_cost,COALESCE((v_budget->>'max_credits')::INTEGER,v_max_cost));
    v_max_cost:=LEAST(v_max_cost,COALESCE((v_parent.request_params->'_media_budget_v1'->>'max_credits')::INTEGER,v_max_cost));
    IF COALESCE((v_budget->>'reserved_credits')::INTEGER,0)
       + COALESCE((v_parent.request_params->'_media_budget_v1'->>'reserved_credits')::INTEGER,0)+v_cost>v_max_cost THEN
        RAISE EXCEPTION 'CHAT_IMAGE_REPLAY_BUDGET_EXCEEDED' USING ERRCODE='54000';
    END IF;
    INSERT INTO public.messages(id,conversation_id,org_id,role,content,status,generation_params)
        VALUES(v_message,v_source.conversation_id,p_org_id,'assistant','[{"type":"image","url":null}]','pending',
            jsonb_build_object('origin','chat_image','task_id',v_task,'type','image','num_images',1,'model',p_snapshot->>'model',
                'mode',p_snapshot->>'mode','aspect_ratio',p_snapshot->>'aspect_ratio','resolution',p_snapshot->>'resolution','source_task_id',p_source_task_id,
                'size_requirement',p_snapshot->'size_requirement'));
    INSERT INTO public.tasks(id,user_id,org_id,conversation_id,type,status,model_id,assistant_message_id,placeholder_message_id,batch_id,image_index,request_params)
        VALUES(v_task,v_actor,p_org_id,v_source.conversation_id,'image','pending',p_snapshot->>'model',v_message,v_message::TEXT,v_task::TEXT,0,
            jsonb_build_object('_media_request_v1',p_snapshot,'_media_lifecycle_v1',jsonb_build_object('phase','queued','accepted_at',NOW())));
    UPDATE public.tasks SET request_params=jsonb_set(COALESCE(request_params,'{}'::JSONB),'{_media_retry_budget_v1}',
        jsonb_build_object('requests',COALESCE((v_budget->>'requests')::INTEGER,0)+1,
            'reserved_credits',COALESCE((v_budget->>'reserved_credits')::INTEGER,0)+v_cost,
            'max_requests',v_max_count,'max_credits',v_max_cost)) WHERE id=v_parent.id;
    RETURN jsonb_build_object('outcome','accepted','task_id',v_task,'message_id',v_message,'submission_state','queued');
END;
$$;
RESET ROLE;
