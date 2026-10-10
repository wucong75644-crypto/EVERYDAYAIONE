-- The least-privilege schema owner does not retain REFERENCES on application
-- identity tables. Grant it only for this transaction's foreign-key creation,
-- then revoke before committing the new tables.
RESET ROLE;
SET LOCAL ROLE everydayai;
GRANT REFERENCES (id) ON TABLE public.users, public.organizations,
    public.conversations, public.tasks, public.messages TO everydayai_owner;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;

CREATE TABLE public.ecom_image_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES public.organizations(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE RESTRICT,
    parent_task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE RESTRICT,
    input_message_id UUID NOT NULL REFERENCES public.messages(id) ON DELETE RESTRICT,
    base_context_revision BIGINT NOT NULL,
    invocation_key TEXT NOT NULL,
    plan_revision INTEGER NOT NULL DEFAULT 1 CHECK (plan_revision > 0),
    supersedes_plan_id UUID REFERENCES public.ecom_image_plans(id) ON DELETE RESTRICT,
    input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('planning','needs_input','insufficient','ready','failed','cancelled')),
    current_stage SMALLINT NOT NULL CHECK (current_stage BETWEEN 1 AND 3),
    image_count SMALLINT NOT NULL CHECK (image_count BETWEEN 1 AND 15),
    input_snapshot JSONB NOT NULL,
    prompt_versions JSONB NOT NULL,
    model_settings JSONB NOT NULL,
    stage_outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
    stage_attempts JSONB NOT NULL DEFAULT '[]'::jsonb,
    items JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_records JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_size JSONB NOT NULL,
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    row_version BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parent_task_id, invocation_key)
);

CREATE INDEX ecom_image_plans_owner_recent ON public.ecom_image_plans
    (user_id, conversation_id, created_at DESC);
ALTER TABLE public.ecom_image_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ecom_image_plans FORCE ROW LEVEL SECURITY;
CREATE POLICY ecom_image_plans_actor ON public.ecom_image_plans FOR ALL TO everydayai
    USING (user_id = NULLIF(current_setting('app.actor_user_id', true), '')::uuid
       AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.actor_user_id', true), '')::uuid
       AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE ON public.ecom_image_plans TO everydayai;

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
    IF p.id IS NULL OR t.id IS NULL OR p.parent_task_id IS DISTINCT FROM p_parent_task_id
       OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
       OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::uuid
       OR t.user_id IS DISTINCT FROM p.user_id OR t.org_id IS DISTINCT FROM p.org_id
       OR t.conversation_id IS DISTINCT FROM p.conversation_id OR t.status <> 'running' THEN
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
GRANT EXECUTE ON FUNCTION public.claim_ecom_image_plan(UUID,UUID,UUID,INTEGER) TO everydayai;

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
                    'KIE GPT 5.6 Luna ecommerce planning stage '||p_stage,p.org_id);
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
                    'KIE GPT 5.6 Luna ecommerce planning stage '||p_stage,p.org_id);
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
GRANT EXECUTE ON FUNCTION public.save_ecom_image_plan_stage(UUID,UUID,INTEGER,JSONB,JSONB,TEXT,JSONB,JSONB,INTEGER) TO everydayai;

CREATE TABLE public.ecom_image_plan_acceptances (
    parent_task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE RESTRICT,
    plan_id UUID NOT NULL REFERENCES public.ecom_image_plans(id) ON DELETE RESTRICT,
    plan_revision INTEGER NOT NULL DEFAULT 1,
    item_id UUID NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    receipt JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(parent_task_id,plan_id,plan_revision,item_id)
);

RESET ROLE;
SET LOCAL ROLE everydayai;
REVOKE REFERENCES (id) ON TABLE public.users, public.organizations,
    public.conversations, public.tasks, public.messages FROM everydayai_owner;
RESET ROLE;
SET LOCAL ROLE everydayai_owner;

