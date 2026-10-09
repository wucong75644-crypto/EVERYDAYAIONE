-- Page orchestration reuses ecom_image_plans, tasks, media snapshots and credit ledger.
SET LOCAL ROLE everydayai;
ALTER TABLE public.detail_projects DROP CONSTRAINT detail_projects_content_type_check;
ALTER TABLE public.detail_projects ADD CONSTRAINT detail_projects_content_type_check CHECK(content_type IN ('default','main_image','detail_page'));
ALTER TABLE public.detail_projects DROP CONSTRAINT detail_projects_image_count_check;
ALTER TABLE public.detail_projects ADD CONSTRAINT detail_projects_image_count_check CHECK(
 (content_type='default' AND image_count=14)
 OR (content_type IN ('main_image','detail_page') AND image_count BETWEEN 1 AND 15));
ALTER TABLE public.detail_projects ALTER COLUMN content_type SET DEFAULT 'default';
ALTER TABLE public.detail_projects ALTER COLUMN image_count SET DEFAULT 14;
ALTER TABLE public.detail_projects ADD COLUMN prompt_model TEXT NOT NULL DEFAULT 'kimi-k3'
 CHECK(prompt_model IN ('kimi-k3','gemini-3.8-flash')), ADD COLUMN run_state JSONB NOT NULL DEFAULT '{}';
CREATE INDEX detail_projects_active_work ON public.detail_projects(updated_at,id)
 WHERE status IN ('analyzing','plan_ready','generating');
GRANT SELECT,UPDATE ON public.detail_projects TO everydayai_worker;
GRANT REFERENCES(id) ON public.detail_projects TO everydayai_owner;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.ecom_image_plans
 ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'chat' CHECK(source_kind IN ('chat','detail_project')),
 ADD COLUMN project_id UUID REFERENCES public.detail_projects(id), ADD COLUMN generation_run_id UUID,
 ALTER COLUMN conversation_id DROP NOT NULL, ALTER COLUMN parent_task_id DROP NOT NULL,
 ALTER COLUMN input_message_id DROP NOT NULL, ALTER COLUMN base_context_revision DROP NOT NULL;
ALTER TABLE public.ecom_image_plans ADD CONSTRAINT ecom_plan_source_check CHECK(
 (source_kind='chat' AND conversation_id IS NOT NULL AND parent_task_id IS NOT NULL AND input_message_id IS NOT NULL AND base_context_revision IS NOT NULL AND project_id IS NULL)
 OR (source_kind='detail_project' AND project_id IS NOT NULL AND generation_run_id IS NOT NULL AND conversation_id IS NULL AND parent_task_id IS NULL AND input_message_id IS NULL AND base_context_revision IS NULL));
CREATE UNIQUE INDEX ecom_page_run_kind ON public.ecom_image_plans(project_id,generation_run_id,invocation_key) WHERE source_kind='detail_project';
CREATE POLICY ecom_page_worker_read ON public.ecom_image_plans FOR SELECT TO everydayai,everydayai_worker
 USING(current_setting('app.access_kind',true)='worker' AND source_kind='detail_project'
 AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid);
GRANT SELECT ON public.ecom_image_plans TO everydayai_worker;


