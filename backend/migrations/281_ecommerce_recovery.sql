-- Additive recovery state. Existing stage outputs, leases and image workers stay authoritative.
ALTER TABLE public.ecom_image_plans ADD COLUMN root_plan_id UUID REFERENCES public.ecom_image_plans(id),
    ADD COLUMN recovery_state JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.ecom_image_plan_acceptances ADD COLUMN generation_run_id UUID;
CREATE UNIQUE INDEX ecom_acceptances_generation_item ON public.ecom_image_plan_acceptances
    (generation_run_id,plan_id,plan_revision,item_id) WHERE generation_run_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.bind_ecom_workflow(p_task_id UUID,p_execution_token UUID,p_binding JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; p public.ecom_image_plans%ROWTYPE; old JSONB;
BEGIN
    SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',TRUE) <> 'runtime'
       OR t.id IS NULL OR t.status <> 'running' OR t.execution_token IS DISTINCT FROM p_execution_token
       OR p_execution_token IS NULL OR t.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
       OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::uuid
       OR jsonb_typeof(p_binding) IS DISTINCT FROM 'object'
       OR p_binding->>'kind' IS DISTINCT FROM 'main_images' OR p_binding->>'version' IS DISTINCT FROM '1'
       OR p_binding->>'generation_run_id' IS NULL THEN
        RAISE EXCEPTION 'ECOM_WORKFLOW_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_binding->>'plan_id' IS NOT NULL THEN
        SELECT * INTO p FROM public.ecom_image_plans WHERE id=(p_binding->>'plan_id')::uuid;
        IF p.id IS NULL OR p.user_id IS DISTINCT FROM t.user_id OR p.org_id IS DISTINCT FROM t.org_id
           OR p.conversation_id IS DISTINCT FROM t.conversation_id THEN
            RAISE EXCEPTION 'ECOM_WORKFLOW_PLAN_DENIED' USING ERRCODE='42501';
        END IF;
    END IF;
    old=t.request_params->'_ecom_workflow';
    IF old->>'generation_run_id' IS NOT NULL AND old->>'generation_run_id' IS DISTINCT FROM p_binding->>'generation_run_id' THEN
        RAISE EXCEPTION 'ECOM_WORKFLOW_BINDING_CONFLICT' USING ERRCODE='22023';
    END IF;
    UPDATE public.tasks SET request_params=jsonb_set(COALESCE(request_params,'{}'::jsonb),'{_ecom_workflow}',p_binding)
        WHERE id=t.id;
    RETURN p_binding;
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
       OR t.id IS NULL OR t.status <> 'running' OR p_lease_token IS NULL OR p.lease_token IS DISTINCT FROM p_lease_token
       OR p.lease_expires_at IS NULL OR p.lease_expires_at < NOW() OR p.status <> 'planning' OR p.current_stage IS DISTINCT FROM p_stage
       OR p_stage IS NULL OR p_attempt_id IS NULL OR p_wall_seconds IS NULL OR p_wall_seconds NOT BETWEEN 1 AND 600 THEN
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
        state=jsonb_build_object('version',1,'window_task_id',t.id,'deadline',NOW()+make_interval(secs=>p_wall_seconds),
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

CREATE OR REPLACE FUNCTION public.finish_ecom_plan_attempt(
    p_plan_id UUID,p_lease_token UUID,p_stage INTEGER,p_attempt_id UUID,p_usage JSONB,
    p_outcome TEXT,p_output JSONB DEFAULT NULL,p_status TEXT DEFAULT 'planning',
    p_items JSONB DEFAULT NULL,p_reviews JSONB DEFAULT NULL,p_credits INTEGER DEFAULT 0
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; r public.ecom_image_plans%ROWTYPE;
        root_id UUID; a JSONB; final JSONB; fingerprint TEXT; saved JSONB;
BEGIN
    SELECT COALESCE(root_plan_id,id) INTO root_id FROM public.ecom_image_plans WHERE id=p_plan_id;
    SELECT * INTO r FROM public.ecom_image_plans WHERE id=root_id FOR UPDATE;
    SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',TRUE) <> 'runtime'
       OR p.id IS NULL OR r.id IS NULL OR r.user_id IS DISTINCT FROM p.user_id OR r.org_id IS DISTINCT FROM p.org_id
       OR r.conversation_id IS DISTINCT FROM p.conversation_id
       OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
       OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::uuid
       OR p_outcome IS NULL OR p_outcome NOT IN ('completed','validation_failed','rejected','uncertain') THEN
        RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    a=r.recovery_state->'attempts'->p_attempt_id::text;
    IF a IS NULL OR a->>'plan_id' IS DISTINCT FROM p.id::text OR (a->>'stage')::integer IS DISTINCT FROM p_stage THEN
        RAISE EXCEPTION 'ECOM_PLAN_ATTEMPT_CONFLICT' USING ERRCODE='22023';
    END IF;
    fingerprint=md5(jsonb_build_object('usage',p_usage,'outcome',p_outcome,'output',p_output,
        'status',p_status,'items',p_items,'reviews',p_reviews,'credits',p_credits)::text);
    IF a->>'outcome'<>'started' THEN
        IF a->>'fingerprint' IS DISTINCT FROM fingerprint THEN
            RAISE EXCEPTION 'ECOM_PLAN_ATTEMPT_CONFLICT' USING ERRCODE='22023';
        END IF;
        RETURN jsonb_build_object('outcome','replay','attempt_id',p_attempt_id);
    END IF;
    saved=public.save_ecom_image_plan_stage(p_plan_id,p_lease_token,p_stage,p_output,
        jsonb_build_object('attempt_id',p_attempt_id,'stage',p_stage,'status',p_outcome,'usage',p_usage),
        p_status,p_items,p_reviews,p_credits);
    final=a || jsonb_build_object('outcome',p_outcome,'fingerprint',fingerprint,'usage',p_usage,
        'user_credits',p_credits,'completed_at',NOW());
    UPDATE public.ecom_image_plans SET recovery_state=jsonb_set(recovery_state,
        ARRAY['attempts',p_attempt_id::text],final),updated_at=NOW() WHERE id=root_id;
    RETURN saved || jsonb_build_object('outcome','saved','attempt_id',p_attempt_id);
END $$;

CREATE OR REPLACE FUNCTION public.fail_ecom_plan(
    p_plan_id UUID,p_lease_token UUID,p_stage INTEGER,p_status TEXT,p_error JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE root_id UUID; saved JSONB;
BEGIN
    SELECT COALESCE(root_plan_id,id) INTO root_id FROM public.ecom_image_plans WHERE id=p_plan_id;
    PERFORM 1 FROM public.ecom_image_plans WHERE id=root_id FOR UPDATE;
    saved=public.save_ecom_image_plan_stage(p_plan_id,p_lease_token,p_stage,NULL,
        jsonb_build_object('stage',p_stage,'status',p_status,'error',p_error),p_status);
    UPDATE public.ecom_image_plans SET recovery_state=jsonb_set(recovery_state,'{last_error}',p_error)
        WHERE id=p_plan_id;
    RETURN saved;
END $$;

REVOKE ALL ON FUNCTION public.bind_ecom_workflow(UUID,UUID,JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.reserve_ecom_plan_attempt(UUID,UUID,UUID,INTEGER,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.finish_ecom_plan_attempt(UUID,UUID,INTEGER,UUID,JSONB,TEXT,JSONB,TEXT,JSONB,JSONB,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fail_ecom_plan(UUID,UUID,INTEGER,TEXT,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.bind_ecom_workflow(UUID,UUID,JSONB),
    public.reserve_ecom_plan_attempt(UUID,UUID,UUID,INTEGER,INTEGER),
    public.finish_ecom_plan_attempt(UUID,UUID,INTEGER,UUID,JSONB,TEXT,JSONB,TEXT,JSONB,JSONB,INTEGER),
    public.fail_ecom_plan(UUID,UUID,INTEGER,TEXT,JSONB) TO everydayai;

-- Full immutable-plan validation precedes both new acceptance and cross-turn receipt replay.
CREATE OR REPLACE FUNCTION public.accept_chat_ecom_plan_image(
    p_parent_task_id UUID, p_execution_token UUID, p_snapshot JSONB, p_org_id UUID,
    p_plan_id UUID, p_plan_revision INTEGER, p_item_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE prior public.ecom_image_plan_acceptances%ROWTYPE; result JSONB;
        plan public.ecom_image_plans%ROWTYPE; parent public.tasks%ROWTYPE; item JSONB;
        generation_id UUID; expected_refs JSONB; submitted_refs JSONB; expected_generation_refs JSONB; submitted_generation_refs JSONB;
BEGIN
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',TRUE) <> 'runtime'
       OR current_setting('app.actor_user_id',TRUE) IS NULL THEN
        RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO plan FROM public.ecom_image_plans WHERE id=p_plan_id FOR SHARE;
    SELECT * INTO parent FROM public.tasks WHERE id=p_parent_task_id;
    IF parent.status <> 'running' OR parent.execution_token IS DISTINCT FROM p_execution_token
       OR p_execution_token IS NULL OR parent.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       OR parent.org_id IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'ECOM_PLAN_PARENT_DENIED' USING ERRCODE='42501';
    END IF;
    generation_id=(parent.request_params->'_ecom_workflow'->>'generation_run_id')::uuid;
    IF parent.request_params->'_ecom_workflow'->>'kind'='main_images'
       AND parent.request_params->'_ecom_workflow'->>'plan_id' IS DISTINCT FROM p_plan_id::text THEN
        RAISE EXCEPTION 'ECOM_PLAN_WORKFLOW_MISMATCH' USING ERRCODE='42501';
    END IF;
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
    INSERT INTO public.ecom_image_plan_acceptances(parent_task_id,plan_id,plan_revision,item_id,request_hash,receipt,generation_run_id)
        VALUES(p_parent_task_id,p_plan_id,p_plan_revision,p_item_id,p_snapshot->>'prompt_sha256','{}'::jsonb,generation_id)
        ON CONFLICT DO NOTHING;
    SELECT * INTO prior FROM public.ecom_image_plan_acceptances WHERE ((generation_id IS NOT NULL AND generation_run_id=generation_id)
            OR (generation_id IS NULL AND parent_task_id=p_parent_task_id))
        AND plan_id=p_plan_id AND plan_revision=p_plan_revision AND item_id=p_item_id FOR UPDATE;
    IF prior.parent_task_id IS NULL THEN RAISE EXCEPTION 'ECOM_PLAN_ACCEPTANCE_CONFLICT' USING ERRCODE='22023'; END IF;
    IF prior.receipt <> '{}'::jsonb THEN
        IF prior.request_hash IS DISTINCT FROM p_snapshot->>'prompt_sha256' THEN
            RAISE EXCEPTION 'ECOM_PLAN_ACCEPTANCE_CONFLICT' USING ERRCODE='22023';
        END IF;
        RETURN prior.receipt || jsonb_build_object('outcome','replay');
    END IF;
    result := public.accept_chat_image_request(p_parent_task_id,p_execution_token,p_snapshot,p_org_id);
    UPDATE public.ecom_image_plan_acceptances SET receipt=result WHERE parent_task_id=prior.parent_task_id
        AND plan_id=p_plan_id AND plan_revision=p_plan_revision AND item_id=p_item_id;
    RETURN result;
END $$;
GRANT EXECUTE ON FUNCTION public.accept_chat_ecom_plan_image(UUID,UUID,JSONB,UUID,UUID,INTEGER,UUID) TO everydayai;
