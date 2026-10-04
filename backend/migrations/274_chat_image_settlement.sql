-- Single-image lifecycle and platform-paid uncertain submissions. Existing tables/ledger only.
CREATE INDEX IF NOT EXISTS idx_chat_image_platform_cost
ON public.tasks (completed_at, id) WHERE request_params ? '_media_platform_cost_v1';

CREATE OR REPLACE FUNCTION public.assert_chat_image_worker(p_org_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
    IF SESSION_USER NOT IN ('everydayai','everydayai_worker')
       OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'worker'
       OR NULLIF(current_setting('app.org_id',TRUE),'')::UUID IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'CHAT_IMAGE_WORKER_DENIED' USING ERRCODE='42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.mark_chat_image_dispatch(
    p_task_id UUID,p_claim_token UUID,p_org_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_task public.tasks%ROWTYPE; v_lifecycle JSONB;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id AND type='image'
        AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
    v_lifecycle:=v_task.request_params->'_media_lifecycle_v1';
    IF NOT FOUND OR v_task.status <> 'running' OR v_lifecycle->>'phase' <> 'submitting'
       OR v_lifecycle->>'claim_token' IS DISTINCT FROM p_claim_token::TEXT
       OR (v_lifecycle->>'lease_expires_at')::TIMESTAMPTZ <= NOW()
       OR v_lifecycle ? 'dispatch_started_at' THEN
        RETURN jsonb_build_object('outcome','not_owned');
    END IF;
    UPDATE public.tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1}',
        v_lifecycle || jsonb_build_object('dispatch_started_at',NOW())) WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','dispatch');
END;
$$;

CREATE OR REPLACE FUNCTION public.bind_chat_image_submission(
    p_task_id UUID,p_claim_token UUID,p_external_task_id TEXT,p_org_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_task public.tasks%ROWTYPE; v_lifecycle JSONB;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    IF NULLIF(BTRIM(p_external_task_id),'') IS NULL OR length(p_external_task_id)>100 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_EXTERNAL_ID_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id AND type='image'
        AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
    IF NOT FOUND OR NOT (v_task.request_params ? '_media_request_v1') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501';
    END IF;
    v_lifecycle:=v_task.request_params->'_media_lifecycle_v1';
    IF v_task.external_task_id=p_external_task_id THEN RETURN jsonb_build_object('outcome','accepted'); END IF;
    IF v_lifecycle->>'claim_token' IS DISTINCT FROM p_claim_token::TEXT
       OR COALESCE(v_lifecycle->>'phase','') NOT IN ('submitting','uncertain')
       OR v_task.external_task_id IS NOT NULL THEN RETURN jsonb_build_object('outcome','not_owned'); END IF;
    UPDATE public.tasks SET external_task_id=p_external_task_id,
        request_params=jsonb_set(request_params,'{_media_lifecycle_v1}',
            v_lifecycle || jsonb_build_object('phase','accepted','accepted_at',NOW())),
        version=COALESCE(version,0)+1 WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','accepted');
END;
$$;

CREATE OR REPLACE FUNCTION public.mark_chat_image_uncertain(
    p_task_id UUID,p_claim_token UUID,p_timeout_seconds INTEGER,p_org_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_task public.tasks%ROWTYPE; v_lifecycle JSONB;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    IF p_timeout_seconds IS NULL OR p_timeout_seconds NOT BETWEEN 60 AND 86400 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_DEADLINE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id AND type='image'
        AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
    v_lifecycle:=v_task.request_params->'_media_lifecycle_v1';
    IF NOT FOUND OR NOT (v_task.request_params ? '_media_request_v1') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501';
    END IF;
    IF v_lifecycle->>'phase'='uncertain' THEN RETURN jsonb_build_object('outcome','uncertain'); END IF;
    IF COALESCE(v_lifecycle->>'phase','') NOT IN ('submitting','accepted')
       OR (v_lifecycle->>'phase'='submitting' AND v_lifecycle->>'claim_token' IS DISTINCT FROM p_claim_token::TEXT) THEN
        RETURN jsonb_build_object('outcome','not_owned');
    END IF;
    UPDATE public.tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1}',
        v_lifecycle || jsonb_build_object('phase','uncertain','uncertain_at',NOW(),
            'uncertain_deadline',NOW()+make_interval(secs=>p_timeout_seconds))) WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','uncertain');