CREATE OR REPLACE FUNCTION public.claim_ecom_image_plan(
    p_plan_id UUID, p_parent_task_id UUID, p_lease_token UUID, p_lease_seconds INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; t public.tasks%ROWTYPE;
BEGIN
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind', TRUE) NOT IN ('runtime','runtime_admin')
       OR p_lease_token IS NULL OR p_lease_seconds NOT BETWEEN 10 AND 600 THEN
        RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
    SELECT * INTO t FROM public.tasks WHERE id=p_parent_task_id FOR UPDATE;
    IF p.id IS NULL OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
       OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::uuid
       OR (p.source_kind='chat' AND (t.id IS NULL OR p.parent_task_id IS DISTINCT FROM p_parent_task_id
          OR t.user_id IS DISTINCT FROM p.user_id OR t.org_id IS DISTINCT FROM p.org_id
          OR t.conversation_id IS DISTINCT FROM p.conversation_id OR t.status<>'running'))
       OR (p.source_kind='detail_project' AND (p_parent_task_id IS NOT NULL OR NOT EXISTS(
          SELECT 1 FROM public.detail_projects d WHERE d.id=p.project_id AND d.user_id=p.user_id
          AND d.org_id IS NOT DISTINCT FROM p.org_id AND d.run_state->>'run_id'=p.generation_run_id::text
          AND d.status IN ('analyzing','plan_ready','generating')))) THEN
        RAISE EXCEPTION 'ECOM_PLAN_PARENT_DENIED' USING ERRCODE='42501';
    END IF;
    IF p.status IN ('ready','needs_input','insufficient','cancelled') THEN
        RETURN jsonb_build_object('claimed',false,'status',p.status,'row_version',p.row_version);
    END IF;
    IF p.lease_expires_at > NOW() AND p.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('claimed',false,'status',p.status,'row_version',p.row_version);
    END IF;
    UPDATE public.ecom_image_plans SET status=CASE WHEN p.status='failed' THEN 'planning' ELSE p.status END,
        lease_token=p_lease_token,
        lease_expires_at=NOW()+make_interval(secs=>p_lease_seconds),updated_at=NOW(),row_version=row_version+1
        WHERE id=p_plan_id;
    RETURN jsonb_build_object('claimed',true,'status',CASE WHEN p.status='failed' THEN 'planning' ELSE p.status END,
        'current_stage',p.current_stage,
        'row_version',p.row_version+1,'stage_outputs',p.stage_outputs,'stage_attempts',p.stage_attempts);
END $$;

CREATE OR REPLACE FUNCTION public.reserve_ecom_plan_attempt(
    p_plan_id UUID,p_lease_token UUID,p_attempt_id UUID,p_stage INTEGER,p_wall_seconds INTEGER DEFAULT 600
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; r public.ecom_image_plans%ROWTYPE; t public.tasks%ROWTYPE;
        root_id UUID; state JSONB; attempts JSONB; n INTEGER; reset_window BOOLEAN;
BEGIN
    SELECT COALESCE(root_plan_id,id) INTO root_id FROM public.ecom_image_plans WHERE id=p_plan_id;
    SELECT * INTO r FROM public.ecom_image_plans WHERE id=root_id FOR UPDATE;
    SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
    SELECT * INTO t FROM public.tasks WHERE id=p.parent_task_id;
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',TRUE) <> 'runtime'
       OR p.id IS NULL OR r.id IS NULL OR r.user_id IS DISTINCT FROM p.user_id OR r.org_id IS DISTINCT FROM p.org_id
       OR r.conversation_id IS DISTINCT FROM p.conversation_id
       OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
       OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::uuid
       OR (p.source_kind='chat' AND (t.id IS NULL OR t.status <> 'running')) OR p_lease_token IS NULL OR p.lease_token IS DISTINCT FROM p_lease_token
       OR p.lease_expires_at IS NULL OR p.lease_expires_at < NOW() OR p.status <> 'planning' OR p.current_stage IS DISTINCT FROM p_stage
       OR p_stage IS NULL OR p_attempt_id IS NULL OR p_wall_seconds IS NULL OR p_wall_seconds NOT BETWEEN 1 AND (CASE WHEN p.source_kind='detail_project' THEN 1800 ELSE 600 END) THEN
        RAISE EXCEPTION 'ECOM_PLAN_LEASE_LOST' USING ERRCODE='55000';
    END IF;
    state=r.recovery_state;
    reset_window=COALESCE((t.request_params->'_ecom_workflow'->>'reset_window')::boolean,false)
        AND t.request_params->'_ecom_workflow'->>'window_task_id'=t.id::text
        AND state->>'window_task_id' IS DISTINCT FROM t.id::text;
    IF state->>'window_task_id' IS NULL AND jsonb_array_length(r.stage_attempts)>0 AND NOT reset_window THEN
        RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000';
    END IF;
    IF state->>'window_task_id' IS NULL OR reset_window THEN
        state=jsonb_build_object('version',1,'window_task_id',COALESCE(t.id,p.generation_run_id),'deadline',NOW()+make_interval(secs=>p_wall_seconds),
            'counts','{}'::jsonb,'attempts','{}'::jsonb);
    END IF;
    attempts=COALESCE(state->'attempts','{}'::jsonb);
    IF attempts ? p_attempt_id::text THEN
        RETURN jsonb_build_object('outcome','replay','attempt',attempts->p_attempt_id::text);
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_each(attempts) x WHERE x.value->>'outcome' IN ('started','uncertain')) THEN
        RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000';
    END IF;
    IF (state->>'deadline')::timestamptz <= NOW() THEN
        RAISE EXCEPTION 'ECOM_PLAN_PARENT_BUDGET_EXHAUSTED' USING ERRCODE='55000';
    END IF;
    n=COALESCE((state->'counts'->>p_stage::text)::integer,0);
    IF n>=3 THEN RAISE EXCEPTION 'ECOM_PLAN_RETRY_EXHAUSTED' USING ERRCODE='55000'; END IF;
    state=jsonb_set(state,ARRAY['counts',p_stage::text],to_jsonb(n+1));
    state=jsonb_set(state,ARRAY['attempts',p_attempt_id::text],jsonb_build_object(
        'stage',p_stage,'plan_id',p.id,'outcome','started','started_at',NOW(),'ordinal',n+1));
    UPDATE public.ecom_image_plans SET recovery_state=state,updated_at=NOW() WHERE id=root_id;
    RETURN jsonb_build_object('outcome','execute','attempt_id',p_attempt_id,'ordinal',n+1,
        'remaining_attempts',2-n,'deadline',state->>'deadline');
END $$;

CREATE OR REPLACE FUNCTION public.save_ecom_image_plan_stage(
    p_plan_id UUID, p_lease_token UUID, p_stage INTEGER, p_output JSONB,
    p_attempt JSONB, p_status TEXT, p_items JSONB DEFAULT NULL, p_reviews JSONB DEFAULT NULL,
    p_credits INTEGER DEFAULT 0
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; balance INTEGER;
BEGIN
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind', TRUE) NOT IN ('runtime','runtime_admin') THEN
        RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_stage NOT BETWEEN 1 AND 3 OR p_status NOT IN ('planning','needs_input','insufficient','ready','failed','cancelled') THEN
        RAISE EXCEPTION 'ECOM_PLAN_STAGE_OR_STATUS_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
    IF NOT FOUND OR p.lease_token IS DISTINCT FROM p_lease_token OR p.lease_expires_at IS NULL OR p.lease_expires_at < NOW()
       OR p.current_stage IS DISTINCT FROM p_stage OR p.status <> 'planning'
       OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid THEN
        RAISE EXCEPTION 'ECOM_PLAN_LEASE_LOST' USING ERRCODE='55000';
    END IF;
    IF p_attempt IS NOT NULL AND p_output IS NULL THEN
        IF p_credits < 0 OR p_credits > 100000 THEN
            RAISE EXCEPTION 'ECOM_PLAN_CREDITS_INVALID' USING ERRCODE='22023';
        END IF;
        IF p_credits > 0 THEN
            UPDATE public.users SET credits=credits-p_credits,updated_at=NOW()
                WHERE id=p.user_id AND credits >= p_credits RETURNING credits INTO balance;
            IF NOT FOUND THEN RAISE EXCEPTION 'ECOM_PLAN_INSUFFICIENT_CREDITS' USING ERRCODE='P0001'; END IF;
            INSERT INTO public.credits_history(user_id,change_type,change_amount,balance_after,description,org_id)
                VALUES(p.user_id,'conversation_cost'::public.credits_change_type,-p_credits,balance,
                    COALESCE(p.model_settings->>'model','ecommerce')||' planning stage '||p_stage,p.org_id);
        END IF;
        UPDATE public.ecom_image_plans SET stage_attempts=stage_attempts || jsonb_build_array(p_attempt),
            status=CASE WHEN p_status IN ('failed','cancelled') THEN p_status ELSE status END,
            lease_token=CASE WHEN p_status IN ('failed','cancelled') THEN NULL ELSE lease_token END,
            lease_expires_at=CASE WHEN p_status IN ('failed','cancelled') THEN NULL ELSE lease_expires_at END,
            updated_at=NOW(),row_version=row_version+1 WHERE id=p_plan_id;
        RETURN jsonb_build_object('row_version',p.row_version+1);
    END IF;
    IF p_output IS NOT NULL THEN
        IF p_credits < 0 OR p_credits > 100000 THEN
            RAISE EXCEPTION 'ECOM_PLAN_CREDITS_INVALID' USING ERRCODE='22023';
        END IF;
        IF p_credits > 0 THEN
            UPDATE public.users SET credits=credits-p_credits,updated_at=NOW()
                WHERE id=p.user_id AND credits >= p_credits RETURNING credits INTO balance;
            IF NOT FOUND THEN RAISE EXCEPTION 'ECOM_PLAN_INSUFFICIENT_CREDITS' USING ERRCODE='P0001'; END IF;
            INSERT INTO public.credits_history(user_id,change_type,change_amount,balance_after,description,org_id)
                VALUES(p.user_id,'conversation_cost'::public.credits_change_type,-p_credits,balance,
                    COALESCE(p.model_settings->>'model','ecommerce')||' planning stage '||p_stage,p.org_id);
        END IF;
        UPDATE public.ecom_image_plans SET
            stage_outputs=stage_outputs || jsonb_build_object(p_stage::text,p_output),
            stage_attempts=CASE WHEN p_attempt IS NULL THEN stage_attempts
                ELSE stage_attempts || jsonb_build_array(p_attempt) END,
            current_stage=CASE WHEN p_status='planning' THEN LEAST(3,p_stage+1) ELSE p_stage END,
            status=p_status,
            items=COALESCE(p_items,items), review_records=COALESCE(p_reviews,review_records),
            lease_token=CASE WHEN p_status='planning' THEN p_lease_token ELSE NULL END,
            lease_expires_at=CASE WHEN p_status='planning' THEN NOW()+INTERVAL '600 seconds' ELSE NULL END,
            updated_at=NOW(),row_version=row_version+1 WHERE id=p_plan_id;
    ELSE
        UPDATE public.ecom_image_plans SET status=p_status,lease_token=NULL,lease_expires_at=NULL,
            updated_at=NOW(),row_version=row_version+1 WHERE id=p_plan_id;
    END IF;
    RETURN jsonb_build_object('row_version',p.row_version+1);
END $$;

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
        IF EXISTS(SELECT 1 FROM public.tasks t WHERE t.id=p_task_id AND t.request_params->'_media_request_v1'->'origin'->>'destination'='detail_project') THEN
            PERFORM 1 FROM public.detail_projects d JOIN public.tasks t ON t.id=p_task_id
             WHERE d.id=(t.request_params->'_media_request_v1'->'origin'->>'project_id')::uuid
             AND d.user_id=t.user_id AND d.org_id IS NOT DISTINCT FROM p_org_id
             AND d.run_state->>'run_id'=t.request_params->'_media_request_v1'->'origin'->>'generation_run_id' FOR UPDATE OF d;
        ELSE
        PERFORM 1 FROM public.skill_draft_trial_runs r JOIN public.tasks t ON t.id=p_task_id
          WHERE t.request_params->'_media_request_v1'->'origin'->>'destination'='skill_trial'
            AND r.id=(t.request_params->'_media_request_v1'->'origin'->>'trial_id')::UUID
            AND r.actor_user_id=t.user_id AND r.org_id=p_org_id AND r.status='running'
            AND r.result->>'image_task_id'=p_task_id::TEXT FOR UPDATE OF r;
        IF NOT FOUND THEN RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE = '42501'; END IF;
        END IF;
        IF NOT FOUND THEN RAISE EXCEPTION 'CHAT_IMAGE_TASK_DENIED' USING ERRCODE='42501'; END IF;
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

CREATE OR REPLACE FUNCTION public.start_detail_page_run(p_project_id UUID,p_version INTEGER,p_request_id UUID,p_plans JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE d public.detail_projects%ROWTYPE; actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org UUID:=NULLIF(current_setting('app.org_id',true),'')::uuid; entry JSONB;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true)<>'runtime' OR actor IS NULL OR p_request_id IS NULL THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM org FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'DETAIL_PROJECT_DENIED' USING ERRCODE='42501'; END IF;
 IF d.run_state->>'request_id'=p_request_id::text THEN RETURN to_jsonb(d); END IF;
 IF d.status<>'draft' OR d.version<>p_version THEN RAISE EXCEPTION 'DETAIL_PROJECT_VERSION_CONFLICT' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM public.detail_projects WHERE user_id=actor AND org_id IS NOT DISTINCT FROM org AND status IN ('analyzing','plan_ready','generating')) THEN
  RAISE EXCEPTION 'DETAIL_RUN_ACTIVE' USING ERRCODE='55000'; END IF;
 IF jsonb_typeof(p_plans)<>'array' OR jsonb_array_length(p_plans)<>(CASE WHEN d.content_type='default' THEN 2 ELSE 1 END) THEN
  RAISE EXCEPTION 'DETAIL_PLAN_INVALID' USING ERRCODE='22023'; END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(p_plans) LOOP
  IF entry->>'project_id' IS DISTINCT FROM d.id::text OR entry->>'user_id' IS DISTINCT FROM actor::text
   OR entry->>'org_id' IS DISTINCT FROM org::text OR entry->>'generation_run_id' IS DISTINCT FROM p_request_id::text
   OR (entry->>'image_count')::int<>(CASE WHEN d.content_type='default' THEN 7 ELSE d.image_count END)
   OR entry->'model_settings'->>'model' IS DISTINCT FROM d.prompt_model THEN
   RAISE EXCEPTION 'DETAIL_PLAN_INVALID' USING ERRCODE='22023'; END IF;
  INSERT INTO public.ecom_image_plans(id,user_id,org_id,source_kind,project_id,generation_run_id,invocation_key,input_digest,
    status,current_stage,image_count,input_snapshot,prompt_versions,model_settings,target_size)
   VALUES((entry->>'id')::uuid,actor,org,'detail_project',d.id,p_request_id,entry->>'invocation_key',entry->>'input_digest',
    'planning',1,(entry->>'image_count')::int,entry->'input_snapshot',entry->'prompt_versions',entry->'model_settings',entry->'target_size');
 END LOOP;
 UPDATE public.detail_projects SET status='analyzing',version=version+1,updated_at=NOW(),
  run_state=jsonb_build_object('run_id',p_request_id,'request_id',p_request_id,'started_at',NOW()) WHERE id=d.id RETURNING * INTO d;
 RETURN to_jsonb(d);
END $$;
REVOKE ALL ON FUNCTION public.start_detail_page_run(UUID,INTEGER,UUID,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.start_detail_page_run(UUID,INTEGER,UUID,JSONB) TO everydayai;

CREATE OR REPLACE FUNCTION public.accept_detail_page_image(p_project_id UUID,p_plan_id UUID,p_item_id UUID,p_snapshot JSONB,p_org_id UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE d public.detail_projects%ROWTYPE; plan public.ecom_image_plans%ROWTYPE; item JSONB; origin JSONB:=p_snapshot->'origin';
 actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; prior public.tasks%ROWTYPE;
 source public.tasks%ROWTYPE; task_id UUID:=gen_random_uuid(); cost INTEGER:=(p_snapshot->>'estimated_credits')::int;
 n INTEGER; reserved INTEGER;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true)<>'runtime' OR actor IS NULL
 OR NULLIF(current_setting('app.org_id',true),'')::uuid IS DISTINCT FROM p_org_id THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 SELECT * INTO plan FROM public.ecom_image_plans WHERE id=p_plan_id AND project_id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id;
 SELECT value INTO item FROM jsonb_array_elements(plan.items) WHERE value->>'item_id'=p_item_id::text;
 IF d.id IS NULL OR plan.id IS NULL OR plan.status<>'ready' OR item IS NULL
 OR d.run_state->>'run_id' IS DISTINCT FROM plan.generation_run_id::text
 OR origin->>'generation_run_id' IS DISTINCT FROM plan.generation_run_id::text
 OR origin->>'project_id' IS DISTINCT FROM d.id::text OR origin->>'destination' IS DISTINCT FROM 'detail_project'
 OR origin->>'actor_user_id' IS DISTINCT FROM actor::text OR origin->>'workspace_owner_id' IS DISTINCT FROM actor::text
 OR origin->>'org_id' IS DISTINCT FROM p_org_id::text OR origin->>'context_scope' IS DISTINCT FROM 'user'
 OR origin ?| ARRAY['conversation_id','parent_task_id','input_message_id','base_context_revision','trial_id']
 OR origin->'plan_source'->>'plan_id' IS DISTINCT FROM plan.id::text
 OR origin->'plan_source'->>'item_id' IS DISTINCT FROM p_item_id::text
 OR origin->'plan_source'->>'revision' IS DISTINCT FROM plan.plan_revision::text
 OR origin->'plan_source'->>'request_text_sha256' IS DISTINCT FROM item->>'request_text_sha256'
 OR p_snapshot->>'schema_version' IS DISTINCT FROM '1' OR p_snapshot->>'num_images' IS DISTINCT FROM '1'
 OR p_snapshot->>'prompt' IS DISTINCT FROM item->>'request_text'
 OR p_snapshot->>'prompt_sha256' IS DISTINCT FROM item->>'request_text_sha256'
 OR p_snapshot->'references' IS DISTINCT FROM plan.input_snapshot->'resolved_references'
 OR p_snapshot->>'aspect_ratio' IS DISTINCT FROM item->>'aspect_ratio'
 OR p_snapshot->>'resolution' IS DISTINCT FROM plan.target_size->>'resolution'
 OR p_snapshot->>'request_hash' !~ '^[0-9a-f]{64}$' OR cost IS NULL OR cost NOT BETWEEN 1 AND 300
 OR pg_column_size(p_snapshot)>131072 THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_SOURCE_DENIED' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=actor AND status='active')
 OR (p_org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
 WHERE m.user_id=actor AND m.org_id=p_org_id AND m.status='active' AND o.status='active')) THEN
  RAISE EXCEPTION 'DETAIL_IDENTITY_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM public.tasks WHERE type='image' AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id
 AND request_params->'_media_request_v1'->'origin'->>'project_id'=d.id::text
 AND request_params->'_media_request_v1'->'origin'->'plan_source'->>'item_id'=p_item_id::text
 AND COALESCE(request_params->'_media_request_v1'->'origin'->>'retry_request_id','initial')=COALESCE(origin->>'retry_request_id','initial');
 IF FOUND THEN RETURN jsonb_build_object('outcome','replay','task_id',prior.id,'message_id',NULL,'submission_state',prior.request_params->'_media_lifecycle_v1'->>'phase'); END IF;
 IF origin ? 'retry_request_id' THEN
  SELECT * INTO source FROM public.tasks WHERE id=(origin->>'retry_of_task_id')::uuid AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id;
  IF source.id IS NULL OR source.request_params->'_media_lifecycle_v1'->>'phase'<>'published'
  OR source.request_params->'_media_request_v1'->'origin'->>'project_id' IS DISTINCT FROM d.id::text
  OR source.request_params->'_media_request_v1'->>'prompt' IS DISTINCT FROM p_snapshot->>'prompt'
  OR source.request_params->'_media_request_v1'->'references' IS DISTINCT FROM p_snapshot->'references'
  OR source.request_params->'_media_request_v1'->>'model' IS DISTINCT FROM p_snapshot->>'model'
  OR source.request_params->'_media_request_v1'->>'estimated_credits' IS DISTINCT FROM p_snapshot->>'estimated_credits' THEN
   RAISE EXCEPTION 'DETAIL_REPLAY_DENIED' USING ERRCODE='42501'; END IF;
 ELSE
  SELECT COUNT(*),COALESCE(SUM((request_params->'_media_request_v1'->>'estimated_credits')::int),0) INTO n,reserved
   FROM public.tasks WHERE type='image' AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id
   AND request_params->'_media_request_v1'->'origin'->>'project_id'=d.id::text
   AND NOT(request_params->'_media_request_v1'->'origin' ? 'retry_request_id');
  IF n>=COALESCE((plan.model_settings->'image_budget'->>'max_requests')::int,15) OR reserved+cost>COALESCE((plan.model_settings->'image_budget'->>'max_credits')::int,300) THEN RAISE EXCEPTION 'DETAIL_IMAGE_BUDGET_EXCEEDED' USING ERRCODE='54000'; END IF;
 END IF;
 INSERT INTO public.tasks(id,user_id,org_id,type,status,model_id,batch_id,image_index,request_params)
 VALUES(task_id,actor,p_org_id,'image','pending',p_snapshot->>'model',plan.generation_run_id::text,(item->>'position')::int-1,
 jsonb_build_object('_media_request_v1',p_snapshot,'_media_lifecycle_v1',jsonb_build_object('phase','queued','attempt',0),
 'prompt',p_snapshot->>'prompt','model',p_snapshot->>'model','num_images',1));
 UPDATE public.detail_projects SET status='generating',updated_at=NOW() WHERE id=d.id;
 RETURN jsonb_build_object('outcome','accepted','task_id',task_id,'message_id',NULL,'submission_state','queued');
END $$;
REVOKE ALL ON FUNCTION public.accept_detail_page_image(UUID,UUID,UUID,JSONB,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.accept_detail_page_image(UUID,UUID,UUID,JSONB,UUID) TO everydayai;

CREATE OR REPLACE FUNCTION public.scan_detail_page_work(p_org_id UUID DEFAULT NULL,p_limit INTEGER DEFAULT 20)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE rows JSONB;
BEGIN
 PERFORM public.assert_chat_image_worker(p_org_id);
 IF p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'DETAIL_SCAN_INVALID'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(d)),'[]') INTO rows FROM(
 SELECT * FROM public.detail_projects WHERE org_id IS NOT DISTINCT FROM p_org_id AND status IN ('analyzing','plan_ready','generating')
 ORDER BY updated_at,id LIMIT p_limit) d;
 RETURN rows;