ALTER TABLE public.ecom_image_plan_acceptances ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ecom_image_plan_acceptances FORCE ROW LEVEL SECURITY;
CREATE POLICY ecom_image_plan_acceptances_actor ON public.ecom_image_plan_acceptances FOR ALL TO everydayai
    USING (EXISTS (SELECT 1 FROM public.tasks t WHERE t.id=parent_task_id
       AND t.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       AND t.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid))
    WITH CHECK (EXISTS (SELECT 1 FROM public.tasks t WHERE t.id=parent_task_id
       AND t.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       AND t.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid));
GRANT SELECT, INSERT, UPDATE ON public.ecom_image_plan_acceptances TO everydayai;

CREATE OR REPLACE FUNCTION public.accept_chat_ecom_plan_image(
    p_parent_task_id UUID, p_execution_token UUID, p_snapshot JSONB, p_org_id UUID,
    p_plan_id UUID, p_plan_revision INTEGER, p_item_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE prior public.ecom_image_plan_acceptances%ROWTYPE; result JSONB;
        plan public.ecom_image_plans%ROWTYPE; parent public.tasks%ROWTYPE; item JSONB;
        expected_refs JSONB; submitted_refs JSONB; expected_generation_refs JSONB; submitted_generation_refs JSONB;
BEGIN
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',TRUE) <> 'runtime'
       OR current_setting('app.actor_user_id',TRUE) IS NULL THEN
        RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO plan FROM public.ecom_image_plans WHERE id=p_plan_id FOR SHARE;
    SELECT * INTO parent FROM public.tasks WHERE id=p_parent_task_id;
    SELECT entry.value INTO item FROM jsonb_array_elements(COALESCE(plan.items,'[]'::jsonb)) AS entry(value)
        WHERE entry.value->>'item_id'=p_item_id::text LIMIT 1;
    SELECT COALESCE(jsonb_agg(entry.value - 'source_id' ORDER BY entry.ordinality),'[]'::jsonb)
        INTO expected_refs FROM jsonb_array_elements(COALESCE(item->'references','[]'::jsonb))
            WITH ORDINALITY AS entry(value,ordinality);
    SELECT COALESCE(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
            'resource_ref',entry.value->'resource_ref','file_id',entry.value->'file_id',
            'asset_id',entry.value->'asset_id','message_id',entry.value->'message_id',
            'content_index',entry.value->'content_index','role',entry.value->'role'))
            ORDER BY entry.ordinality),'[]'::jsonb)
        INTO submitted_refs FROM jsonb_array_elements(COALESCE(p_snapshot->'references','[]'::jsonb))
            WITH ORDINALITY AS entry(value,ordinality);
    SELECT COALESCE(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
            'resource_ref',selected.value->'resource_ref','file_id',selected.value->'file_id',
            'asset_id',selected.value->'asset_id','message_id',selected.value->'message_id',
            'content_index',selected.value->'content_index','role',selected.value->'role',
            'source_id',selected.value->'source_id','content_sha256',facts.value->'content_sha256',
            'file_version',facts.value->'file_version','workspace_path',facts.value->'workspace_path'))
            ORDER BY selected.ordinality),'[]'::jsonb)
        INTO expected_generation_refs
        FROM jsonb_array_elements(COALESCE(item->'references','[]'::jsonb))
            WITH ORDINALITY AS selected(value,ordinality)
        LEFT JOIN LATERAL (
            SELECT resolved.value FROM jsonb_array_elements(
                COALESCE(plan.input_snapshot->'resolved_references','[]'::jsonb)) AS resolved(value)
            WHERE resolved.value->>'source_id'=selected.value->>'source_id' LIMIT 1
        ) AS facts ON TRUE;
    SELECT COALESCE(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
            'resource_ref',entry.value->'resource_ref','file_id',entry.value->'file_id',
            'asset_id',entry.value->'asset_id','message_id',entry.value->'message_id',
            'content_index',entry.value->'content_index','role',entry.value->'role',
            'source_id',entry.value->'source_id','content_sha256',entry.value->'content_sha256',
            'file_version',entry.value->'file_version','workspace_path',entry.value->'workspace_path'))
            ORDER BY entry.ordinality),'[]'::jsonb)
        INTO submitted_generation_refs FROM jsonb_array_elements(COALESCE(p_snapshot->'references','[]'::jsonb))
            WITH ORDINALITY AS entry(value,ordinality);
    IF plan.id IS NULL OR parent.id IS NULL OR plan.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       OR plan.org_id IS DISTINCT FROM p_org_id OR plan.conversation_id IS DISTINCT FROM parent.conversation_id
       OR plan.status <> 'ready' OR plan.plan_revision IS DISTINCT FROM p_plan_revision OR item IS NULL
       OR item->>'request_text' IS DISTINCT FROM p_snapshot->>'prompt'
       OR item->>'request_text_sha256' IS DISTINCT FROM p_snapshot->>'prompt_sha256'
       OR item->>'aspect_ratio' IS DISTINCT FROM plan.target_size->>'aspect_ratio'
       OR p_snapshot->>'aspect_ratio' IS DISTINCT FROM item->>'aspect_ratio'
       OR p_snapshot->>'resolution' IS DISTINCT FROM plan.target_size->>'resolution'
       OR expected_refs IS DISTINCT FROM submitted_refs
       OR expected_generation_refs IS DISTINCT FROM submitted_generation_refs THEN
        RAISE EXCEPTION 'ECOM_PLAN_SOURCE_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_snapshot->'origin'->'plan_source'->>'plan_id' IS DISTINCT FROM p_plan_id::text
       OR (p_snapshot->'origin'->'plan_source'->>'revision')::integer IS DISTINCT FROM p_plan_revision
       OR p_snapshot->'origin'->'plan_source'->>'item_id' IS DISTINCT FROM p_item_id::text
       OR p_snapshot->'origin'->'plan_source'->>'request_text_sha256' IS DISTINCT FROM item->>'request_text_sha256' THEN
        RAISE EXCEPTION 'ECOM_PLAN_SOURCE_DENIED' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.ecom_image_plan_acceptances(parent_task_id,plan_id,plan_revision,item_id,request_hash,receipt)
        VALUES(p_parent_task_id,p_plan_id,p_plan_revision,p_item_id,p_snapshot->>'prompt_sha256','{}'::jsonb)
        ON CONFLICT DO NOTHING;
    SELECT * INTO prior FROM public.ecom_image_plan_acceptances WHERE parent_task_id=p_parent_task_id
        AND plan_id=p_plan_id AND plan_revision=p_plan_revision AND item_id=p_item_id FOR UPDATE;
    IF prior.receipt <> '{}'::jsonb THEN
        IF prior.request_hash IS DISTINCT FROM p_snapshot->>'prompt_sha256' THEN
            RAISE EXCEPTION 'ECOM_PLAN_ACCEPTANCE_CONFLICT' USING ERRCODE='22023';
        END IF;
        RETURN jsonb_build_object('outcome','replay') || prior.receipt;
    END IF;
    result := public.accept_chat_image_request(p_parent_task_id,p_execution_token,p_snapshot,p_org_id);
    UPDATE public.ecom_image_plan_acceptances SET receipt=result WHERE parent_task_id=p_parent_task_id
        AND plan_id=p_plan_id AND plan_revision=p_plan_revision AND item_id=p_item_id;
    RETURN result;
END $$;
GRANT EXECUTE ON FUNCTION public.accept_chat_ecom_plan_image(UUID,UUID,JSONB,UUID,UUID,INTEGER,UUID) TO everydayai;

-- Rollback is intentionally separate and only safe after planners and plan-backed
-- image workers are drained. The task release script never invokes this file.