END;
$$;

CREATE OR REPLACE FUNCTION public.record_chat_image_provider_result(
    p_task_id UUID,p_result JSONB,p_org_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_task public.tasks%ROWTYPE; v_lifecycle JSONB;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    IF p_result IS NULL OR jsonb_typeof(p_result)<>'object'
       OR COALESCE(p_result->>'status','') NOT IN ('success','failed') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_RESULT_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id AND type='image'
        AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
    IF NOT FOUND OR NOT (v_task.request_params ? '_media_request_v1') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501';
    END IF;
    v_lifecycle:=v_task.request_params->'_media_lifecycle_v1';
    IF v_lifecycle->>'phase' IN ('settling','published') THEN
        RETURN jsonb_build_object('outcome','replay');
    END IF;
    IF COALESCE(v_lifecycle->>'phase','') NOT IN ('queued','submitting','accepted','uncertain') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_STATE_INVALID' USING ERRCODE='55000';
    END IF;
    -- Only a definite supplier result can override uncertainty. An error before
    -- dispatch is also definite; callers never feed a network timeout here.
    UPDATE public.tasks SET result=p_result,
        request_params=jsonb_set(request_params,'{_media_lifecycle_v1}',v_lifecycle ||
            jsonb_build_object('phase','settling','provider_finished_at',NOW())),
        version=COALESCE(version,0)+1 WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','settling');
END;
$$;

-- Local validation/queue failures cannot overwrite another worker's paid send.
CREATE OR REPLACE FUNCTION public.fail_chat_image_before_dispatch(
    p_task_id UUID,p_error TEXT,p_expected_phase TEXT,p_claim_token UUID DEFAULT NULL,p_org_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_task public.tasks%ROWTYPE; v_state JSONB;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id AND type='image'
        AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
    IF NOT FOUND OR NOT (v_task.request_params ? '_media_request_v1') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501';
    END IF;
    v_state:=v_task.request_params->'_media_lifecycle_v1';
    IF p_expected_phase IS NULL OR p_expected_phase NOT IN ('queued','submitting')
       OR v_state->>'phase' IS DISTINCT FROM p_expected_phase
       OR v_state ? 'dispatch_started_at'
       OR (p_expected_phase='submitting' AND v_state->>'claim_token' IS DISTINCT FROM p_claim_token::TEXT) THEN
        RETURN jsonb_build_object('outcome','state_changed');
    END IF;
    UPDATE public.tasks SET result=jsonb_build_object('status','failed','error',LEFT(p_error,2000)),
        request_params=jsonb_set(request_params,'{_media_lifecycle_v1}',v_state ||
            jsonb_build_object('phase','settling','provider_finished_at',NOW())),version=COALESCE(version,0)+1
        WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','settling');
END;
$$;

CREATE OR REPLACE FUNCTION public.publish_chat_image_result(
    p_task_id UUID,p_content JSONB,p_status TEXT,p_error TEXT DEFAULT '',
    p_platform_pays BOOLEAN DEFAULT FALSE,p_org_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    v_task public.tasks%ROWTYPE; v_lifecycle JSONB; v_snapshot JSONB;
    v_tx public.credit_transactions%ROWTYPE; v_revision BIGINT; v_conv UUID;
    v_message public.messages%ROWTYPE; v_cost INTEGER:=0; v_balance INTEGER;
    v_trial_id UUID; v_trial RECORD;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    IF p_platform_pays IS NULL OR p_status IS NULL OR p_status NOT IN ('completed','failed','cancelled')
       OR p_content IS NULL OR jsonb_typeof(p_content)<>'array' OR jsonb_array_length(p_content)<>1 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_PUBLICATION_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT conversation_id,(request_params->'_media_request_v1'->'origin'->>'trial_id')::UUID INTO v_conv,v_trial_id
        FROM public.tasks WHERE id=p_task_id AND type='image'
        AND org_id IS NOT DISTINCT FROM p_org_id AND request_params ? '_media_request_v1';
    IF NOT FOUND THEN RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501'; END IF;
    -- Same ordering as Actor publication: conversation before output/task rows.
    IF v_trial_id IS NULL THEN
        SELECT context_revision INTO v_revision FROM public.conversations WHERE id=v_conv FOR UPDATE;
    ELSE
        SELECT * INTO v_trial FROM public.skill_draft_trial_runs WHERE id=v_trial_id AND org_id=p_org_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_DENIED' USING ERRCODE='42501'; END IF;
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id FOR UPDATE;
    v_snapshot:=v_task.request_params->'_media_request_v1';
    v_lifecycle:=v_task.request_params->'_media_lifecycle_v1';
    IF v_trial_id IS NULL THEN
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
    IF v_trial_id IS NOT NULL THEN
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

CREATE OR REPLACE FUNCTION public.stop_queued_chat_image(p_task_id UUID,p_org_id UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_task public.tasks%ROWTYPE;
BEGIN
    IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime'
       OR NULLIF(current_setting('app.org_id',TRUE),'')::UUID IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'CHAT_IMAGE_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id AND user_id=NULLIF(current_setting('app.actor_user_id',TRUE),'')::UUID
        AND org_id IS NOT DISTINCT FROM p_org_id AND type='image' FOR UPDATE;
    IF NOT FOUND OR NOT (v_task.request_params ? '_media_request_v1') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501';
    END IF;
    IF v_task.request_params->'_media_lifecycle_v1'->>'phase'<>'queued' THEN
        RETURN jsonb_build_object('outcome','already_submitted','message','任务已领取或提交，无法撤回；将继续核实与结算');
    END IF;
    UPDATE public.tasks SET result=jsonb_build_object('status','failed','error','排队任务已停止','cancelled',TRUE),
        request_params=jsonb_set(request_params,'{_media_lifecycle_v1,phase}','"settling"') WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','stopped');
END;
$$;

CREATE OR REPLACE FUNCTION public.ack_chat_image_delivery(p_task_id UUID,p_org_id UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    UPDATE public.tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,delivery_pending}','false')
        WHERE id=p_task_id AND type='image' AND org_id IS NOT DISTINCT FROM p_org_id
        AND request_params ? '_media_request_v1'
        AND request_params->'_media_lifecycle_v1'->>'phase'='published';
    RETURN jsonb_build_object('outcome',CASE WHEN FOUND THEN 'delivered' ELSE 'not_published' END);
END;
$$;

CREATE OR REPLACE FUNCTION public.scan_chat_image_work(
    p_org_id UUID DEFAULT NULL,p_after_created TIMESTAMPTZ DEFAULT NULL,p_after_id UUID DEFAULT NULL,
    p_limit INTEGER DEFAULT 20
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_rows JSONB;
BEGIN
    PERFORM public.assert_chat_image_worker(p_org_id);
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_SCAN_LIMIT_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.created_at,t.id),'[]'::JSONB) INTO v_rows FROM (
        SELECT * FROM public.tasks WHERE type='image' AND org_id IS NOT DISTINCT FROM p_org_id
        AND request_params ? '_media_request_v1'
        AND (request_params->'_media_lifecycle_v1'->>'phase' IN ('queued','submitting','accepted','uncertain','settling')
            OR request_params->'_media_lifecycle_v1'->>'delivery_pending'='true')
        AND (p_after_created IS NULL OR (created_at,id)>(p_after_created,p_after_id))
        ORDER BY created_at,id LIMIT p_limit
    ) t;
    RETURN v_rows;
END;
$$;

-- No sampling: aggregate all cost facts in the requested half-open time range.
-- Return only operational facts, never prompts, URLs or credentials.
CREATE OR REPLACE FUNCTION public.chat_image_platform_cost_report(
    p_since TIMESTAMPTZ,p_until TIMESTAMPTZ,p_page INTEGER DEFAULT 1,p_page_size INTEGER DEFAULT 20
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_summary JSONB; v_items JSONB;
BEGIN
    IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime_admin'
       OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=NULLIF(current_setting('app.actor_user_id',TRUE),'')::UUID
            AND status='active' AND role='super_admin') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_COST_ADMIN_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_since IS NULL OR p_until IS NULL OR p_since>=p_until
       OR p_until-p_since>INTERVAL '366 days' OR p_page IS NULL OR p_page NOT BETWEEN 1 AND 100000
       OR p_page_size IS NULL OR p_page_size NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'CHAT_IMAGE_COST_RANGE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT jsonb_build_object('count',COUNT(*),
        'refunded_user_credits',COALESCE(SUM((request_params->'_media_platform_cost_v1'->>'refunded_user_credits')::BIGINT),0),
        'estimated_provider_credits',COALESCE(SUM((request_params->'_media_platform_cost_v1'->>'estimated_provider_credits')::BIGINT),0),
        'evidence','unconfirmed','since',p_since,'until',p_until) INTO v_summary
    FROM public.tasks WHERE request_params ? '_media_platform_cost_v1' AND completed_at>=p_since AND completed_at<p_until;
    SELECT COALESCE(jsonb_agg(item ORDER BY completed_at DESC,id DESC),'[]'::JSONB) INTO v_items FROM (
        SELECT id,completed_at,jsonb_build_object('task_id',id,'org_id',org_id,'user_id',user_id,
            'completed_at',completed_at,'cost',request_params->'_media_platform_cost_v1') AS item
        FROM public.tasks WHERE request_params ? '_media_platform_cost_v1' AND completed_at>=p_since AND completed_at<p_until
        ORDER BY completed_at DESC,id DESC LIMIT p_page_size OFFSET (p_page-1)*p_page_size
    ) page;
    RETURN jsonb_build_object('summary',v_summary,'items',v_items,'page',p_page,'page_size',p_page_size);
END;
$$;

REVOKE ALL ON FUNCTION public.assert_chat_image_worker(UUID),
 public.mark_chat_image_dispatch(UUID,UUID,UUID),public.bind_chat_image_submission(UUID,UUID,TEXT,UUID),
 public.mark_chat_image_uncertain(UUID,UUID,INTEGER,UUID),public.record_chat_image_provider_result(UUID,JSONB,UUID),
 public.fail_chat_image_before_dispatch(UUID,TEXT,TEXT,UUID,UUID),
 public.publish_chat_image_result(UUID,JSONB,TEXT,TEXT,BOOLEAN,UUID),public.stop_queued_chat_image(UUID,UUID),
 public.ack_chat_image_delivery(UUID,UUID),public.scan_chat_image_work(UUID,TIMESTAMPTZ,UUID,INTEGER),
 public.chat_image_platform_cost_report(TIMESTAMPTZ,TIMESTAMPTZ,INTEGER,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.assert_chat_image_worker(UUID),
 public.mark_chat_image_dispatch(UUID,UUID,UUID),public.bind_chat_image_submission(UUID,UUID,TEXT,UUID),
 public.mark_chat_image_uncertain(UUID,UUID,INTEGER,UUID),public.record_chat_image_provider_result(UUID,JSONB,UUID),
 public.fail_chat_image_before_dispatch(UUID,TEXT,TEXT,UUID,UUID),
 public.publish_chat_image_result(UUID,JSONB,TEXT,TEXT,BOOLEAN,UUID),public.ack_chat_image_delivery(UUID,UUID),
 public.scan_chat_image_work(UUID,TIMESTAMPTZ,UUID,INTEGER) TO everydayai,everydayai_worker;
GRANT EXECUTE ON FUNCTION public.stop_queued_chat_image(UUID,UUID) TO everydayai;
GRANT EXECUTE ON FUNCTION public.chat_image_platform_cost_report(TIMESTAMPTZ,TIMESTAMPTZ,INTEGER,INTEGER) TO everydayai;