END $$;
REVOKE ALL ON FUNCTION public.scan_detail_page_work(UUID,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.scan_detail_page_work(UUID,INTEGER) TO everydayai,everydayai_worker;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.accept_detail_page_group(p_project_id UUID,p_plan_id UUID,p_snapshots JSONB,p_org_id UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE entry JSONB; receipts JSONB:='[]';
BEGIN
 IF jsonb_typeof(p_snapshots)<>'array' OR jsonb_array_length(p_snapshots) NOT BETWEEN 1 AND 15 THEN RAISE EXCEPTION 'DETAIL_GROUP_INVALID'; END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(p_snapshots) LOOP
  receipts=receipts||jsonb_build_array(public.accept_detail_page_image(p_project_id,p_plan_id,(entry->>'item_id')::uuid,entry->'snapshot',p_org_id));
 END LOOP;
 RETURN receipts;
END $$;
REVOKE ALL ON FUNCTION public.accept_detail_page_group(UUID,UUID,JSONB,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.accept_detail_page_group(UUID,UUID,JSONB,UUID) TO everydayai;
RESET ROLE;

SET LOCAL ROLE everydayai_owner;
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
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.resume_detail_page_plan(p_project_id UUID,p_plan_id UUID,p_request_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; d public.detail_projects%ROWTYPE; state JSONB;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true)<>'runtime' OR p_request_id IS NULL THEN RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid FOR UPDATE;
 SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id AND project_id=p_project_id FOR UPDATE;
 IF d.id IS NULL OR p.id IS NULL OR p.source_kind<>'detail_project' OR p.user_id<>d.user_id
  OR p.org_id IS DISTINCT FROM d.org_id OR d.status='archived' OR d.run_state->>'run_id' IS DISTINCT FROM p.generation_run_id::text THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 IF p.recovery_state->>'resume_request_id'=p_request_id::text THEN RETURN to_jsonb(p); END IF;
 IF p.status='ready' THEN
  UPDATE public.ecom_image_plans SET recovery_state=recovery_state-'acceptance_error',updated_at=NOW() WHERE id=p.id;
 ELSIF p.status='failed' AND (p.lease_expires_at IS NULL OR p.lease_expires_at<=NOW()) THEN
  IF EXISTS(SELECT 1 FROM jsonb_each(COALESCE(p.recovery_state->'attempts','{}')) a WHERE a.value->>'outcome' IN ('started','uncertain')) THEN
   RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000'; END IF;
  state=jsonb_build_object('version',1,'window_task_id',p_request_id,'deadline',NOW()+make_interval(secs=>LEAST(1800,GREATEST(600,(p.model_settings->'profile'->>'wall_seconds')::int))),
    'counts','{}'::jsonb,'attempts','{}'::jsonb,'resume_request_id',p_request_id,
    'previous_windows',COALESCE(p.recovery_state->'previous_windows','[]')||jsonb_build_array(p.recovery_state-'previous_windows'));
  UPDATE public.ecom_image_plans SET status='planning',lease_token=NULL,lease_expires_at=NULL,recovery_state=state,updated_at=NOW() WHERE id=p.id;
 ELSE RAISE EXCEPTION 'DETAIL_PLAN_NOT_RETRYABLE' USING ERRCODE='55000'; END IF;
 UPDATE public.detail_projects SET status='analyzing',updated_at=NOW() WHERE id=d.id;
 RETURN jsonb_build_object('status','resumed');
END $$;
REVOKE ALL ON FUNCTION public.resume_detail_page_plan(UUID,UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.resume_detail_page_plan(UUID,UUID,UUID) TO everydayai;
RESET ROLE;
